"""FOMC Stage 2 (post-event summary). Pure: this and the prior meeting's extractions in.

Sections: statement redline vs the prior meeting; vote-split diff; projections table
(prior, current, change -- the change is arithmetic on two extracted values and is
labelled as computed); implementation-note settings vs prior; press conference
status; market reaction line (omitted when no market-data provider is configured).
"""
from __future__ import annotations

from ..core.models import Message
from ..diff.redline import redline
from ..extract.base import Extraction
from ..extract.validator import good
from ..stage1 import render
from ..stage1.fomc import FAILED, SEP_LABELS, Doc, headline, vote_line
from ..stage1.render import Table, change

SETTINGS = (("fomc.target_low", "Target range, lower bound"), ("fomc.target_high", "Target range, upper bound"),
            ("fomc.iorb", "Interest on reserve balances"), ("fomc.onrrp_rate", "ON RRP offering rate"),
            ("fomc.srp_rate", "Standing repo rate"), ("fomc.primary_credit", "Primary credit rate"),
            ("fomc.onrrp_cap_bn", "ON RRP per-counterparty limit ($bn/day)"))
COMPUTED = "Change is computed: current minus prior, both as extracted from the source documents."


def _names(ex: list[Extraction] | None, field: str) -> list[str]:
    return [e.value_text for e in (ex or []) if e.field == field and e.ok]


def vote_section(ex: list[Extraction], prior_ex: list[Extraction] | None) -> dict:
    now, now_note = vote_line(ex)
    prior, prior_note = vote_line(prior_ex) if prior_ex else ("", "")
    a_now, a_prior = _names(ex, "fomc.vote_against"), _names(prior_ex, "fomc.vote_against")
    prefs = {e.value_text: e.meta["preferred"] for e in ex if e.field == "fomc.vote_against" and e.ok}
    lines = [f"New dissent: {n}, preferred {prefs[n]}" for n in a_now if n not in a_prior]
    lines += [f"Dissented again: {n}, preferred {prefs[n]}" for n in a_now if n in a_prior]
    lines += [f"No longer dissenting: {n}" for n in a_prior if n not in a_now]
    if not lines:
        lines.append("No dissents at either meeting.")
    return {"now": now, "now_note": now_note, "prior": prior, "prior_note": prior_note, "lines": lines}


def build(meeting_ref: str, ex: list[Extraction], texts: dict[str, str], prior_ex: list[Extraction] | None,
          prior_texts: dict[str, str] | None, prior_label: str | None, docs: list[Doc], *,
          transcript_status: str, market_line: str | None = None, mode: str = "live") -> Message:
    from ..extract.fomc import statement_body

    red_html, red_stats = "", None
    if prior_texts and "statement" in prior_texts:
        red_html, red_stats = redline(statement_body(prior_texts["statement"]), statement_body(texts["statement"]))
    tables = []
    sep_rows = []
    for e in ex:
        if e.field.startswith("sep.median."):
            _, _, var, year = e.field.split(".")
            p = good(ex, f"sep.prior_median.{var}.{year}")
            sep_rows.append([SEP_LABELS.get(var, var), "Longer run" if year == "longer_run" else year,
                             p.value_text if p else "", e.value_text if e.ok else FAILED,
                             change(p, e) if e.ok else ""])
    if sep_rows:
        prior_sep = next((e.meta.get("label") for e in ex if e.field.startswith("sep.prior_median.")), None) or "Prior"
        tables.append(Table("Projections, medians", ["Variable", "Year", prior_sep, "Current", "Change*"],
                            sep_rows, "* " + COMPUTED, bold_col=4))
    settings = []
    for field, label in SETTINGS:
        cur, pri = good(ex, field), good(prior_ex or [], field)
        if cur or pri:
            settings.append([label, pri.value_text if pri else "", cur.value_text if cur else FAILED,
                             change(pri, cur)])
    tables.append(Table("Policy settings", ["", prior_label or "Prior", "Current", "Change*"], settings,
                        "* " + COMPUTED, bold_col=3))
    return render.stage2(ref=meeting_ref, subject=f"[FED] Stage 2 | FOMC {meeting_ref.split(':', 1)[1]}",
                         source_tag="FED", event_name="FOMC", title=headline(ex), redline_title="Statement redline",
                         prior_label=prior_label, redline=red_html, red_stats=red_stats,
                         vote=vote_section(ex, prior_ex), tables=tables, transcript_status=transcript_status,
                         market_line=market_line, docs=docs, mode=mode)
