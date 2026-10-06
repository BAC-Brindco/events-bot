"""FOMC Stage 2 (post-event summary). Pure: this and the prior meeting's extractions in.

Sections: statement redline vs the prior meeting; vote-split diff; projections table
(prior, current, change -- the change is arithmetic on two extracted values and is
labelled as computed); implementation-note settings vs prior; discussion points
from the press conference (transcript pending until a transcript source exists);
market reaction line (omitted when no market-data provider is configured).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..core.models import Message
from ..diff.redline import redline
from ..extract.base import Extraction
from ..extract.validator import good
from ..stage1.build import _env
from ..stage1.fomc import FAILED, SEP_LABELS, Doc, headline, vote_line

IMPL_FIELDS = (("fomc.target_low", "Target range, lower bound"), ("fomc.target_high", "Target range, upper bound"),
               ("fomc.iorb", "Interest on reserve balances"), ("fomc.onrrp_rate", "ON RRP offering rate"),
               ("fomc.srp_rate", "Standing repo rate"), ("fomc.primary_credit", "Primary credit rate"),
               ("fomc.onrrp_cap_bn", "ON RRP per-counterparty limit ($bn/day)"))


def _chg(a: Extraction | None, b: Extraction | None) -> str:
    """b - a, formatted to the larger number of decimals in the two source strings."""
    if not (a and b) or a.value_norm is None or b.value_norm is None:
        return ""
    d = b.value_norm - a.value_norm
    places = max(-a.value_norm.as_tuple().exponent, -b.value_norm.as_tuple().exponent, 0)
    q = Decimal(1).scaleb(-places) if places else Decimal(1)
    s = f"{d.quantize(q):+f}"
    return "0" if d == 0 else s


def _names(ex: list[Extraction], field: str) -> list[str]:
    return [e.value_text for e in ex if e.field == field and e.ok]


def build(meeting_ref: str, ex: list[Extraction], texts: dict[str, str], prior_ex: list[Extraction] | None,
          prior_texts: dict[str, str] | None, prior_label: str | None, docs: list[Doc], *,
          transcript_status: str, market_line: str | None = None, mode: str = "live") -> Message:
    from ..extract.fomc import statement_body

    head = headline(ex)
    subject = f"[FED] Stage 2 | FOMC {meeting_ref.split(':', 1)[1]}"
    red_html, red_stats = ("", None)
    if prior_texts and "statement" in prior_texts:
        red_html, red_stats = redline(statement_body(prior_texts["statement"]), statement_body(texts["statement"]))

    vote_now, note_now = vote_line(ex)
    vote_prior, note_prior = vote_line(prior_ex) if prior_ex else ("", "")
    against_now, against_prior = _names(ex, "fomc.vote_against"), _names(prior_ex or [], "fomc.vote_against")
    vote_diff = {
        "now": vote_now, "now_note": note_now, "prior": vote_prior, "prior_note": note_prior,
        "new_dissents": [n for n in against_now if n not in against_prior],
        "dropped_dissents": [n for n in against_prior if n not in against_now],
        "repeat_dissents": [n for n in against_now if n in against_prior],
        "prefs": {e.value_text: e.meta["preferred"] for e in ex if e.field == "fomc.vote_against" and e.ok},
    }

    sep_rows = []
    for e in ex:
        if not e.field.startswith("sep.median."):
            continue
        _, _, var, year = e.field.split(".")
        p = good(ex, f"sep.prior_median.{var}.{year}")
        sep_rows.append({"label": SEP_LABELS.get(var, var), "year": "Longer run" if year == "longer_run" else year,
                         "prior": p.value_text if p else "", "current": e.value_text if e.ok else FAILED,
                         "change": _chg(p, e) if e.ok else ""})
    prior_sep_label = next((e.meta.get("label") for e in ex if e.field.startswith("sep.prior_median.")), None)

    impl_rows = []
    for field, label in IMPL_FIELDS:
        cur, pri = good(ex, field), good(prior_ex or [], field)
        if cur or pri:
            impl_rows.append({"label": label, "prior": pri.value_text if pri else "",
                              "current": cur.value_text if cur else FAILED, "change": _chg(pri, cur)})

    ctx = dict(subject=subject, ref=meeting_ref, stage="stage2", mode=mode, title=head, prior_label=prior_label,
               redline=red_html, red_stats=red_stats, vote=vote_diff, sep_rows=sep_rows,
               prior_sep_label=prior_sep_label, impl_rows=impl_rows, transcript_status=transcript_status,
               market_line=market_line, docs=docs)
    body_html = _env.get_template("fomc_stage2.html.j2").render(**ctx)
    lines = [head, f"Statement vs {prior_label}: "
             + (f"{red_stats.changed} sentences changed, {red_stats.added} added, {red_stats.removed} removed"
                if red_stats else "prior statement not available")]
    lines += [f"Vote: {vote_now} (prior {vote_prior})"]
    if vote_diff["new_dissents"]:
        lines.append("New dissents: " + ", ".join(vote_diff["new_dissents"]))
    if vote_diff["dropped_dissents"]:
        lines.append("No longer dissenting: " + ", ".join(vote_diff["dropped_dissents"]))
    if sep_rows:
        lines += ["", f"SEP medians (prior = {prior_sep_label}; change computed):"]
        lines += [f"{r['label']} {r['year']}: {r['prior'] or 'n/a'} -> {r['current']}"
                  + (f" ({r['change']})" if r["change"] else "") for r in sep_rows]
    lines += ["", "Settings (change computed):"]
    lines += [f"{r['label']}: {r['prior'] or 'n/a'} -> {r['current']}" + (f" ({r['change']})" if r["change"] else "")
              for r in impl_rows]
    lines += ["", f"Press conference: {transcript_status}"]
    if market_line:
        lines.append(market_line)
    lines += [""] + [f"{d.name}: {d.url}" for d in docs]
    lines += ["", f"RAAS Research Capital · Economic Events Bot · {meeting_ref}"]
    return Message(ref=meeting_ref, stage="stage2", kind="realtime", subject=subject,
                   body_html=body_html, body_text="\n".join(lines))
