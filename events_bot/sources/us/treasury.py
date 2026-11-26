"""U.S. Treasury -> US Morning Wrap (section "Treasury").

treasury_auctions    TreasuryDirect https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=7
                     (no key, no validators; format=xml answers 406). A row gets its results ~3-4 min after the
                     competitive close (updatedTimestamp, ET wall time). Coupon auctions (notes, bonds, TIPS,
                     FRNs) are kept once the row carries a results file and a bid-to-cover; bills and cash-
                     management bills are dropped. The figures come from the results XML
                     https://www.treasurydirect.gov/xml/R_{YYYYMMDD}_{n}.xml (filename from the JSON, LM +2.5 min).
treasury_press       GovDelivery RSS https://public.govdelivery.com/topics/USTREAS_49/feed.rss (ETag). Mostly
                     sanctions designations and ceremonies; kept: refunding, borrowing estimates, debt limit,
                     TIC, buybacks, fiscal / tax / tariff / FX / China items, and sanctions that name India.
                     Items link to content.govdelivery.com bulletins (the full release, #bulletin_body).
treasury_debt_limit  https://home.treasury.gov/policy-issues/financial-markets-financial-institutions-and-fiscal-service/debt-limit
                     (ETag+LM, conditional GET). Every PDF in the page body (letters to Congress, debt-subject-
                     to-limit reports, fund reports) is an item; dormant since the 25 Jun 2025 letter.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin
from xml.etree import ElementTree as ET_XML

from selectolax.parser import HTMLParser

from ...core.models import FetchResult, RawItem
from ...core.registry import SourceAdapter
from ...core.timeutil import ET, parse_rfc822
from ...digest import content
from .fed_docs import rss_entries

WRAP = "us_morning_wrap"

# ---- treasury_auctions ----------------------------------------------------------------------------------

TD = "https://www.treasurydirect.gov"
RESULT_XML = TD + "/xml/{name}"
RESULT_PDF = TD + "/instit/annceresult/press/preanre/{year}/{name}"


def _et(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s).replace(tzinfo=ET) if s else None
    except ValueError:
        return None


def auction_rows(raw: bytes) -> list[dict]:
    rows = json.loads(raw.decode("utf-8-sig"))
    return rows if isinstance(rows, list) else []


def is_coupon(row: dict) -> bool:
    return row.get("securityType") in ("Note", "Bond") and row.get("cashManagementBill") != "Yes"


def auction_title(row: dict) -> str:
    term = " ".join(x for x in (row.get("securityTerm"), row.get("type") or row.get("securityType")) if x) or "Treasury"
    return f"{term}{' (reopening)' if row.get('reopening') == 'Yes' else ''} auction results"


class TreasuryAuctions(SourceAdapter):
    doc_type = "treasury_auctions"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in auction_rows(res.content):
            cusip, close = r.get("cusip"), (r.get("auctionDate") or r.get("competitiveAuctionCloseDate") or "")[:10]
            xml_name = r.get("treasuryAuctionResultsXmlFileName") or r.get("xmlFilenameCompetitiveResults")
            if not cusip or not close:
                continue
            if not is_coupon(r):
                hint, reason = "drop", "bill / cash-management bill auction"
            elif not xml_name or not r.get("bidToCoverRatio"):
                continue        # auctioned but results not posted yet: picked up by a later poll
            else:
                hint, reason = WRAP, "coupon auction results"
            pdf_name = r.get("treasuryAuctionResultsPressReleaseFileName") or ""
            url = (RESULT_PDF.format(year=close[:4], name=pdf_name) if pdf_name
                   else RESULT_XML.format(name=xml_name) if xml_name else f"{TD}/auctions/auction-query/")
            figs = {k: r.get(k) for k in ("highYield", "highDiscountMargin", "bidToCoverRatio", "interestRate",
                                          "offeringAmount") if r.get(k)}
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=f"{cusip}:{close}", url=url, title=auction_title(r),
                source_published_at=_et(r.get("updatedTimestamp")), published_raw=r.get("updatedTimestamp"),
                summary="; ".join(f"{k} {v}" for k, v in figs.items()) or None, document_id=res.document_id,
                meta={"route_hint": hint, "scope_reason": reason, "kind": "Auction results", "cusip": cusip,
                      "auction_date": close, "security_type": r.get("securityType"),
                      "result_xml": RESULT_XML.format(name=xml_name) if xml_name else None,
                      "tips": r.get("inflationProtected") == "Yes", "frn": r.get("floatingRate") == "Yes"}))
        return items

    def _xml(self, it: RawItem):
        url = (it.meta or {}).get("result_xml")
        if not url:
            raise ValueError("no results XML for this auction")
        return self.ctx.fetcher.get_with_retry(url, tries=3, base_delay=4, source_id=self.cfg.id,
                                               doc_type="treasury_auctions:result_xml", expect="xml",
                                               conditional=False, parent_id=it.document_id)

    def enrich(self, it: RawItem) -> RawItem:
        r = self._xml(it)
        res = parse_result_xml(r.content)
        return it.model_copy(update={"summary": "\n".join(auction_lines(res)),
                                     "meta": {**it.meta, "results": res, "result_document_id": r.document_id}})

    def digest_body(self, it: RawItem) -> content.Body:
        r = self._xml(it)
        text = "\n".join(auction_lines(parse_result_xml(r.content)))
        return content.Body(text, content._paragraphs(text), it.url, it.url if it.url.endswith(".pdf") else None,
                            r.document_id)


def parse_result_xml(raw: bytes) -> dict[str, str]:
    """Every leaf of <TreasuryAuctionResults> by tag name, values verbatim (first occurrence wins)."""
    root = ET_XML.fromstring(raw)
    node = root.find(".//TreasuryAuctionResults")
    out: dict[str, str] = {}
    for el in (node if node is not None else root).iter():
        if len(el) == 0 and el.text and el.text.strip():
            out.setdefault(el.tag, el.text.strip())
    return out


def _pct(part: str | None, whole: str | None) -> str | None:
    try:
        p, w = Decimal(part or ""), Decimal(whole or "")
    except InvalidOperation:
        return None
    return f"{(p * 100 / w):.1f}" if w else None


def auction_lines(x: dict[str, str]) -> list[str]:
    """Deterministic text of a results XML: every figure is the XML value verbatim; the bidder shares are
    computed from the XML's competitive awards and say so."""
    g = x.get
    desc = g("SecurityDescription") or g("SecurityType") or "Treasury security"
    if g("Reopening") == "Y":
        desc += " (reopening)"
    lines = [f"{desc}, CUSIP {g('CUSIP')}, auctioned {g('CompetitiveAuctionCloseDate')} "
             f"(competitive close {g('CompetitiveAuctionCloseTime')}); offering amount {g('OfferingAmount')}; "
             f"issue date {g('IssueDate')}; maturity date {g('MaturityDate')}."]
    if g("HighYield"):
        y = (f"High yield {g('HighYield')} percent"
             + (" (real yield; inflation-protected)" if g("InflationProtected") == "Y" else "")
             + (f", allotted at high {g('AllottedAtHighYieldPercentage')} percent" if g("AllottedAtHighYieldPercentage") else "")
             + (f"; median yield {g('MedianYield')}" if g("MedianYield") else "")
             + (f"; low yield {g('LowYield')}" if g("LowYield") else "")
             + (f"; interest rate {g('InterestRate')} percent" if g("InterestRate") else "") + ".")
        lines.append(y)
    if g("HighDiscountMargin"):
        lines.append(f"High discount margin {g('HighDiscountMargin')} percent"
                     + (f", allotted at high {g('AllottedAtHighDiscountMarginPercentage')} percent"
                        if g("AllottedAtHighDiscountMarginPercentage") else "")
                     + (f"; median discount margin {g('MedianDiscountMargin')}" if g("MedianDiscountMargin") else "")
                     + (f"; spread {g('Spread')}" if g("Spread") else "") + " (floating rate note).")
    if g("HighRate") and not g("HighYield"):
        lines.append(f"High rate {g('HighRate')} percent (investment rate {g('InvestmentRate')}).")
    lines.append(f"Bid-to-cover ratio {g('BidToCoverRatio')}; competitive tenders "
                 f"{g('SubtotalCompetitiveParRecognizedInsideOfOfferingAmount')}, competitive accepted "
                 f"{g('SubtotalCompetitiveParAwardedInsideOfOfferingAmount')}; total accepted "
                 f"{g('TotalParAwardedInsideOfOfferingAmount')}.")
    comp = g("SubtotalCompetitiveParAwardedInsideOfOfferingAmount")
    pd, di, ind = (g("PrimaryDealersCompetitiveParAwarded"), g("DirectBiddersCompetitiveParAwarded"),
                   g("IndirectBiddersCompetitiveParAwarded"))
    if comp and pd and di and ind:
        shares = [s for s in (_pct(ind, comp), _pct(di, comp), _pct(pd, comp))]
        line = (f"Competitive awards: indirect bidders {ind}, direct bidders {di}, primary dealers {pd}")
        if all(shares):
            line += (f" (shares of competitive accepted, computed from these amounts: indirect {shares[0]}%, "
                     f"direct {shares[1]}%, primary dealers {shares[2]}%)")
        lines.append(line + ".")
    return lines


