"""NSE surveillance lists: ASM (long- and short-term) and GSM, as consolidated change alerts.

The APIs return the full current lists (daily snapshots; asmTime / gsmTime is the refresh date, not
the entry date), so additions, removals and stage moves are found by diffing against the previous
archived snapshot of the same URL. One alert per changed snapshot, not one per stock; names on the
watchlist are flagged at the top.

Access (memory: nse-egress-and-cookie-bootstrap): '/' 403s but sets cookies, '/all-reports' yields
_abck + nsit; then the JSON endpoints answer with a Referer. Works from GitHub runners.
"""
from __future__ import annotations

import json
from datetime import datetime
from html import escape as _e

from ...core.fetch import FetchError
from ...core.models import FetchResult, Message, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import fmt_ist
from ...deliver import design as dz
from ...deliver import house as hs
from ...stage1.render import Doc, _provenance

BOOT = ("https://www.nseindia.com/", "https://www.nseindia.com/all-reports")


def snapshot(kind: str, payload) -> dict[tuple[str, str], dict]:
    """{(list, symbol): row} with list in {'LT-ASM', 'ST-ASM', 'GSM'}."""
    out = {}
    if kind == "asm" and isinstance(payload, dict):
        for key, label in (("longterm", "LT-ASM"), ("shortterm", "ST-ASM")):
            for r in (payload.get(key) or {}).get("data") or []:
                out[(label, r.get("symbol"))] = {"stage": r.get("asmSurvIndicator") or r.get("survCode"),
                                                  "name": r.get("companyName"), "time": r.get("asmTime")}
    elif kind == "gsm" and isinstance(payload, list):
        for r in payload:
            out[("GSM", r.get("symbol"))] = {"stage": r.get("gsmStage"), "name": r.get("companyName"),
                                              "desc": r.get("survDesc"), "time": r.get("gsmTime")}
    return out


def diff(old: dict, new: dict) -> list[dict]:
    ch = []
    for k, v in new.items():
        if k not in old:
            ch.append({"list": k[0], "symbol": k[1], "name": v["name"], "change": "added", "stage": v["stage"]})
        elif old[k]["stage"] != v["stage"]:
            ch.append({"list": k[0], "symbol": k[1], "name": v["name"], "change": "stage change",
                       "stage": f"{old[k]['stage']} -> {v['stage']}"})
    for k, v in old.items():
        if k not in new:
            ch.append({"list": k[0], "symbol": k[1], "name": v["name"], "change": "removed", "stage": v["stage"]})
    order = {"added": 0, "stage change": 1, "removed": 2}
    return sorted(ch, key=lambda c: (order[c["change"]], c["list"], c["symbol"] or ""))


class NseSurveillance(SourceAdapter):
    doc_type = "nse_surv"

    def _bootstrap(self) -> None:
        for u in BOOT:
            try:
                self.ctx.fetcher.client.get(u)          # '/' answers 403 but sets the cookies
            except Exception:  # noqa: BLE001
                pass

    def poll(self) -> list[RawItem]:
        self._bootstrap()
        items = []
        for name in ("asm", "gsm"):
            url = self.cfg.urls[name]
            res = self.ctx.fetcher.get(url, source_id=self.cfg.id, doc_type=f"nse_surv:{name}", expect="json",
                                       conditional=False,
                                       headers={"Referer": f"https://www.nseindia.com/reports/{name}",
                                                "Accept": "application/json"})
            items += self._changes(name, url, res)
        return items

    def _changes(self, name: str, url: str, res: FetchResult) -> list[RawItem]:
        prev = self.ctx.db.one("select storage_key from documents where url = %s and sha256 <> %s "
                               "order by fetched_at desc limit 1", (url, res.sha256)) if self.ctx.db else None
        if prev is None:
            return []                                     # first snapshot: baseline, nothing to diff
        new = snapshot(name, json.loads(res.content))
        old = snapshot(name, json.loads(self.ctx.archive.get(prev["storage_key"])))
        if not new:
            raise FetchError(url, "shape", "empty surveillance list")
        ch = diff(old, new)
        if not ch:
            return []
        stamp = next(iter(new.values())).get("time") or ""
        added = sum(c["change"] == "added" for c in ch)
        removed = sum(c["change"] == "removed" for c in ch)
        moved = len(ch) - added - removed
        label = "ASM" if name == "asm" else "GSM"
        title = f"NSE {label} lists updated ({stamp}): {added} added, {removed} removed, {moved} stage changes"
        return [RawItem(source_id=self.cfg.id, ext_id=f"{name}:{res.sha256[:16]}", url=self.cfg.urls[f"{name}_page"],
                        title=title, document_id=res.document_id,
                        # summary carries every changed symbol/name so watchlist tagging sees them
                        summary="; ".join(f"{c['symbol']} {c['name']}" for c in ch),
                        meta={"kind": name, "changes": ch, "stamp": stamp})]

    def render_stage1(self, it: RawItem, *, first_seen: datetime, mode: str) -> Message | None:
        ch = (it.meta or {}).get("changes") or []
        wl = self.ctx.app.pipeline.watchlist if self.ctx.app else None
        flagged = {c["symbol"] for c in ch if wl and wl.tags(RawItem(source_id="x", ext_id="x", url="x",
                                                                      title=f"{c['symbol']} {c['name']}"))}
        rows = [[(dz.badge("WATCHLIST", dz.BAD) if c["symbol"] in flagged else "") + f"<strong>{_e(c['symbol'] or '')}</strong>",
                 _e(c["name"] or ""), _e(c["list"]), _e(c["change"]), _e(str(c["stage"] or ""))] for c in ch]
        body = dz.masthead(kicker=hs.KICKER, title="NSE Surveillance",
                           dateline=f"{_e(it.meta.get('stamp', ''))} &middot; List changes",
                           subline=f"National Stock Exchange of India &middot; first seen {fmt_ist(first_seen)}")
        body += dz.row(dz.callout(f"<strong>{_e(it.title)}</strong>", accent="gold", title="What changed"),
                       pad=dz.BLOCK_PAD)
        body += dz.row(hs.section_caption("i", "Changes", "watchlist names first when a watchlist is configured")
                       + dz.datatable(["Symbol", "Company", "List", "Change", "Stage"],
                                      sorted(rows, key=lambda r: "WATCHLIST" not in r[0]),
                                      align=["l", "l", "l", "l", "l"],
                                      source="NSE surveillance lists (ASM/GSM), diff against the previous snapshot"),
                       pad=dz.SECTION_PAD)
        body += dz.row(hs.section_caption("", "Source documents") + hs.links([Doc("NSE surveillance reports", it.url)]),
                       pad=dz.SECTION_PAD)
        body += dz.row(dz.colophon(_provenance("National Stock Exchange of India", it.ref, mode), hs.DISCLAIMER),
                       pad="26px 24px 26px 24px")
        subject = f"[NSE] Surveillance | {it.title}"
        text = [it.title, ""] + [f"{c['change'].upper():12} {c['list']:6} {c['symbol']:12} {c['stage']} — {c['name']}"
                                 for c in ch] + ["", it.url]
        return Message(ref=it.ref, stage="stage1", kind="realtime", subject=subject,
                       body_html=dz.doc_open(_e(subject), _e(it.title)) + body + dz.DOC_CLOSE,
                       body_text="\n".join(text))
