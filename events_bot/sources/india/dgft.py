"""DGFT: notifications, public notices and trade notices (PROPOSAL §d, Phase 0 india_t1).

Three server-rendered tables on www.dgft.gov.in, one per kind, each holding every row back to Oct 2023:

  notification   https://www.dgft.gov.in/CP/?opt=notification     (~235 rows, ~290 KB)
  public-notice  https://www.dgft.gov.in/CP/?opt=public-notice    (~180 rows)
  trade-notice   https://www.dgft.gov.in/CP/?opt=trade-notice     (~110 rows)

`table#metaTable` columns: Sl.No., Number, Year, Description, Date, CRT DT (hidden, the upload time to the
second, IST), Attachment (a PDF on content.dgft.gov.in/Website/dgftprod/{uuid}/{name}.pdf; names contain
spaces). Rows are ordered by Date, not by upload, and numbers are typed by hand ("47", "47/2026-27",
"33/2026-2027", "Corrigendum to ..."), so the item key is kind + CRT DT, the one machine-written field.
Many attachments are scans with no text layer (Adobe Scan, *_0001.pdf): the digest then carries the title
and a note pointing at the PDF.

Reachability (F-17): dgft.gov.in sits behind CloudFront and returns 403 to GitHub-runner IPs for httpx and
curl_cffi alike; it answers from an Indian egress (verified 26 Nov 2026). The source ships disabled.

Desk scope (F-29) is decided here, per row:
  notifications   kept unless procedural (Board of Trade nominations, halal certification, agency lists,
                  timeline extensions of schemes, portal / IT changes)
  public / trade  dropped unless they change what can be exported or imported, or at what price or duty:
  notices         export/import policy, bans, prohibitions, quotas and TRQ allocations, MIP/MEP, RoDTEP /
                  RoSCTL / MEIS / drawback rates, and the sensitive commodities (wheat, rice, sugar, pulses,
                  onion, edible oil, gold, silver)
  Procedural items (SION norms, ANF forms, Appendix agency lists, CoO e-filing, PSIA/PSIC, EODC, portal
  modules, comments invited, application windows) are always dropped.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import IST

LISTING = "https://www.dgft.gov.in/CP/?opt={}"
KIND = {"notification": "Notification", "public-notice": "Public notice", "trade-notice": "Trade notice"}
NEWEST = 60              # rows per table handed to the pipeline (the tables go back to 2023)
MIN_ROWS = 50            # a table shorter than this is a broken page, not a quiet day

# Changes to what can cross the border, or at what price / duty / incentive rate.
_KEEP = re.compile(
    r"\b(?:export|import)\s+polic(?:y|ies)\b|polic(?:y|ies)\s+conditions?\b|"
    r"\bprohibit|\bban\b|\bbanned\b|restrict|"
    r"\bquota\b|\bTRQ\b|Tariff Rate Quota|allocation of|re-?allocation|modalities for export|"
    r"Minimum (?:Import|Export) Price|\bMIP\b|\bMEP\b|"
    r"\bRoDTEP\b|\bRoSCTL\b|\bMEIS\b|\bdrawback\b|\bduty\b|\bduties\b|\brates?\b|"
    r"\bwheat\b|\brice\b|\bpaddy\b|\bsugar\b|\bpulses\b|\bonions?\b|\btur\b|\burad\b|\bpeas\b|"
    r"edible oil|\bgold\b|\bsilver\b|\bbullion\b|\bGem\b|Jewellery|\bsteel\b|\burea\b|\bfertili[sz]er",
    re.I)
# Procedure, forms, IT systems, agency lists and calls for comments: never macro.
_DROP = re.compile(
    r"\bSIONs?\b|Standard Input[- ]Output Norms|\bANFs?\b|Appendix[- ]?2[A-Z]\b|"
    r"Certificate of Origin|\bCoO\b|\bPSIAs?\b|\bPSICs?\b|Pre-Shipment Inspection|\bEODCs?\b|"
    r"\bRCMC\b|Bank Guarantee|\bmodule\b|e-?platform|Trade Connect|\bportal\b|\bonline\b|Open API|"
    r"inviting|comments|suggestions|views on|"
    r"extension of (?:timelines?|last date|filing)|surrender of unutilised|"
    r"\bHalal\b|Board of Trade|advisory against|sub-?committee|composition of|constitution of|"
    r"Agenc(?:y|ies) Authori[sz]ed|Free Sale and Commerce Certificate|Quality Complaints|"
    r"Niryat (?:Disha|Protsahan)|Export Promotion Mission|Interest Subvention|\bRELIEF\b|"
    r"extension of timelines|last date|window for submission|application fees|RoDTEP Returns",
    re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def classify(kind: str, title: str) -> tuple[str, str]:
    """(route_hint, scope_reason) for one row. India never goes realtime (F-29)."""
    label = KIND.get(kind, kind)
    if _DROP.search(title):
        return "drop", f"DGFT {label.lower()}: procedural"
    if kind == "notification" or _KEEP.search(title):
        return "india_eod", f"DGFT {label.lower()}: trade policy / rate change"
    return "drop", f"DGFT {label.lower()}: outside desk scope"


def parse_listing(content: bytes) -> list[dict]:
    """Rows of `table#metaTable`, newest upload first. Columns are located by header text."""
    tree = HTMLParser(content.decode("utf-8", "replace"))
    t = tree.css_first("table#metaTable")
    if t is None:
        return []
    head = [_clean(th.text()).lower() for th in t.css("thead th")]

    def col(name: str) -> int | None:
        return head.index(name) if name in head else None

    i_no, i_desc, i_date, i_crt = col("number"), col("description"), col("date"), col("crt dt")
    if None in (i_no, i_desc, i_crt):
        raise ValueError(f"DGFT table header changed: {head}")
    out = []
    for tr in t.css("tbody tr"):
        tds = tr.css("td")
        if len(tds) < len(head):
            continue
        crt_raw = _clean(tds[i_crt].text())
        try:
            crt = datetime.strptime(crt_raw, "%d/%m/%Y %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            continue
        a = tr.css_first("a[href]")
        href = (a.attributes.get("href") or "").strip() if a else ""
        out.append({"number": _clean(tds[i_no].text()), "title": _clean(tds[i_desc].text()),
                    "date": _clean(tds[i_date].text()) if i_date is not None else "", "crt": crt,
                    "crt_raw": crt_raw, "pdf": quote(href, safe=":/?=&%#") if href else None})
    out.sort(key=lambda r: r["crt"], reverse=True)
    return out


class Dgft(SourceAdapter):
    doc_type = "dgft"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        rows = parse_listing(res.content)
        if len(rows) < MIN_ROWS:
            # Never treat a broken page as a quiet day (Phase 0 recommendation: alert on parse failure).
            raise ValueError(f"DGFT {url_name}: parsed {len(rows)} rows < {MIN_ROWS}")
        items = []
        for r in rows[:NEWEST]:
            hint, reason = classify(url_name, r["title"])
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"{url_name}:{r['crt']:%Y%m%d%H%M%S}",
                url=r["pdf"] or LISTING.format(url_name), title=r["title"],
                source_published_at=r["crt"], published_raw=r["crt_raw"], document_id=res.document_id,
                meta={"kind": KIND[url_name], "number": r["number"], "date": r["date"], "feed": url_name,
                      "route_hint": hint, "scope_reason": reason, "priority_tags": [f"dgft:{url_name}"]}))
        return items

    def digest_body(self, it: RawItem):
        """The attachment PDF. Most DGFT PDFs are scans: then the body is empty and a note says so."""
        from ...digest import content
        if not it.url.lower().endswith(".pdf"):
            return content.Body("", [], it.url, None, None, notes=["DGFT listed no attachment for this row."])
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:dgft_pdf", expect="pdf", conditional=False)
        text = content.pdf_body(r.content)
        notes = [] if len(text) > 200 else ["The PDF is a scan with no text layer; see the PDF."]
        return content.Body(text, content._paragraphs(text), it.url, it.url, r.document_id, notes=notes)