# ---- treasury_press ----------------------------------------------------------------------------------------

_PRESS_NOISE = re.compile(r"^(?:STARTING SOON|HAPPENING NOW|ICYMI|MEDIA ADVISORY|WATCH|LIVESTREAM|PHOTOS?)\b|"
                          r"Trump Accounts?\b|\bAward(?:s|ed)?\b|\bCeremony\b|\bSwear(?:s|ing)[- ]in\b", re.I)
_SANCTIONS = re.compile(r"\bSanction|\bDesignat|\bTargets?\b|Operation Economic Outcast|\bOFAC\b|"
                        r"Disrupts?\b|Fraud\b|Scam\b|Cartel|Terroris|Trafficking", re.I)
_INDIA = re.compile(r"\bIndia(?:n|’s|'s)?\b")
_PRESS_KEEP = re.compile(
    r"refunding|borrowing estimate|marketable borrowing|debt limit|debt ceiling|extraordinary measures|"
    r"Treasury International Capital|\bTIC\b|buyback|auction (?:size|schedule)|Monthly Treasury Statement|"
    r"budget deficit|\bdeficit\b|tariff|trade (?:deal|agreement|talks)|currency|exchange rate|foreign exchange|"
    r"\bChina\b|\bChinese\b|Vice Premier|\bIndia(?:n)?\b|tax cut|tax relief|tax bill|reconciliation|"
    r"global minimum tax|Pillar (?:One|Two)|\bG-?7\b|\bG-?20\b|\bIMF\b|bond market|Treasury market|stablecoin",
    re.I)


