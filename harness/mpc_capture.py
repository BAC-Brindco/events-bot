"""Phase 1a: capture-only harness for an RBI MPC decision day.

Polls every public RBI channel that could carry the MPC outcome. For each
item it records the first-seen time, archives the raw bytes and stores the
sha256. It sends nothing; the output is evidence for Phase 2 (lag per
channel, re-uploads, golden fixtures).

Channels
  rss_pr      pressreleases_rss.xml            every 20 s (conditional GET)
  rss_notif   notifications_rss.xml            every 60 s
  prid_probe  BS_PressReleaseDisplay?prid=N    next PROBE_AHEAD unpublished ids, each about every 30 s
  annual      Annualpolicy.aspx (MPC index)    every 60 s, new links only
  youtube     RBI channel videos.xml           every 60 s (CDN-cached up to 15 min)

For each new press release: archive the HTML, then every PressRelease PDF it
links, with %PDF validation and retry (rbidocs can serve HTTP 200 bot pages).
HTML is re-fetched +15 and +60 min after first sight to catch re-uploads.

Usage
  uv run python harness/mpc_capture.py --until 14:00
  uv run python harness/mpc_capture.py --until 12:10 --seed-prid 63718   # test: rediscover a known id
Output: archive/captures/<date>_mpc/{events,polls}.jsonl, raw/, SUMMARY.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from selectolax.parser import HTMLParser

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

RSS_PR = "https://rbi.org.in/pressreleases_rss.xml"
RSS_NOTIF = "https://rbi.org.in/notifications_rss.xml"
PR_PAGE = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={}"
ANNUAL = "https://www.rbi.org.in/scripts/Annualpolicy.aspx"
YOUTUBE = "https://www.youtube.com/feeds/videos.xml?channel_id=UCIfCOl43tunZVNYafeC4RQA"

PROBE_AHEAD = 6
MIN_GAP = 1.0                       # seconds between requests to one host
REFETCH_AFTER = (15 * 60, 60 * 60)  # re-upload checks (FLAGS T-08)
PDF_TRIES = 5


def now() -> datetime:
    return datetime.now(IST)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rbi_pubdate(s: str | None) -> str | None:
    """RBI pubDates are naive IST ("Fri, 02 Oct 2026 17:05:00"), FLAGS T-03."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=IST).isoformat()
    except ValueError:
        return None


