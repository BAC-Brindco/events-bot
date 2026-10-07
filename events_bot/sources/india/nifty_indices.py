"""NSE Indices (niftyindices.com) press releases: equity index inclusions, exclusions, replacements.

Listing: https://www.niftyindices.com/press-release (one large page, strong ETag -> cheap 304s).
Rows: date, title, PDF link (ind_prs{DDMMYYYY}[_{n}].pdf). Routine maintenance (Nifty IPO daily
inclusions, fixed-income / bond / SME indices) is filtered by config/keywords/nifty_indices.yaml.
Kept releases get the PDF parsed into verbatim (index, action, company, symbol) rows. Semi-annual
reviews run to ~1,000 rows, so the e-mail lists headline indices and watchlist names in full and
counts the rest (Gmail clips messages over ~100 KB); the PDF link carries everything.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, time
from html import escape as _e
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, Message, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST, fmt_ist
from ...deliver import design as dz
from ...deliver import house as hs
from ...extract import nifty_indices as nx
from ...stage1.render import Doc, _provenance

BASE = "https://www.niftyindices.com/"
LISTING_ROWS = 60        # newest first; older rows can never be new (the page holds ~1,500 back to 1998)
HEADLINE = ("Nifty 50", "Nifty Next 50", "Nifty 100", "Nifty 200", "Nifty 500", "Nifty Bank",
            "Nifty Financial Services", "Nifty Midcap 50", "Nifty Midcap 100", "Nifty Midcap 150",
            "Nifty Smallcap 100", "Nifty Smallcap 250")


def parse_listing(content: bytes) -> list[dict]:
    tree = HTMLParser(content.decode("utf-8", "replace"))
    out = []
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if "Press_Release/ind_prs" not in href:
            continue
        ctx = re.sub(r"\s+", " ", a.parent.text(separator=" ", strip=True)) if a.parent else ""
        m = re.match(r"([A-Z][a-z]{2} \d{1,2}, \d{4})", ctx)
        d = datetime.strptime(m.group(1), "%b %d, %Y").date() if m else None
        out.append({"url": urljoin(BASE, href), "title": re.sub(r"\s+", " ", a.text(strip=True)), "date": d})
    return out


class NiftyIndices(SourceAdapter):
    doc_type = "nifty_indices"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return [RawItem(source_id=self.cfg.id, ext_id=f"pr:{r['url'].rsplit('/', 1)[-1]}", url=r["url"],
                        title=r["title"], document_id=res.document_id,
                        source_published_at=datetime.combine(r["date"], time(0), tzinfo=IST) if r["date"] else None,
                        published_raw=r["date"].strftime("%b %d, %Y") if r["date"] else None,
                        meta={"date_only": True, "priority_tags": ["index_change"]})
                for r in parse_listing(res.content)[:LISTING_ROWS]]

    def enrich(self, it: RawItem) -> RawItem:
        """Kept releases: parse the PDF so watchlist tagging sees every company/symbol."""
        res = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=5, source_id=self.cfg.id,
                                              doc_type="nifty_indices:pdf", expect="pdf", conditional=False,
                                              parent_id=it.document_id)
        r = nx.parse_changes(res.content)
        return it.model_copy(update={
            "summary": "; ".join(f"{x['symbol']} {x['company']}" for x in r["rows"]),
            "meta": {**it.meta, "rows": r["rows"], "effective": r["effective"]}})

    def render_stage1(self, it: RawItem, *, first_seen: datetime, mode: str) -> Message | None:
        rows = (it.meta or {}).get("rows")
        if rows is None:
            return None
        wl = self.ctx.app.pipeline.watchlist if self.ctx.app else None
        watched = {x["symbol"] for x in rows if wl and wl.tags(RawItem(source_id="x", ext_id="x", url="x",
                                                                        title=f"{x['symbol']} {x['company']}"))}
        counts = Counter((x["index"], x["action"]) for x in rows)
        indices = list(dict.fromkeys(x["index"] for x in rows))
        head = [i for i in indices if i in HEADLINE] or indices[:6]
        detail = [x for x in rows if x["index"] in head or x["symbol"] in watched]

        n_ex = sum(1 for x in rows if x["action"] == "excluded")
        n_in = sum(1 for x in rows if x["action"] == "included")
        lead = f"{it.title}: {n_in} inclusions and {n_ex} exclusions across {len(indices)} indices (counted from the release)."
        if it.meta.get("effective"):
            lead += f" Effective from {it.meta['effective']}."

        def syms(idx: str, action: str) -> str:
            out = []
            for x in rows:
                if x["index"] == idx and x["action"] == action:
                    s_ = _e(x["symbol"])
                    out.append(f"<strong style=\"color:{dz.BAD};\">{s_}</strong>" if x["symbol"] in watched else s_)
            return ", ".join(out) or dz.value("")

        # Compact: one row per headline index (symbols verbatim), watchlist names with company, the rest as
        # counts in one paragraph. Keeps a ~1,000-row semi-annual review well under Gmail's ~100 KB clip.
        body = dz.masthead(kicker=hs.KICKER, title="Index Changes",
                           dateline=f"{_e(it.published_raw or '')} &middot; NSE Indices",
                           subline=f"NSE Indices Limited &middot; first seen {fmt_ist(first_seen)}")
        body += dz.row(dz.callout(_e(lead), accent="gold", title="What changes"), pad=dz.BLOCK_PAD)
        if watched:
            wrows = [[f"<strong>{_e(x['symbol'])}</strong>", _e(x["company"]), _e(x["index"]), _e(x["action"])]
                     for x in rows if x["symbol"] in watched]
            body += dz.row(hs.section_caption("", "Watchlist names affected")
                           + dz.datatable(["Symbol", "Company", "Index", "Change"], wrows, align=["l"] * 4,
                                          source="NSE Indices Limited press release"), pad=dz.SECTION_PAD)
        body += dz.row(hs.section_caption("i", "Headline indices", "symbols as printed; watchlist names in red")
                       + dz.datatable(["Index", "Excluded", "Included"],
                                      [[f"<strong>{_e(i)}</strong>", syms(i, "excluded"), syms(i, "included")]
                                       for i in head], align=["l", "l", "l"],
                                      source="NSE Indices Limited press release"), pad=dz.SECTION_PAD)
        others = [i for i in indices if i not in head]
        if others:
            para = "; ".join(f"{_e(i)} (&minus;{counts[(i, 'excluded')]} / +{counts[(i, 'included')]})" for i in others)
            body += dz.row(hs.section_caption("ii", f"{len(others)} other indices", "exclusions / inclusions counted "
                                              "from the release; full lists in the PDF") + hs.prose(para, size=11.5,
                                                                                                    leading=17),
                           pad=dz.SECTION_PAD)
        body += dz.row(hs.section_caption("", "Source documents") + hs.links([Doc("Press release (PDF)", it.url)]),
                       pad=dz.SECTION_PAD)
        body += dz.row(dz.colophon(_provenance("NSE Indices Limited", it.ref, mode), hs.DISCLAIMER),
                       pad="26px 24px 26px 24px")
        subject = f"[NSE Indices] {it.title}" + (" | WATCHLIST" if watched else "")
        text = [lead, ""] + [f"{x['index']}: {x['action']} {x['symbol']} ({x['company']})" for x in detail] + ["", it.url]
        return Message(ref=it.ref, stage="stage1", kind="realtime", subject=subject,
                       body_html=dz.doc_open(_e(subject), _e(lead)) + body + dz.DOC_CLOSE, body_text="\n".join(text))