def classify_treasury_press(title: str, summary: str) -> tuple[str, str]:
    if _PRESS_NOISE.search(title):
        return "drop", "Treasury advisory / ceremonial item"
    if _SANCTIONS.search(title):
        if _INDIA.search(title) or _INDIA.search(summary):
            return WRAP, "Treasury sanctions action naming India"
        return "drop", "routine OFAC / enforcement action"
    m = _PRESS_KEEP.search(title)
    if m:
        return WRAP, f"Treasury financing / fiscal / trade release ('{m.group(0)}')"
    return "drop", "Treasury release outside macro scope"


class TreasuryPress(SourceAdapter):
    doc_type = "treasury_press"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        items = []
        for r in rss_entries(res.content):
            if not r["link"]:
                continue
            summary = re.sub(r"\s+", " ", HTMLParser(r["summary"] or "<p></p>").text()).strip()
            hint, reason = classify_treasury_press(r["title"], summary)
            items.append(RawItem(
                source_id=self.cfg.id, ext_id=r["link"].rstrip("/").rsplit("/", 1)[-1], url=r["link"],
                title=r["title"], source_published_at=parse_rfc822(r["published_raw"]),
                published_raw=r["published_raw"], summary=summary[:600] or None, document_id=res.document_id,
                meta={"route_hint": hint, "scope_reason": reason, "kind": "Press release"}))
        return items

    def digest_body(self, it: RawItem) -> content.Body:
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:treasury_bulletin", expect="html", conditional=False)
        text = content.html_body(r.content, "#bulletin_body")
        return content.Body(text, content._paragraphs(text), it.url, None, r.document_id)


# ---- treasury_debt_limit -------------------------------------------------------------------------------------

HOME = "https://home.treasury.gov"
_DATE = re.compile(r"\((\d{1,2})/(\d{1,2})/(\d{2,4})\)")


def parse_debt_limit(raw: bytes) -> list[dict]:
    tree = HTMLParser(raw.decode("utf-8", "replace"))
    body = tree.css_first(".field--name-field-page-body")
    out, seen = [], set()
    for a in (body.css("a[href]") if body is not None else []):
        href = a.attributes.get("href") or ""
        if not href.lower().endswith(".pdf"):
            continue
        url = urljoin(HOME, href)
        if url in seen:
            continue
        seen.add(url)
        title = re.sub(r"\s+", " ", a.text(strip=True)) or url.rsplit("/", 1)[-1]
        m = _DATE.search(title)
        d = None
        if m:
            y = int(m.group(3))
            try:
                d = datetime(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2))).date()
            except ValueError:
                d = None
        out.append({"url": url, "title": title, "date": d})
    return out


class TreasuryDebtLimit(SourceAdapter):
    doc_type = "treasury_debt_limit"

    def parse(self, res: FetchResult, url_name: str) -> list[RawItem]:
        return [RawItem(source_id=self.cfg.id, ext_id=r["url"].rsplit("/", 1)[-1], url=r["url"], title=r["title"],
                        source_published_at=datetime.combine(r["date"], time(0), tzinfo=ET) if r["date"] else None,
                        published_raw=r["date"].isoformat() if r["date"] else None, document_id=res.document_id,
                        meta={"route_hint": WRAP, "scope_reason": "Treasury debt-limit document", "date_only": True,
                              "kind": "Debt limit"})
                for r in parse_debt_limit(res.content)]

    def digest_body(self, it: RawItem) -> content.Body:
        r = self.ctx.fetcher.get_with_retry(it.url, tries=3, base_delay=4, source_id=self.cfg.id,
                                            doc_type="digest:treasury_debt_limit_pdf", expect="pdf",
                                            conditional=False)
        text = content.pdf_body(r.content)
        notes = [] if len(text) > 200 else ["The PDF has no text layer (scanned image); see the PDF."]
        return content.Body(text, content._paragraphs(text), it.url, it.url, r.document_id, notes=notes)
