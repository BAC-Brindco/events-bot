"""Phase 0 lag watcher (India Tier 1).

Polls feeds every POLL seconds and records when each item is first seen,
then compares that with the official timestamp:
  * PIB  : RSS has no pubDate -> fetch release page, parse "Posted On: DD MON YYYY H:MMAM/PM"
           and the ministry line. lag = first_seen - posted_on (upper bound = poll interval).
  * RSS w/ pubDate (RBI, SEBI): lag = first_seen - pubDate.
Items present on the first poll are 'baseline' (no lag claim).

Usage:  uv run --project "Z:\\Data Pipelines\\events_bot" python india_t1_lag_watch.py [minutes] [feeds...]
Output: docs/phase0/raw/india_t1/lag_watch_<feed>.jsonl
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
from selectolax.parser import HTMLParser

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from _probe_common import RAW, client, fetch  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
POLL = 120

FEEDS = {
    # NB: the URL advertised on pib.gov.in/ViewRss.aspx (without &reg=3) 302-redirects to the Hindi feed.
    "pib": "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3&reg=3",
    "rbi_pr": "https://rbi.org.in/pressreleases_rss.xml",
    "rbi_notif": "https://rbi.org.in/notifications_rss.xml",
    "sebi": "https://www.sebi.gov.in/sebirss.xml",
}

POSTED_RE = re.compile(r"Posted On:\s*(\d{1,2} [A-Z]{3} \d{4}) (\d{1,2}:\d{2}\s?[AP]M)")


def pib_meta(c, prid: str) -> dict:
    r = fetch(c, f"https://www.pib.gov.in/PressReleasePage.aspx?PRID={prid}&reg=3&lang=1", save=False)
    if r.get("status") != 200:
        return {"err": r.get("status") or r.get("error")}
    h = HTMLParser(r["_resp"].text)
    txt = re.sub(r"\s+", " ", h.body.text(separator=" ")) if h.body else ""
    m = POSTED_RE.search(txt)
    out = {}
    if m:
        out["posted_on"] = datetime.strptime(f"{m.group(1)} {m.group(2).replace(' ', '')}",
                                             "%d %b %Y %I:%M%p").replace(tzinfo=IST).isoformat()
    mn = h.css_first("#MinistryName")
    if mn:
        out["ministry"] = re.sub(r"\s+", " ", mn.text()).strip()
    pd = h.css_first("#PrDateTime")
    if pd:
        out["posted_raw"] = re.sub(r"\s+", " ", pd.text()).strip()
    return out


def main() -> None:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 90
    names = sys.argv[2:] or ["pib"]
    c = client()
    seen: dict[str, set] = {n: set() for n in names}
    first = {n: True for n in names}
    end = time.time() + minutes * 60
    while time.time() < end:
        for n in names:
            r = fetch(c, FEEDS[n], save=False)
            now = datetime.now(IST)
            if r.get("status") != 200:
                with (RAW / f"lag_watch_{n}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"poll_error": r.get("status") or r.get("error"), "at": now.isoformat()}) + "\n")
                continue
            fp = feedparser.parse(r["_resp"].content)
            for e in fp.entries:
                key = e.get("link") or e.get("id") or e.get("title")
                if key in seen[n]:
                    continue
                seen[n].add(key)
                rec = {"feed": n, "key": key, "title": e.get("title", "")[:200],
                       "first_seen": now.isoformat(), "baseline": first[n],
                       "pubDate": e.get("published")}
                if n == "pib" and not first[n]:
                    prid = key.split("PRID=")[-1]
                    rec.update(pib_meta(c, prid))
                    if "posted_on" in rec:
                        rec["lag_min"] = round((now - datetime.fromisoformat(rec["posted_on"])).total_seconds() / 60, 1)
                elif e.get("published_parsed") or e.get("published"):
                    # RBI pubDate carries NO timezone ("Thu, 01 Oct 2026 14:35:00") and is IST;
                    # feedparser would treat it as UTC, so re-interpret naive stamps as IST.
                    naive = not re.search(r"(GMT|UTC|[+-]\d{4}|[A-Z]{3})\s*$", (e.get("published") or "").strip())
                    if e.get("published_parsed"):
                        pub = datetime(*e.published_parsed[:6], tzinfo=IST if naive else timezone.utc)
                    else:  # feedparser returns published_parsed=None for RBI's TZ-less stamps
                        pub = datetime.strptime(e["published"].strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=IST)
                    rec["pub_ist"] = pub.astimezone(IST).isoformat()
                    rec["lag_min"] = round((now - pub).total_seconds() / 60, 1)
                with (RAW / f"lag_watch_{n}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            first[n] = False
        time.sleep(POLL)


if __name__ == "__main__":
    main()
