"""RBI MPC Stage 1 (release alert) and Stage 2 (post-event summary). Pure builders.

Stage 1 carries the policy rates, stance, vote and the projections table, all as
exact strings from the resolution page. Stage 2 adds the resolution redline against
the prior meeting, vote/dissent changes and projections prior vs current (change
computed and labelled). Market line is omitted until a provider is configured.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..core.models import Message
from ..core.timeutil import fmt_ist
from ..diff.redline import redline
from ..extract.base import Extraction
from ..extract import mpc as mx
from ..extract.validator import first, good
from . import render
from .render import Doc, Table, change

FAILED = "EXTRACTION FAILED"
VARS = {"gdp": "Real GDP growth", "cpi": "CPI inflation", "core_cpi": "Core inflation"}
RATES = (("mpc.repo", "Policy repo rate"), ("mpc.sdf", "Standing deposit facility rate"),
         ("mpc.msf", "Marginal standing facility rate"), ("mpc.bank_rate", "Bank Rate"))
COMPUTED = "Change is computed: current minus prior, both as extracted from the two resolutions."


def _v(ex: list[Extraction], field: str, suffix: str = "") -> str:
    e = first(ex, field)
    return "" if e is None else (e.value_text + suffix if e.ok else FAILED)


def headline(ex: list[Extraction]) -> str:
    act, repo, stance = good(ex, "mpc.action"), good(ex, "mpc.repo"), good(ex, "mpc.stance")
    if not (act and repo):
        return FAILED
    verb = {"keep": "Repo unchanged at", "maintain": "Repo unchanged at", "reduce": "Repo cut to",
            "cut": "Repo cut to", "raise": "Repo raised to", "increase": "Repo raised to",
            "hike": "Repo raised to"}.get(act.value_text, f"Repo ({act.value_text})")
    chg = good(ex, "mpc.change_bps")
    if chg and verb.endswith(" to"):
        verb = verb[:-3] + f" by {chg.value_text} bps to"
    s = f"{verb} {repo.value_text} per cent"
    return s + (f"; stance {stance.value_text}" if stance else "")


def vote_text(ex: list[Extraction]) -> tuple[str, str]:
    v, split = good(ex, "mpc.rate_vote"), good(ex, "mpc.rate_vote_split")
    if split:
        return split.value_text, "rate vote as stated"
    if v and "unanimous" in v.value_text:
        return "Unanimous", "rate vote as stated"
    return (v.value_text if v else FAILED), "rate vote as stated" if v else ""


def dissents(ex: list[Extraction]) -> list[str]:
    out = []
    for e in ex:
        if e.field in ("mpc.stance_dissent", "mpc.rate_dissent") and e.ok:
            what = "Stance" if e.field == "mpc.stance_dissent" else "Rate"
            out.append(f"{what} dissent: {e.value_text} — {e.meta['view']}")
    return out


def _period_key(k: str) -> tuple:
    m = re.match(r"(FY|Q(\d)):?(\d{4})-(\d{2})", k)
    if not m:
        return (9999, 9)
    fy = int(m.group(3))
    return (fy, 0 if m.group(1) == "FY" else int(m.group(2)))


def proj_table(ex: list[Extraction]) -> Table | None:
    cells: dict[str, dict[str, str]] = {}
    periods: list[str] = []
    for e in ex:
        if e.field.startswith("mpc.proj."):
            _, _, var, per = e.field.split(".", 3)
            cells.setdefault(var, {})[per] = e.value_text if e.ok else FAILED
            if per not in periods:
                periods.append(per)
    if not cells:
        return None
    periods.sort(key=_period_key)
    return Table("Projections (per cent)", [p.replace("FY", "FY ") for p in periods],
                 [{"label": VARS.get(v, v), "cells": [c.get(p, "") for p in periods]} for v, c in cells.items()],
                 note="Blank: not projected in this resolution.")


def build_stage1(ref: str, ex: list[Extraction], docs: list[Doc], *, release_at: datetime, first_seen: datetime,
                 mode: str = "live") -> Message:
    head = headline(ex)
    vote, note = vote_text(ex)
    key = [{"label": label, "value_text": v, "note": "per cent"} for field, label in RATES if (v := _v(ex, field))]
    chg = good(ex, "mpc.change_bps")
    if key and chg and key[0]["value_text"] != FAILED:
        key[0]["note"] = f"per cent · change of {chg.value_text} bps as stated"
    key.append({"label": "Stance", "value_text": _v(ex, "mpc.stance") or FAILED})
    key.append({"label": "Rate vote", "value_text": vote, "note": note})
    for field, label in (("mpc.crr", "CRR"), ("mpc.crr_change", "CRR change"), ("mpc.slr", "SLR"),
                         ("mpc.slr_change", "SLR change")):
        if v := _v(ex, field):
            key.append({"label": label, "value_text": v})
    bullets = dissents(ex)
    members = [e.value_text for e in ex if e.field == "mpc.member" and e.ok]
    if members:
        bullets.append("Members: " + ", ".join(members) + ".")
    t = proj_table(ex)
    return render.stage1(ref=ref, subject=f"[RBI] MPC decision | {head}", source_tag="RBI",
                         event_name="Monetary Policy Committee", title=head, key=key, tables=[t] if t else [],
                         bullets=bullets[:8], source_time=fmt_ist(release_at) + " (scheduled)",
                         first_seen=fmt_ist(first_seen), docs=docs,
                         failed=[f"{e.field}: {e.validation_error}" for e in ex if not e.ok]
                         + [f"{f}: not found in source" for f in mx.missing(ex)], mode=mode)


_POLICY_PARA = re.compile(r"\bvoted\b|\bstance\b|policy repo rate|\bwere of the view\b|\bwas of the view\b")


def _body(text: str) -> str:
    """Policy paragraphs only (decision, stance, vote, dissent): RBI rewrites the growth and inflation
    outlook every meeting, so a redline of the whole resolution is mostly noise (7 Oct 2026)."""
    lines = text.split("\n")
    a = next((i for i, l in enumerate(lines) if l.startswith("The Monetary Policy Committee (MPC) held")), 0)
    b = next((i for i, l in enumerate(lines) if re.match(r"\(.+\) (Chief )?General Manager", l)), len(lines))
    return "\n".join(re.sub(r"^\d+\. ", "", l) for l in lines[a:b] if _POLICY_PARA.search(l))


def build_stage2(ref: str, ex: list[Extraction], text: str, prior_ex: list[Extraction] | None, prior_text: str | None,
                 prior_label: str | None, docs: list[Doc], *, transcript_status: str, market_line: str | None = None,
                 mode: str = "live") -> Message:
    red_html, stats = ("", None)
    if prior_text:
        red_html, stats = redline(_body(prior_text), _body(text))
    v_now, n_now = vote_text(ex)
    v_prior, n_prior = vote_text(prior_ex) if prior_ex else ("", "")
    d_now = {e.value_text: e for e in ex if e.field.endswith("_dissent") and e.ok}
    d_prior = {e.value_text: e for e in (prior_ex or []) if e.field.endswith("_dissent") and e.ok}
    lines = [f"New dissent: {n} — {e.meta['view']}" for n, e in d_now.items() if n not in d_prior]
    lines += [f"Dissented again: {n} — {e.meta['view']}" for n, e in d_now.items() if n in d_prior]
    lines += [f"No longer dissenting: {n}" for n in d_prior if n not in d_now]
    m_now = [e.value_text for e in ex if e.field == "mpc.member" and e.ok]
    m_prior = [e.value_text for e in (prior_ex or []) if e.field == "mpc.member" and e.ok]
    if m_prior and m_now != m_prior:
        lines += [f"Joined the MPC: {n}" for n in m_now if n not in m_prior]
        lines += [f"Left the MPC: {n}" for n in m_prior if n not in m_now]
    if not lines:
        lines.append("No dissents at either meeting; membership unchanged.")
    vote = {"now": v_now, "now_note": n_now, "prior": v_prior, "prior_note": n_prior, "lines": lines}

    tables = []
    rows = []
    for field, label in RATES:
        cur, pri = good(ex, field), good(prior_ex or [], field)
        if cur or pri:
            rows.append([label, pri.value_text if pri else "", cur.value_text if cur else FAILED, change(pri, cur)])
    st_now, st_prior = good(ex, "mpc.stance"), good(prior_ex or [], "mpc.stance")
    rows.append(["Stance", st_prior.value_text if st_prior else "", st_now.value_text if st_now else FAILED, ""])
    tables.append(Table("Policy settings", ["", prior_label or "Prior", "Current", "Change*"], rows,
                        "* " + COMPUTED, bold_col=3))
    prow = []
    for e in sorted((e for e in ex if e.field.startswith("mpc.proj.")),
                    key=lambda e: (list(VARS).index(e.field.split(".")[2]), _period_key(e.field.split(".", 3)[3]))):
        _, _, var, per = e.field.split(".", 3)
        p = good(prior_ex or [], e.field)
        prow.append([VARS.get(var, var), per.replace("FY", "FY "), p.value_text if p else "",
                     e.value_text if e.ok else FAILED, change(p, e) if e.ok else ""])
    if prow:
        tables.append(Table("Projections (per cent)", ["Variable", "Period", prior_label or "Prior", "Current",
                                                        "Change*"], prow,
                            "* " + COMPUTED + " Blank prior: the period was not projected last time.", bold_col=4))
    return render.stage2(ref=ref, subject=f"[RBI] Stage 2 | MPC {ref.split(':', 1)[1]}", source_tag="RBI",
                         event_name="Monetary Policy Committee", title=headline(ex),
                         redline_title="Policy paragraphs redline", prior_label=prior_label, redline=red_html,
                         red_stats=stats, vote=vote, tables=tables, transcript_status=transcript_status,
                         market_line=market_line, docs=docs, mode=mode)
