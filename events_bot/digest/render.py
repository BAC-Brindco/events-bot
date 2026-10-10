"""Daily Macro Digest e-mail, in the house template (deliver/design.py + deliver/house.py).

One write-up per release, grouped by section, each in its own card. Every sentence in an item is quoted
from the release: the header facts (reference, effective date, addressees) and the "What it says"
paragraphs are exact lines of the archived document's text, and each key figure is an extraction validated
in place, shown with the short clause around it (a verbatim slice of its sentence). A paragraph longer than
PARA_CAP is cut at a sentence end and marked "[…]"; the shown part is still verbatim. The only words the
bot adds are labels and section captions; figures are set in bold, which changes no text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from html import escape as _e

from ..core.models import Message
from ..core.timeutil import fmt_ist, utcnow
from ..deliver import design as dz
from ..deliver import house as hs
from .content import Body
from .select import _FIGURE_RX, Figure, sentences

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

# Styles used many times per e-mail: set once here, from design.py tokens only.
_TBL = 'role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
_CARD = f"border:1px solid {dz.CARD_BORDER};border-top:3px solid {dz.NAVY};background-color:{dz.PAPER};"
_LABEL = dz.font(9, color=dz.INK_FAINT, weight="bold", ls=1.1, upper=True)
_CAP = dz.font(9.5, color=dz.GOLD, weight="bold", ls=1.3, upper=True)
_MARK = f'<span style="color:{dz.GOLD};">&#9656;</span>&nbsp;'
_LINK = f"color:{dz.NAVY_SOFT};font-weight:bold;text-decoration:none;"


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


def group_figures(figs: list[Figure]) -> list[tuple[str, list[str], bool, bool]]:
    """One row per clause, with every figure quoted from it: (clause, values, cut_left, cut_right)."""
    out: dict[str, tuple[list[str], bool, bool]] = {}
    for f in figs:
        key = f.clause or f.context
        if key in out:
            out[key][0].append(f.ex.value_text)
        else:
            out[key] = ([f.ex.value_text], f.cut_left if f.clause else False, f.cut_right if f.clause else False)
    return [(k, v[0], v[1], v[2]) for k, v in out.items()]


def bold_figures(text: str) -> str:
    """Escape `text`, setting every figure in bold. The characters are unchanged."""
    out, i = [], 0
    for m in _FIGURE_RX.finditer(text):
        if not re.search(r"\d", m.group(0)):
            continue
        out.append(_e(text[i:m.start()]))
        out.append(f"<b>{_e(m.group(0))}</b>")
        i = m.end()
    out.append(_e(text[i:]))
    return "".join(out)


def _ell(clause: str, left: bool, right: bool) -> str:
    return ("&hellip;&#8202;" if left else "") + bold_figures(clause) + ("&#8202;&hellip;" if right else "")


def _posted(it: DigestItem) -> str:
    if it.published is None:
        return _e(it.published_raw or "") or f"first seen {fmt_ist(it.first_seen)}"
    if it.date_only:
        return _e(it.published_raw or it.published.strftime("%d %b %Y"))
    return fmt_ist(it.published)


def _cell(label: str, value: str, colspan: int = 1, pad_right: bool = False) -> str:
    span = f' colspan="{colspan}"' if colspan > 1 else ""
    width = ' width="50%"' if colspan == 1 else ""
    return (f'<td valign="top"{span}{width} style="padding:7px {"12px" if pad_right else "0"} 0 0;">'
            f'<div style="{_LABEL}">{label}</div>'
            f'<div style="padding-top:2px;">{value}</div></td>')


def _meta(it: DigestItem) -> str:
    """Posted / reference side by side, then effective and applies-to across the card. Empty fields omitted."""
    short = [("Posted", _posted(it))]
    if it.refs:
        short.append(("Reference", _e(" · ".join(it.refs))))
    rows = "<tr>" + "".join(_cell(lab, val, pad_right=i == 0 and len(short) > 1)
                            for i, (lab, val) in enumerate(short))
    rows += ("" if len(short) > 1 else "<td></td>") + "</tr>"
    if it.eff:
        rows += "<tr>" + _cell("Takes effect", bold_figures(it.eff), colspan=2) + "</tr>"
    if it.who:
        rows += "<tr>" + _cell("Applies to", "<br />".join(_e(w) for w in it.who), colspan=2) + "</tr>"
    return (f'<table {_TBL} style="background-color:{dz.BAND};margin-top:10px;">'
            f'<tr><td style="padding:3px 12px 9px 12px;{dz.font(12, color=dz.INK_SOFT, leading=17)}">'
            f'<table {_TBL}>{rows}</table></td></tr></table>')


def _caption(text: str, top: int = 14) -> str:
    return f'<div style="{_CAP}padding:{top}px 0 7px 0;">{text}</div>'


# PDF bullet glyphs (private-use code points render as boxes) at the start of a line; dropping a prefix
# keeps the shown text a verbatim slice of the source.
_LEAD_JUNK = re.compile("^[\\s-•●▪■□◆◦❖➢✓·]+")


def lead_trim(p: str) -> str:
    return _LEAD_JUNK.sub("", p)


def _paras_html(ps: list[str], limit: int) -> str:
    # One cell carries the font; each paragraph only sets its spacing (keeps a long digest under Gmail's
    # clipping size without dropping text).
    inner = "".join(
        f'<p style="margin:0 0 {"0" if i == len(ps) - 1 else "11px"} 0;">{_MARK}'
        f'{bold_figures(lead_trim(cap(p, limit)))}</p>'
        for i, p in enumerate(ps))
    return (f'<table {_TBL}><tr><td style="{dz.font(14, leading=21)}text-align:left;">{inner}</td></tr></table>')


def _figures_html(rows: list[tuple[str, list[str], bool, bool]]) -> str:
    """Key figures: each figure in bold beside the short clause it sits in (verbatim)."""
    body = "".join(
        f'<tr><td valign="top" width="118" style="width:118px;padding:6px 10px 6px 0;color:{dz.NAVY};'
        f'font-weight:bold;{"" if i == 0 else f"border-top:1px solid {dz.ROW_RULE};"}">'
        f'{"<br />".join(_e(v) for v in vals)}</td>'
        f'<td valign="top" style="padding:6px 0;color:{dz.INK_SOFT};'
        f'{"" if i == 0 else f"border-top:1px solid {dz.ROW_RULE};"}">{_ell(cl, lft, rgt)}</td></tr>'
        for i, (cl, vals, lft, rgt) in enumerate(rows))
    return (f'<table {_TBL} style="{dz.font(12.5, leading=18)}border-top:1px solid {dz.RULE};'
            f'border-bottom:1px solid {dz.RULE};">{body}</table>')


def _links(it: DigestItem) -> str:
    links = [f'<a href="{_e(it.url)}" style="{_LINK}">Read release&nbsp;&rarr;</a>']
    pdf = it.body.pdf_url if it.body else None
    if pdf and pdf != it.url:
        links.append(f'<a href="{_e(pdf)}" style="{_LINK}">PDF&nbsp;&rarr;</a>')
    sep = f'&nbsp;&nbsp;<span style="color:{dz.RULE_STRONG};">|</span>&nbsp;&nbsp;'
    return f'<div style="{dz.font(12)}padding-top:12px;">{sep.join(links)}</div>'


def item_html(it: DigestItem, n: int, limit: int = PARA_CAP) -> str:
    kind = it.kind or "Release"
    issuer = ISSUER.get(it.source_id, it.source_id)
    head = (f'<div style="{_LABEL}">'
            f'<span style="color:{dz.GOLD};">{n}</span>{dz.badge(it.section.upper())}&nbsp;&nbsp;'
            f'{_e(issuer)}{f" &middot; {_e(kind)}" if kind and kind != issuer else ""}</div>'
            f'<div style="{dz.font(16.5, color=dz.NAVY, weight="bold", leading=21.5)}padding-top:6px;">'
            f'{_e(it.title)}</div>')
    out = head + _meta(it)
    if it.error:
        out += (f'<table {_TBL}><tr><td style="padding-top:12px;">'
                + dz.callout(f"The full text could not be fetched when the digest was compiled "
                             f"({_e(it.error[:160])}). Open the release from the link below.", accent="navy")
                + "</td></tr></table>")
        if it.paras:
            out += _caption("Opening of the release &middot; verbatim") + _paras_html(it.paras, limit)
    else:
        if it.paras:
            out += _caption("What it says &middot; verbatim") + _paras_html(it.paras, limit)
        notes = list(it.body.notes if it.body else [])
        if it.trimmed:
            notes.insert(0, "Shortened to the leading paragraphs to keep this e-mail under Gmail's size limit; "
                            "the full text is at the link below.")
        for note in notes:
            out += f'<div style="{dz.font(11, color=dz.INK_FAINT, italic=True)}padding-top:8px;">{_e(note)}</div>'
    if it.rows:
        out += _caption("Benchmark index changes &middot; as printed") + dz.datatable(
            ["Index", "Action", "Company", "Symbol"],
            [[_e(r["index"]), _e(r["action"]), _e(r["company"]), f"<strong>{_e(r['symbol'])}</strong>"] for r in it.rows],
            align=["l", "l", "l", "l"], source="NSE Indices press release (PDF)")
    if it.figs:
        out += _caption("Key figures") + _figures_html(group_figures(it.figs))
    out += _links(it)
    return (f'<table {_TBL}><tr><td style="padding:0 0 16px 0;"><a name="i{n}" id="i{n}"></a>'
            f'<table {_TBL} style="{_CARD}"><tr><td style="padding:13px 16px 14px 16px;">{out}'
            f'</td></tr></table></td></tr></table>')


def item_text(it: DigestItem, n: int, limit: int = PARA_CAP) -> list[str]:
    lines = [f"{n}. {it.title}", f"   {ISSUER.get(it.source_id, it.source_id)} | {it.kind}"]
    lines.append("   Posted: " + re.sub(r"<[^>]+>", "", _posted(it)).replace("&middot;", "·"))
    if it.refs:
        lines.append("   Reference: " + " · ".join(it.refs))
    if it.eff:
        lines.append("   Takes effect: " + it.eff)
    lines += [f"   Applies to: {w}" for w in it.who]
    if it.error:
        lines.append(f"   (full text not fetched: {it.error[:120]})")
    lines += [f"   - {cap(p, limit)}" for p in it.paras]
    lines += [f"   * {r['index']}: {r['action']} {r['company']} ({r['symbol']})" for r in it.rows]
    lines += [f"   # {', '.join(vals)}: {'…' if lft else ''}{cl}{'…' if rgt else ''}"
              for cl, vals, lft, rgt in group_figures(it.figs)]
    lines.append(f"   Release: {it.url}")
    pdf = it.body.pdf_url if it.body else None
    if pdf and pdf != it.url:
        lines.append(f"   PDF: {pdf}")
    return lines + [""]


def _pill(text: str, *, solid: bool = False) -> str:
    style = (f"background-color:{dz.NAVY};color:{dz.PAPER};border:1px solid {dz.NAVY};" if solid else
             f"color:{dz.NAVY};border:1px solid {dz.RULE_STRONG};background-color:{dz.PAPER};")
    return (f'<span style="{dz.font(10, weight="bold", ls=0.8, upper=True)}{style}padding:2px 7px;'
            f'white-space:nowrap;">{text}</span>')


def _glance(counts: dict[str, int], scope: str) -> str:
    pills = "&nbsp; ".join(_pill(f"{_e(s)}&nbsp;&middot;&nbsp;{c}") for s, c in counts.items() if c)
    return (f'<div style="{_CAP}padding:0 0 8px 0;">At a glance</div>'
            f'<div style="{dz.font(10, leading=24)}">{pills}</div>'
            f'<div style="{dz.font(11.5, color=dz.INK_FAINT, leading=17)}text-align:left;padding-top:10px;">'
            f'{scope}</div>')


def _contents(secs: list[str], by_sec: dict[str, list[DigestItem]], start_no: int) -> str:
    out = f'<div style="{_CAP}padding:0 0 4px 0;">Contents</div>'
    k = start_no - 1
    for s in secs:
        links = []
        for it in by_sec[s]:
            k += 1
            links.append(f'<a href="#i{k}" style="color:{dz.INK};text-decoration:none;">'
                         f'<span style="color:{dz.GOLD};font-weight:bold;">{k}</span>&nbsp;&nbsp;{_e(it.title)}</a>')
        out += (f'<table {_TBL}><tr><td style="padding:10px 0 0 0;border-top:1px solid {dz.RULE};">'
                f'{_pill(f"{_e(s)}&nbsp;&middot;&nbsp;{len(by_sec[s])}", solid=True)}'
                f'<div style="{dz.font(12.5, leading=19)}padding:6px 0 10px 0;">{"<br />".join(links)}</div>'
                f'</td></tr></table>')
    return out


SCOPE = ("Government, RBI, SEBI, statistical, weather and benchmark-index releases that can move the macro "
         "picture. Each item quotes the release itself: the operative paragraphs, the key figures with the clause "
         "they appear in, who it applies to and when it takes effect. Data prints (CPI, IIP, GDP, WPI, core "
         "industries) and FOMC / RBI MPC decisions are sent as separate detailed alerts.")


def build(items: list[DigestItem], *, ref: str, day: datetime, period_start: datetime, period_end: datetime,
          mode: str, sample: bool = False, limit: int = PARA_CAP, part: tuple[int, int] = (1, 1),
          start_no: int = 1, total_items: int | None = None, section_counts: dict[str, int] | None = None,
          sample_label: str = "SAMPLE") -> Message:
    """One e-mail. A long day is split into parts (each under Gmail's clip size); `part` is (i, n), the
    numbering continues across parts from `start_no`. `section_counts` covers the whole digest (all parts)."""
    by_sec = {s: [i for i in items if i.section == s] for s in SECTIONS}
    secs = [s for s in SECTIONS if by_sec[s]]
    n_items = total_items if total_items is not None else len(items)
    counts = section_counts or {s: len(by_sec[s]) for s in SECTIONS}
    title = f"{n_items} release{'s' if n_items != 1 else ''}"
    if part[1] == 1:
        title += f" across {len(secs)} section{'s' if len(secs) != 1 else ''}"
    else:
        title += f" &middot; part {part[0]} of {part[1]} (items {start_no}&ndash;{start_no + len(items) - 1})"
    title_plain = title.replace("&middot;", "·").replace("&ndash;", "-")
    body = dz.masthead(
        kicker=hs.KICKER, title="Daily Macro Digest",
        dateline=f"{day:%a %d-%b-%Y} &middot; {title}",
        subline=f"Releases first seen {fmt_ist(period_start)} to {fmt_ist(period_end)}")
    body += dz.row(_glance(counts, SCOPE), pad=dz.BLOCK_PAD)
    body += dz.row(_contents(secs, by_sec, start_no), pad=dz.BLOCK_PAD)

    k = start_no - 1
    lines = [f"Daily Macro Digest - {day:%a %d-%b-%Y} - {title_plain}",
             "At a glance: " + " · ".join(f"{s} {c}" for s, c in counts.items() if c), ""]
    for si, s in enumerate(secs, 1):
        html = ""
        lines += [s.upper(), ""]
        for it in by_sec[s]:
            k += 1
            html += item_html(it, k, limit)
            lines += item_text(it, k, limit)
        body += dz.row(hs.section_caption(hs.ROMAN[si] if si < len(hs.ROMAN) else str(si), s, STANDFIRST[s]) + html,
                       pad=f"26px {dz.PAD_X}px 4px {dz.PAD_X}px")

    now = utcnow()
    prov = (f"Compiled by the RAAS Events Bot at {fmt_ist(now)}. Sources: the official releases linked under each "
            f"item. Ref {_e(ref)}.")
    if mode != "live":
        prov += f" {dz.value(mode.upper().replace('_', ' ') + ' — not sent', 'warn')}"
    if sample:
        prov += f" &middot; {dz.value('SAMPLE for review — not sent to the desk', 'warn')}"
    body += dz.row(dz.colophon(prov, hs.DISCLAIMER), pad="20px 24px 26px 24px")
    subject = f"[MACRO] Daily digest | {day:%d-%b-%Y} | {n_items} release{'s' if n_items != 1 else ''}"
    if part[1] > 1:
        subject += f" | part {part[0]} of {part[1]}"
    if sample:
        subject = f"{sample_label.strip() or 'SAMPLE'} {subject}"
    html = dz.doc_open(_e(subject), _e(title_plain)) + body + dz.DOC_CLOSE
    return Message(ref=ref if part[0] == 1 else f"{ref}:part{part[0]}", stage="digest", kind="india_eod",
                   subject=subject, body_html=html, body_text="\n".join(lines))
