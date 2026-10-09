"""Daily Macro Digest e-mail, in the house template (deliver/design.py + deliver/house.py).

One write-up per release, grouped by section. Every sentence in an item is quoted from the release:
the header facts (reference, effective date, addressees) and the "What it says" paragraphs are exact
lines of the archived document's text, and each key figure is an extraction validated in place.
A paragraph longer than PARA_CAP is cut at a sentence end and marked "[…]"; the shown part is still
verbatim. The only words the bot adds are labels and section captions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape as _e

from ..core.models import Message
from ..core.timeutil import fmt_ist, utcnow
from ..deliver import design as dz
from ..deliver import house as hs
from .content import Body
from .select import Figure, sentences

SECTIONS = ["Government", "RBI", "SEBI", "Data", "Weather", "Index changes"]
SECTION_OF = {"pib": "Government", "cbic": "Government", "rbi_pr": "RBI", "rbi_notif": "RBI",
              "sebi_circ": "SEBI", "sebi_pr": "SEBI", "mospi": "Data", "oea": "Data", "imd": "Weather",
              "nifty_indices": "Index changes"}
STANDFIRST = {
    "Government": "Press Information Bureau releases and CBIC duty / GST rate notifications",
    "RBI": "Directions, circulars and reports of the Reserve Bank of India",
    "SEBI": "Market-wide circulars, consultation papers and Board decisions",
    "Data": "Statistical releases (the CPI / IIP / GDP / WPI / core prints go out as separate alerts)",
    "Weather": "India Meteorological Department monsoon and seasonal forecasts",
    "Index changes": "Nifty 50 and Nifty Bank constituent changes",
}
ISSUER = {"pib": "Government of India (PIB)", "cbic": "CBIC", "rbi_pr": "Reserve Bank of India",
          "rbi_notif": "Reserve Bank of India", "sebi_circ": "SEBI", "sebi_pr": "SEBI", "mospi": "MoSPI / NSO",
          "oea": "DPIIT Office of the Economic Adviser", "imd": "India Meteorological Department",
          "nifty_indices": "NSE Indices"}
PARA_CAP = 1100


@dataclass
class DigestItem:
    item_id: int
    ref: str
    source_id: str
    title: str
    url: str
    kind: str
    published: datetime | None
    published_raw: str | None
    date_only: bool
    first_seen: datetime
    tags: list[str]
    meta: dict
    body: Body | None = None
    error: str | None = None
    paras: list[str] = field(default_factory=list)
    figs: list[Figure] = field(default_factory=list)
    who: list[str] = field(default_factory=list)
    eff: str | None = None
    refs: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)          # NSE Indices benchmark rows
    priority: int = 0
    trimmed: bool = False

    @property
    def section(self) -> str:
        return SECTION_OF.get(self.source_id, "Government")


def cap(p: str, limit: int = PARA_CAP) -> str:
    """A verbatim prefix of `p` ending at a sentence, at most `limit` chars (plus the marker)."""
    if len(p) <= limit:
        return p
    out = ""
    for s in sentences(p):
        cand = f"{out} {s}" if out else s
        if len(cand) > limit:
            break
        out = cand
    if len(out) < limit // 3 or out not in p:        # one very long first sentence: cut at a word
        out = p[:limit].rsplit(" ", 1)[0]
    return out + " […]"


def shown(p: str, limit: int = PARA_CAP) -> str:
    """The verbatim part of a displayed paragraph (without the cut marker)."""
    s = cap(p, limit)
    return s[:-4] if s.endswith(" […]") else s


def group_figures(figs: list[Figure]) -> list[tuple[str, list[str]]]:
    """One row per sentence, with every figure quoted from it."""
    out: dict[str, list[str]] = {}
    for f in figs:
        out.setdefault(f.context, []).append(f.ex.value_text)
    return list(out.items())


def _posted(it: DigestItem) -> str:
    if it.published is None:
        return _e(it.published_raw or "") or f"first seen {fmt_ist(it.first_seen)}"
    if it.date_only:
        return f"{_e(it.published_raw or it.published.strftime('%d %b %Y'))} (date only) &middot; first seen {fmt_ist(it.first_seen)}"
    return fmt_ist(it.published)


def _paras_html(ps: list[str], limit: int) -> str:
    # One cell carries the font; the paragraphs inside only set their spacing (keeps a long digest
    # under Gmail's clipping size without dropping text).
    inner = "".join(f'<p style="margin:0 0 9px 0;">{_e(cap(p, limit))}</p>' for p in ps)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="{dz.font(12.5, leading=19)}text-align:justify;">{inner}</td></tr></table>')


def _fig_table(rows: list[tuple[str, list[str]]], source: str) -> str:
    """Key figures: the house table look (navy head, hairlines) with the font set once on the table."""
    head = (f'<tr><th align="left" width="110" style="width:110px;color:{dz.NAVY};font-size:9.5px;letter-spacing:0.8px;'
            f'text-transform:uppercase;background-color:{dz.BAND};border-top:2px solid {dz.NAVY};'
            f'border-bottom:1px solid {dz.RULE_STRONG};padding:6px;">Figure</th>'
            f'<th align="left" style="color:{dz.NAVY};font-size:9.5px;letter-spacing:0.8px;text-transform:uppercase;'
            f'background-color:{dz.BAND};border-top:2px solid {dz.NAVY};border-bottom:1px solid {dz.RULE_STRONG};'
            f'padding:6px;">In the release</th></tr>')
    body = "".join(
        f'<tr><td valign="top" style="border-bottom:1px solid {dz.ROW_RULE};padding:5px 6px;">'
        f'{"<br />".join(f"<b>{_e(v)}</b>" for v in vals)}</td>'
        f'<td valign="top" style="border-bottom:1px solid {dz.ROW_RULE};padding:5px 6px;">{_e(cap(ctx, 320))}</td></tr>'
        for ctx, vals in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;{dz.font(12, leading=16)}">{head}{body}</table>'
            f'<div style="{dz.font(10.5, color=dz.INK_FAINT)}padding:6px 0 0 0;">{source}</div>')


def _label(text: str) -> str:
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="{dz.font(9.5, color=dz.GOLD, weight="bold", ls=1.2, upper=True)}padding:10px 0 6px 0;">'
            f'{text}</td></tr></table>')


def item_html(it: DigestItem, n: int, limit: int = PARA_CAP) -> str:
    kind = it.kind or "Release"
    head = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="border-top:1px solid {dz.RULE_STRONG};padding:16px 0 4px 0;">'
            f'<div style="{dz.font(9.5, color=dz.INK_FAINT, weight="bold", ls=1.1, upper=True)}">'
            f'{n}. {_e(ISSUER.get(it.source_id, it.source_id))} &middot; {_e(kind)}</div>'
            f'<div style="{dz.font(15, color=dz.NAVY, weight="bold", leading=20)}padding-top:3px;">{_e(it.title)}</div>'
            f'</td></tr></table>')
    facts = [("Posted", _posted(it))]
    if it.refs:
        facts.append(("Reference", _e(" · ".join(it.refs))))
    if it.eff:
        facts.append(("Effective", _e(it.eff)))
    out = head + dz.strip(facts)
    if it.error:
        out += dz.callout(f"The full text could not be fetched when the digest was compiled "
                          f"({_e(it.error[:160])}). Open the release from the link below.", accent="navy")
        if it.paras:
            out += _label("Opening of the release, verbatim") + _paras_html(it.paras, limit)
    else:
        if it.paras:
            out += _label("What it says &middot; verbatim") + _paras_html(it.paras, limit)
        if it.trimmed:
            out += (f'<div style="{dz.font(10.5, color=dz.INK_FAINT, italic=True)}padding:0 0 6px 0;">'
                    f'Shortened to the leading paragraphs to keep this e-mail under Gmail\'s size limit; '
                    f'the full text is at the link below.</div>')
        for note in (it.body.notes if it.body else []):
            out += f'<div style="{dz.font(10.5, color=dz.INK_FAINT, italic=True)}padding:0 0 6px 0;">{_e(note)}</div>'
    if it.rows:
        out += _label("Benchmark index changes, as printed") + dz.datatable(
            ["Index", "Action", "Company", "Symbol"],
            [[_e(r["index"]), _e(r["action"]), _e(r["company"]), f"<strong>{_e(r['symbol'])}</strong>"] for r in it.rows],
            align=["l", "l", "l", "l"], source="NSE Indices press release (PDF)")
    if it.figs:
        out += _label("Key figures &middot; each quoted with its sentence") + _fig_table(
            group_figures(it.figs), _e(ISSUER.get(it.source_id, "")))
    if it.who:
        out += ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
                '<tr><td style="padding:10px 0 0 0;">'
                + dz.callout("<br />".join(_e(w) for w in it.who), accent="navy", title="Who it applies to")
                + "</td></tr></table>")
    links = [("Release", f'<a href="{_e(it.url)}" style="color:{dz.NAVY_SOFT};">{_e(it.url[:90])}</a>')]
    pdf = it.body.pdf_url if it.body else None
    if pdf and pdf != it.url:
        links.append(("PDF", f'<a href="{_e(pdf)}" style="color:{dz.NAVY_SOFT};">{_e(pdf[:90])}</a>'))
    out += ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            '<tr><td style="padding:10px 0 4px 0;">' + dz.strip(links) + "</td></tr></table>")
    return out


def item_text(it: DigestItem, n: int, limit: int = PARA_CAP) -> list[str]:
    lines = [f"{n}. {it.title}", f"   {ISSUER.get(it.source_id, it.source_id)} | {it.kind}"]
    if it.refs:
        lines.append("   Reference: " + " · ".join(it.refs))
    if it.eff:
        lines.append("   Effective: " + it.eff)
    if it.error:
        lines.append(f"   (full text not fetched: {it.error[:120]})")
    lines += [f"   - {cap(p, limit)}" for p in it.paras]
    lines += [f"   * {r['index']}: {r['action']} {r['company']} ({r['symbol']})" for r in it.rows]
    lines += [f"   # {', '.join(vals)}: {cap(ctx, 240)}" for ctx, vals in group_figures(it.figs)]
    lines += [f"   Applies to: {w}" for w in it.who]
    lines.append(f"   {it.url}")
    return lines + [""]


def build(items: list[DigestItem], *, ref: str, day: datetime, period_start: datetime, period_end: datetime,
          mode: str, sample: bool = False, limit: int = PARA_CAP, part: tuple[int, int] = (1, 1),
          start_no: int = 1, total_items: int | None = None) -> Message:
    """One e-mail. A long day is split into parts (each under Gmail's clip size); `part` is (i, n), the
    numbering continues across parts from `start_no`."""
    by_sec = {s: [i for i in items if i.section == s] for s in SECTIONS}
    secs = [s for s in SECTIONS if by_sec[s]]
    n_items = total_items if total_items is not None else len(items)
    title = f"{n_items} release{'s' if n_items != 1 else ''}"
    if part[1] == 1:
        title += f" across {len(secs)} section{'s' if len(secs) != 1 else ''}"
    else:
        title += f" &middot; part {part[0]} of {part[1]} (items {start_no}&ndash;{start_no + len(items) - 1})"
    title_plain = title.replace("&middot;", "·").replace("&ndash;", "-")
    body = dz.masthead(
        kicker=hs.KICKER, title="Daily Macro Digest",
        dateline=f"{day:%a %d-%b-%Y} &middot; {title}",
        subline=f"Releases first seen {fmt_ist(period_start)} to {fmt_ist(period_end)}",
        scope="Government, RBI, SEBI, statistical, weather and benchmark-index releases that can move the macro "
              "picture. Each item quotes the release itself: the operative paragraphs, the figures with the "
              "sentence they appear in, who it applies to and when it takes effect. Data prints (CPI, IIP, GDP, "
              "WPI, core industries) and FOMC / RBI MPC decisions are sent as separate detailed alerts.")
    # contents
    contents = []
    k = start_no - 1
    for s in secs:
        names = []
        for it in by_sec[s]:
            k += 1
            names.append(f"{k}. {_e(it.title)}")
        contents.append((f"{_e(s)} ({len(by_sec[s])})", "<br />".join(names)))
    body += dz.row(hs.section_caption("", "Contents") + dz.strip(contents), pad=dz.BLOCK_PAD)

    k = start_no - 1
    lines = [f"Daily Macro Digest - {day:%a %d-%b-%Y} - {title_plain}", ""]
    for si, s in enumerate(secs, 1):
        html = ""
        lines += [s.upper(), ""]
        for it in by_sec[s]:
            k += 1
            html += item_html(it, k, limit)
            lines += item_text(it, k, limit)
        body += dz.row(hs.section_caption(hs.ROMAN[si] if si < len(hs.ROMAN) else str(si), s, STANDFIRST[s]) + html,
                       pad=dz.SECTION_PAD)

    now = utcnow()
    prov = (f"Compiled by the RAAS Events Bot at {fmt_ist(now)}. Sources: the official releases linked under each "
            f"item. Ref {_e(ref)}.")
    if mode != "live":
        prov += f" {dz.value(mode.upper().replace('_', ' ') + ' — not sent', 'warn')}"
    if sample:
        prov += f" {dz.value('SAMPLE for review — not sent to the desk', 'warn')}"
    body += dz.row(dz.colophon(prov, hs.DISCLAIMER), pad="26px 24px 26px 24px")
    subject = f"[MACRO] Daily digest | {day:%d-%b-%Y} | {n_items} release{'s' if n_items != 1 else ''}"
    if part[1] > 1:
        subject += f" | part {part[0]} of {part[1]}"
    if sample:
        subject = "SAMPLE " + subject
    html = dz.doc_open(_e(subject), _e(title_plain)) + body + dz.DOC_CLOSE
    return Message(ref=ref if part[0] == 1 else f"{ref}:part{part[0]}", stage="digest", kind="india_eod",
                   subject=subject, body_html=html, body_text="\n".join(lines))