class Capture:
    def __init__(self, out: Path, until: datetime, seed_prid: int | None):
        self.out, self.until = out, until
        self.raw = out / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.c = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                              follow_redirects=True, timeout=httpx.Timeout(30.0, connect=15.0))
        self.last_host: dict[str, float] = {}
        self.validators: dict[str, dict] = {}
        self.next_due: dict[str, float] = {}
        self.seen: dict[str, set] = {k: set() for k in ("rss_pr", "rss_notif", "annual", "youtube")}
        self.baseline: dict[str, bool] = {k: True for k in self.seen}
        self.prids_done: set[int] = set()
        self.seed = seed_prid
        self.max_prid = seed_prid or 0
        self.probe_queue: list[int] = []
        self.refetch: list[tuple[float, int, str]] = []
        self.html_sha: dict[int, str] = {}

    # ---- io -------------------------------------------------------------
    def log(self, name: str, rec: dict) -> None:
        with (self.out / f"{name}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def save(self, fname: str, body: bytes) -> str:
        p = self.raw / fname
        if p.exists():  # never overwrite a first copy
            stem, dot, ext = fname.rpartition(".")
            p = self.raw / f"{stem}__{now():%H%M%S}.{ext}"
        p.write_bytes(body)
        return p.name

    def get(self, url: str, channel: str, conditional: bool = False) -> httpx.Response | None:
        host = urlparse(url).netloc
        gap = time.time() - self.last_host.get(host, 0)
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        self.last_host[host] = time.time()
        h = {}
        if conditional and url in self.validators:
            v = self.validators[url]
            if v.get("etag"):
                h["If-None-Match"] = v["etag"]
            if v.get("lm"):
                h["If-Modified-Since"] = v["lm"]
        t0 = time.time()
        rec = {"at": now().isoformat(), "channel": channel, "url": url}
        try:
            r = self.c.get(url, headers=h)
        except Exception as e:  # noqa: BLE001
            rec.update(error=f"{type(e).__name__}: {e}"[:200], secs=round(time.time() - t0, 2))
            self.log("polls", rec)
            return None
        rec.update(status=r.status_code, bytes=len(r.content), secs=round(time.time() - t0, 2),
                   ctype=r.headers.get("content-type"))
        self.log("polls", rec)
        if r.status_code == 200 and conditional:
            self.validators[url] = {"etag": r.headers.get("etag"), "lm": r.headers.get("last-modified")}
        return r

    def due(self, key: str, every: float) -> bool:
        t = time.time()
        if t >= self.next_due.get(key, 0):
            self.next_due[key] = t + every
            return True
        return False

    # ---- channels -------------------------------------------------------
    def poll_rss(self, channel: str, url: str) -> None:
        r = self.get(url, channel, conditional=True)
        if r is None or r.status_code != 200:
            return
        fp = feedparser.parse(r.content)
        new = [e for e in fp.entries if e.get("link") not in self.seen[channel]]
        if new and not self.baseline[channel]:
            self.save(f"{channel}_{now():%H%M%S}.xml", r.content)
        for e in new:
            self.seen[channel].add(e.get("link"))
            m = re.search(r"prid=(\d+)", e.get("link", ""), re.I)
            prid = int(m.group(1)) if m else None
            if self.baseline[channel] and prid and self.seed and prid > self.seed:
                self.seen[channel].discard(e.get("link"))  # test mode: leave for rediscovery
                continue
            if prid:
                self.max_prid = max(self.max_prid, prid)
            if self.baseline[channel]:
                if prid:
                    self.prids_done.add(prid)
                continue
            self.log("events", {"first_seen": now().isoformat(), "channel": channel, "kind": "rss_item",
                                "key": e.get("link"), "prid": prid, "title": e.get("title", "")[:300],
                                "source_time": rbi_pubdate(e.get("published"))})
            if prid and channel == "rss_pr":
                self.capture_pr(prid, via=channel)
        self.baseline[channel] = False

    def refill_probes(self) -> None:
        want = [p for p in range(self.max_prid + 1, self.max_prid + 1 + PROBE_AHEAD) if p not in self.prids_done]
        self.probe_queue = [p for p in self.probe_queue if p in want] + \
                           [p for p in want if p not in self.probe_queue]

    def poll_prid(self) -> None:
        self.refill_probes()
        if not self.probe_queue:
            return
        prid = self.probe_queue.pop(0)
        self.probe_queue.append(prid)
        r = self.get(PR_PAGE.format(prid), "prid_probe")
        if r is not None and r.status_code == 200 and HTMLParser(r.text).css_first(".tablebg"):
            self.log("events", {"first_seen": now().isoformat(), "channel": "prid_probe",
                                "kind": "pr_page_live", "prid": prid})
            self.capture_pr(prid, via="prid_probe", resp=r)

    def poll_annual(self) -> None:
        r = self.get(ANNUAL, "annual", conditional=True)
        if r is None or r.status_code != 200:
            return
        links = {urljoin(ANNUAL, a.attributes.get("href") or "") : a.text(strip=True)
                 for a in HTMLParser(r.text).css("a") if a.attributes.get("href")}
        new = {u: t for u, t in links.items() if u not in self.seen["annual"]}
        if new and not self.baseline["annual"]:
            fn = self.save(f"annual_{now():%H%M%S}.html", r.content)
            for u, t in new.items():
                self.log("events", {"first_seen": now().isoformat(), "channel": "annual",
                                    "kind": "new_link", "key": u, "title": t[:300], "page": fn})
        self.seen["annual"] |= set(new)
        self.baseline["annual"] = False

    def poll_youtube(self) -> None:
        r = self.get(YOUTUBE, "youtube")
        if r is None or r.status_code != 200:
            return
        fp = feedparser.parse(r.content)
        for e in fp.entries:
            vid = e.get("yt_videoid") or e.get("id")
            if vid in self.seen["youtube"]:
                continue
            self.seen["youtube"].add(vid)
            if not self.baseline["youtube"]:
                self.log("events", {"first_seen": now().isoformat(), "channel": "youtube", "kind": "video",
                                    "key": vid, "title": e.get("title", "")[:300], "link": e.get("link"),
                                    "source_time": e.get("published"),
                                    "cache_control": r.headers.get("cache-control")})
        self.baseline["youtube"] = False

    # ---- documents ------------------------------------------------------
    def capture_pr(self, prid: int, via: str, resp: httpx.Response | None = None) -> None:
        if prid in self.prids_done:
            return
        self.prids_done.add(prid)
        self.max_prid = max(self.max_prid, prid)
        r = resp or self.get(PR_PAGE.format(prid), "pr_html")
        if r is None or r.status_code != 200:
            self.prids_done.discard(prid)  # let the probe or RSS retry it
            return
        h = HTMLParser(r.text)
        body = h.css_first(".tablebg")
        if body is None:
            self.prids_done.discard(prid)
            return
        text = re.sub(r"\s+", " ", body.text(separator=" ")).strip()
        date = re.search(r"Date\s*:\s*([A-Z][a-z]{2} \d{2}, \d{4})", text)
        heads = [n.text(strip=True) for n in body.css("td.tableheader")]
        title = next((t for t in heads if t and not t.startswith(("(", "Date"))), None)
        fn = self.save(f"pr_{prid}.html", r.content)
        self.html_sha[prid] = sha(r.content)
        pdfs = sorted({urljoin(r.url.__str__(), a.attributes.get("href"))
                       for a in body.css("a") if (a.attributes.get("href") or "").upper().endswith(".PDF")})
        self.log("events", {"first_seen": now().isoformat(), "channel": via, "kind": "pr_html", "prid": prid,
                            "page_date": date.group(1) if date else None,
                            "title": (title or text[:200])[:300],
                            "text_head": text[:400], "sha256": self.html_sha[prid], "file": fn, "pdfs": pdfs})
        for u in pdfs:
            self.capture_pdf(u, prid)
        t = time.time()
        self.refetch += [(t + d, prid, f"+{d // 60}m") for d in REFETCH_AFTER]

    def capture_pdf(self, url: str, prid: int) -> None:
        for i in range(PDF_TRIES):
            r = self.get(url, "pdf")
            if r is not None and r.status_code == 200 and r.content[:4] == b"%PDF":
                fn = self.save(f"pr_{prid}_{Path(urlparse(url).path).name}", r.content)
                self.log("events", {"first_seen": now().isoformat(), "channel": "pdf", "kind": "pdf", "prid": prid,
                                    "url": url, "sha256": sha(r.content), "bytes": len(r.content),
                                    "last_modified": r.headers.get("last-modified"), "file": fn, "tries": i + 1})
                return
            bad = None if r is None else r.content[:300]
            if r is not None and bad:
                self.save(f"pr_{prid}_reject_{i}.html", r.content)
            time.sleep(min(60, 5 * 2 ** i))
        self.log("events", {"first_seen": now().isoformat(), "channel": "pdf", "kind": "pdf_failed",
                            "prid": prid, "url": url, "tries": PDF_TRIES})

    def run_refetch(self) -> None:
        t = time.time()
        due = [x for x in self.refetch if x[0] <= t]
        self.refetch = [x for x in self.refetch if x[0] > t]
        for _, prid, label in due:
            r = self.get(PR_PAGE.format(prid), "pr_refetch")
            if r is None or r.status_code != 200:
                continue
            s = sha(r.content)
            changed = s != self.html_sha.get(prid)
            rec = {"first_seen": now().isoformat(), "channel": "pr_refetch", "kind": "refetch", "prid": prid,
                   "label": label, "sha256": s, "changed": changed}
            if changed:
                rec["file"] = self.save(f"pr_{prid}_{label.strip('+')}.html", r.content)
            self.log("events", rec)

    # ---- loop -----------------------------------------------------------
    def run(self) -> None:
        self.log("polls", {"at": now().isoformat(), "start": True, "until": self.until.isoformat()})
        while now() < self.until:
            try:
                if self.due("rss_pr", 20):
                    self.poll_rss("rss_pr", RSS_PR)
                if self.due("rss_notif", 60):
                    self.poll_rss("rss_notif", RSS_NOTIF)
                if self.max_prid and self.due("prid", 30 / PROBE_AHEAD):
                    self.poll_prid()
                if self.due("annual", 60):
                    self.poll_annual()
                if self.due("youtube", 60):
                    self.poll_youtube()
                self.run_refetch()
            except Exception as e:  # noqa: BLE001  keep capturing whatever breaks
                self.log("polls", {"at": now().isoformat(), "loop_error": f"{type(e).__name__}: {e}"[:300]})
            time.sleep(0.5)
        self.summary()

    def summary(self) -> None:
        ev = [json.loads(l) for l in (self.out / "events.jsonl").open(encoding="utf-8")] \
            if (self.out / "events.jsonl").exists() else []
        polls = [json.loads(l) for l in (self.out / "polls.jsonl").open(encoding="utf-8")]
        errs: dict[str, int] = {}
        for p in polls:
            if p.get("error") or p.get("loop_error") or (p.get("status") and p["status"] not in (200, 304)):
                errs[p.get("channel", "loop")] = errs.get(p.get("channel", "loop"), 0) + 1
        lines = [f"# MPC capture {self.out.name}", "",
                 f"Window ended {now():%Y-%m-%d %H:%M:%S} IST. {len(polls)} requests, {len(ev)} events.", "",
                 "Errors by channel: " + (", ".join(f"{k} {v}" for k, v in errs.items()) or "none"), "",
                 "| first seen (IST) | channel | kind | prid / key | title | source time |",
                 "|---|---|---|---|---|---|"]
        for e in ev:
            if e["kind"] == "refetch" and not e.get("changed"):
                continue
            lines.append(f"| {e['first_seen'][11:19]} | {e['channel']} | {e['kind']} | "
                         f"{e.get('prid') or (e.get('key') or '')[:60]} | "
                         f"{(e.get('title') or e.get('url') or '').replace('|', '/')[:90]} | "
                         f"{(e.get('source_time') or '')[11:19]} |")
        (self.out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="14:00", help="IST HH:MM today")
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed-prid", type=int, default=None,
                    help="start probing above this id instead of the RSS max (testing)")
    a = ap.parse_args()
    hh, mm = map(int, a.until.split(":"))
    until = now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    out = Path(a.out) if a.out else ROOT / "archive" / "captures" / f"{now():%Y-%m-%d}_mpc"
    out.mkdir(parents=True, exist_ok=True)
    cap = Capture(out, until, a.seed_prid)
    cap.run()


if __name__ == "__main__":
    main()
