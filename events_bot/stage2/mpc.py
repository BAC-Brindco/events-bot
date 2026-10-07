"""RBI MPC Stage 2: the detailed meeting review, in the BAC house style.

Built so a reader who missed the meeting understands what happened and why, in RBI's own words:
every paragraph is quoted verbatim from the official resolution, the Statement on Developmental and
Regulatory Policies, or the Governor's Statement (PDF). The only non-quoted numbers are the
prior-vs-current changes, which are arithmetic on extracted values and are labelled as computed.
"""
from __future__ import annotations

import re
from datetime import date
from html import escape as _e

from ..core.models import Message
from ..deliver import design as dz
from ..deliver import house as hs
from ..diff.redline import redline
from ..extract import rbi_docs
from ..extract.base import Extraction
from ..extract.validator import good
from ..stage1.mpc import FAILED, RATES, _body, _period_key, headline, vote_text
from ..stage1.render import Doc, _provenance, change

GOV_SKIP = {"Decisions of the Monetary Policy Committee", "Growth", "Inflation"}   # already covered by the resolution
COMPUTED = "Change is computed: current minus prior, both as extracted from the two resolutions."


def _paras(ps: list[str]) -> str:
    rows = "".join(f'<tr><td style="{dz.font(12.5, leading=19)}text-align:justify;padding:0 0 10px 0;">'
                   f'{_e(p)}</td></tr>' for p in ps)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>'


def _section(n: int, title: str, standfirst: str, body: str) -> str:
    return dz.row(hs.section_caption(hs.ROMAN[n] if n < len(hs.ROMAN) else str(n), title, standfirst) + body,
                  pad=dz.SECTION_PAD)


def _proj_table(ex, prior_ex, var: str, prior_label: str, label: str) -> str:
    rows = []
    for e in sorted((e for e in ex if e.field.startswith(f"mpc.proj.{var}.")),
                    key=lambda e: _period_key(e.field.split(".", 3)[3])):
        per = e.field.split(".", 3)[3]
        p = good(prior_ex or [], e.field)
        rows.append([_e(per.replace("FY", "FY ")), dz.value(_e(p.value_text) if p else ""),
                     f"<strong>{_e(e.value_text) if e.ok else dz.value(FAILED, 'bad')}</strong>",
                     dz.value(change(p, e) if e.ok else "")])
    if not rows:
        return ""
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            '<tr><td style="padding:8px 0 0 0;">'
            + dz.datatable([f"{_e(label)}, period", _e(prior_label), "Current", "Change*"], rows,
                           align=["l", "r", "r", "r"], source="Reserve Bank of India &middot; MPC resolutions",
                           caption="* " + COMPUTED) + "</td></tr></table>")


