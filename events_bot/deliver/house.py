"""Events-bot pieces on top of the BAC house design system (deliver/design.py).

design.py is a byte-identical vendored copy of Z:/Data Pipelines/nse-announcements-pipeline/
reports/design.py (the module the daily deals and announcements e-mails to bac-reports render
through). Do not edit it here; port upstream changes by copying the file again. Anything the
events e-mails need beyond it lives in this module, built only from design.py's tokens, so no
colour or size literal appears in a report's own source.
"""
from __future__ import annotations

from html import escape as _e

from . import design as d

ROMAN = ["", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
KICKER = "RAAS Research Capital &middot; Events Desk"
DISCLAIMER = ("Every figure is quoted verbatim from the official release and checked against it before "
              "sending; values marked computed are arithmetic on those quoted figures. A field that fails "
              "the check is shown as EXTRACTION FAILED, never as a number. For information only &mdash; "
              "not investment advice. Write to bac@brindco.com with corrections.")


def section_caption(num: str, title: str, standfirst: str = "") -> str:
    """Gold uppercase caption with an optional grey standfirst (same as the announcements e-mail)."""
    head = f"{num} &middot; {title}" if num else title
    out = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="{d.font(10.5, color=d.GOLD, weight="bold", ls=1.6, upper=True)}'
        f'padding:0 0 {"6px" if standfirst else "14px"} 0;">{head}</td></tr>'
    )
    if standfirst:
        out += (f'<tr><td style="{d.font(10.5, color=d.INK_FAINT)}padding:0 0 14px 0;">'
                f'{standfirst}</td></tr>')
    return out + "</table>"


def prose(html: str, *, size: float = 12.5, leading: float = 19) -> str:
    """A block of body text in a table cell (padding survives Word)."""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="{d.font(size, leading=leading)}text-align:left;">{html}</td></tr></table>')


def links(docs) -> str:
    """Source documents as a label/value strip."""
    from urllib.parse import urlparse
    return d.strip([(_e(doc.name), f'<a href="{_e(doc.url)}" style="color:{d.NAVY_SOFT};">'
                                   f'Open on {_e(urlparse(doc.url).netloc.removeprefix("www."))}</a>')
                    for doc in docs])


def failed_panel(failed: list[str]) -> str:
    items = "".join(f"<br />&bull; {_e(f)}" for f in failed)
    return d.callout(d.value("EXTRACTION FAILED", "bad") + " &mdash; these fields are not shown as numbers. "
                     "Check the source documents below." + items, accent="navy", title="Extraction check")


def redline_block(html: str) -> str:
    """The redline paragraphs (already escaped, with styled <del>/<ins>) in a navy-edged panel."""
    paras = html.replace("<p>", f'<p style="margin:0 0 9px 0;{d.font(12, leading=18)}">')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-left:4px solid {d.RULE_STRONG};">'
            f'<tr><td style="padding:2px 0 2px 12px;">{paras}</td></tr></table>')


DEL_STYLE = f"color:{d.BAD};text-decoration:line-through;"
INS_STYLE = f"color:{d.GOOD};text-decoration:underline;"