def build(ref: str, ex: list[Extraction], text: str, prior_ex: list[Extraction] | None, prior_text: str | None,
          prior_label: str | None, docs: list[Doc], *, transcript_status: str, sdrp_text: str | None = None,
          governor_pdf: bytes | None = None, market_line: str | None = None, mode: str = "live") -> Message:
    prior_label = prior_label or "Prior"
    title = headline(ex)
    secs = dict(rbi_docs.resolution_sections(text))
    closing = rbi_docs.resolution_closing(text)
    decision = secs.get("Monetary Policy Decisions", [])
    rationale = [p for p in secs.get("Rationale for Monetary Policy Decisions", [])
                 if p not in closing and not re.search(r"were of the view|was of the view", p)]
    growth, inflation = rbi_docs.split_growth_inflation(secs.get("Domestic Outlook", []))
    global_ = secs.get("Global Outlook", [])
    gov: list[tuple[str, list[str]]] = []
    if governor_pdf:
        try:
            gov = rbi_docs.governor_sections(governor_pdf)
        except Exception:  # noqa: BLE001  a broken PDF must not stop the review
            gov = []
    measures = rbi_docs.sdrp_measures(sdrp_text) if sdrp_text else []

    body = dz.masthead(kicker=hs.KICKER, title="MPC Review",
                       dateline=f"{date.fromisoformat(ref.split(':', 1)[1]):%a %d-%b-%Y} &middot; Detailed meeting review",
                       subline=f"Reserve Bank of India &middot; compared with the {_e(prior_label)} meeting",
                       scope="Everything below is quoted from the official documents: the MPC resolution, the "
                             "Governor's Statement and the Statement on Developmental and Regulatory Policies. "
                             "Changes against the prior meeting are computed from the quoted figures and marked.")
    body += dz.row(dz.callout(f"<strong>{_e(title)}</strong>", accent="gold", title="The decision"), pad=dz.BLOCK_PAD)

    cards = []
    for field, label in RATES:
        cur, pri = good(ex, field), good(prior_ex or [], field)
        if cur or pri:
            ch = change(pri, cur)
            cards.append({"label": _e(label), "value": _e(cur.value_text) if cur else dz.value(FAILED, "bad"),
                          "sub": (f"per cent &middot; was {_e(pri.value_text)}, change {_e(ch)} (computed)"
                                  if pri else "per cent")})
    st, st_p = good(ex, "mpc.stance"), good(prior_ex or [], "mpc.stance")
    cards.append({"label": "Stance", "value": _e(st.value_text) if st else dz.value(FAILED, "bad"),
                  "sub": f"was {_e(st_p.value_text)}" if st_p else ""})
    v, note = vote_text(ex)
    cards.append({"label": "Rate vote", "value": _e(v), "sub": _e(note)})
    body += dz.row(dz.kpi_grid(cards, per_row=3), pad=dz.BLOCK_PAD)

    n = 0
    if decision:
        n += 1
        body += _section(n, "What the MPC decided", "MPC resolution, verbatim", _paras(decision))
    if rationale:
        n += 1
        body += _section(n, "Why: the MPC's rationale", "MPC resolution, verbatim", _paras(rationale))
    if growth:
        n += 1
        body += _section(n, "Growth assessment", "MPC resolution, verbatim",
                         _paras(growth) + _proj_table(ex, prior_ex, "gdp", prior_label, "Real GDP growth"))
    if inflation:
        n += 1
        tbl = (_proj_table(ex, prior_ex, "cpi", prior_label, "CPI inflation")
               + _proj_table(ex, prior_ex, "core_cpi", prior_label, "Core inflation"))
        body += _section(n, "Inflation assessment", "MPC resolution, verbatim", _paras(inflation) + tbl)
    if global_:
        n += 1
        body += _section(n, "Global backdrop", "MPC resolution, verbatim", _paras(global_))

    n += 1
    d_now = [(e.value_text, e.meta["view"], e.field) for e in ex if e.field.endswith("_dissent") and e.ok]
    d_prior = {e.value_text for e in (prior_ex or []) if e.field.endswith("_dissent") and e.ok}
    v_p, _ = vote_text(prior_ex) if prior_ex else ("", "")
    vote_html = dz.strip([("This meeting", f"<strong>{_e(v)}</strong> on the policy rate"),
                          (_e(prior_label), _e(v_p))])
    items = [(("Stance dissent: " if f == "mpc.stance_dissent" else "Rate dissent: ") + _e(who),
              _e(view) + (" (also dissented at the prior meeting)" if who in d_prior else " (new dissent)"))
             for who, view, f in d_now]
    members = [e.value_text for e in ex if e.field == "mpc.member" and e.ok]
    if members:
        items.append(("Members:", _e(", ".join(members)) + "."))
    vote_html += ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
                  '<tr><td style="padding:14px 0 0 0;">' + dz.numbered_list(items) + "</td></tr></table>")
    body += _section(n, "Vote and dissent", "", vote_html)

    if gov:
        n += 1
        g_html = ""
        for head, ps in gov:
            if head in GOV_SKIP:
                continue
            g_html += (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
                       f'<tr><td style="{dz.font(11.5, color=dz.NAVY, weight="bold")}padding:6px 0 6px 0;">'
                       f'{_e(head)}</td></tr></table>' + _paras(ps))
        body += _section(n, "Governor's Statement", "by topic, verbatim; growth and inflation are covered above",
                         g_html)
    elif any("Governor" in d.name for d in docs):
        n += 1
        body += _section(n, "Governor's Statement", "", dz.callout(
            "The Governor's Statement PDF was not yet available when this was compiled; see the link below.",
            accent="navy"))

    if measures:
        n += 1
        m_items = [(_e(t) + ".", f'<span style="color:{dz.INK_FAINT};">{_e(a)}</span> &mdash; {_e(d)}')
                   for a, t, d in measures]
        body += _section(n, "Developmental and regulatory measures",
                         "Statement on Developmental and Regulatory Policies, verbatim",
                         dz.callout(dz.numbered_list(m_items), accent="gold"))

    nxt = list(closing) + [transcript_status]
    n += 1
    body += _section(n, "What happens next", "", dz.callout("<br /><br />".join(_e(x) for x in nxt), accent="navy"))
    if market_line:
        body += dz.row(dz.callout(_e(market_line), accent="navy", title="Market reaction"), pad=dz.BLOCK_PAD)

    if prior_text:
        red_html, stats = redline(_body(prior_text), _body(text))
        red = red_html.replace("<del>", f'<del style="{hs.DEL_STYLE}">').replace("<ins>", f'<ins style="{hs.INS_STYLE}">')
        n += 1
        body += _section(n, f"Wording changes in the policy paragraphs vs {_e(prior_label)}",
                         f"{stats.changed} sentences changed &middot; {stats.added} added &middot; "
                         f"{stats.removed} removed", hs.redline_block(red))

    body += dz.row(hs.section_caption("", "Source documents") + hs.links(docs), pad=dz.SECTION_PAD)
    body += dz.row(dz.colophon(_provenance("Reserve Bank of India", ref, mode), hs.DISCLAIMER),
                   pad="26px 24px 26px 24px")
    subject = f"[RBI] MPC review | {title}"
    html = dz.doc_open(_e(subject), _e(title)) + body + dz.DOC_CLOSE

    lines = [title, ""]
    for h, ps in (("What the MPC decided", decision), ("Why", rationale), ("Growth", growth),
                  ("Inflation", inflation), ("Global backdrop", global_)):
        if ps:
            lines += [h.upper()] + ps + [""]
    lines += ["VOTE: " + v] + [f"- {who}: {view}" for who, view, _ in d_now] + [""]
    for head, ps in gov:
        if head not in GOV_SKIP:
            lines += [f"GOVERNOR - {head.upper()}"] + ps + [""]
    for a, t, d in measures:
        lines += [f"MEASURE ({a}): {t}", d, ""]
    lines += ["NEXT"] + nxt + [""] + [f"{d.name}: {d.url}" for d in docs]
    return Message(ref=ref, stage="stage2", kind="realtime", subject=subject, body_html=html,
                   body_text="\n".join(lines))
