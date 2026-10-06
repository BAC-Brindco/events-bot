"""RBI MPC resolution (BS_PressReleaseDisplay page) -> extractions.

The resolution's HTML is the Stage 1 source (Phase 0: the PDF lags 25-80 min). Field
phrasing varies by meeting ("projected at" / "projected to be" / "now projected at" /
"retained at"; quarter lists with "; and" or ", and"; "Q1:2026-27 and Q2 are projected
at X per cent and Y per cent, respectively"), so projections are parsed sentence by
sentence. Every value is a span of the page text; nothing is computed here.
"""
from __future__ import annotations

import re
from datetime import date

from .base import Extraction, find, html_text, span

PCT = r"(\d+(?:\.\d+)?) per cent"
FY = r"\d{4}-\d{2}"
MON3 = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def resolution_text(content: bytes) -> str:
    return html_text(content, ".tablebg")


def page_date(d: date) -> str:
    """The page's own date line, e.g. 'Date : Aug 05, 2026'."""
    return f"{MON3[d.month - 1]} {d.day:02d}, {d.year}"


def _sentences(text: str):
    """(start, end) spans of sentences; never splits inside '5.25' (needs '. ' + capital)."""
    for para in re.finditer(r"[^\n]+", text):
        a = para.start()
        for m in re.finditer(r"(?<=[.?!])\s+(?=[A-Z(\"'])", para.group(0)):
            yield a, para.start() + m.start()
            a = para.start() + m.end()
        yield a, para.end()


def _var(sent: str) -> str | None:
    if re.search(r"\breal GDP growth\b", sent, re.I):
        return "gdp"
    if re.match(r"\s*Core inflation\b", sent):
        return "core_cpi"
    if re.search(r"\bCPI inflation\b|^\s*Inflation for Q", sent):
        return "cpi"
    return None


_FY_HEAD = re.compile(rf"for ({FY}) (?:is|has been) (?:now )?(?:projected|retained|revised [a-z]+)(?: to be)?(?: at)? {PCT}")
_Q_QUAL = re.compile(rf"for Q([1-4]):({FY}) (?:is|has been) (?:now )?(?:projected|retained)(?: to be)?(?: at)? {PCT}")
_Q_PAIR = re.compile(rf"for Q([1-4]):({FY}) and Q([1-4]) are (?:now )?projected (?:at|to be(?: at)?) {PCT} and {PCT}")
_Q_ITEM = re.compile(rf"\bQ([1-4]) at {PCT}")
_CORE = re.compile(rf"Core inflation is projected at {PCT} for ({FY})")


def parse_projections(text: str, period: str) -> list[Extraction]:
    ex: list[Extraction] = []
    seen: set[str] = set()

    def add(var: str, key: str, a: int, b: int) -> None:
        f = f"mpc.proj.{var}.{key}"
        if f in seen:           # first statement of a figure wins (later mentions are commentary)
            return
        seen.add(f)
        ex.append(span(text, a, b, f, unit="percent", doc="resolution", period=period))

    for sa, sb in _sentences(text):
        s = text[sa:sb]
        var = _var(s)
        if var is None or not re.search(r"projected|retained", s):
            continue
        if var == "core_cpi":
            m = _CORE.search(s)
            if m:
                add(var, f"FY{m.group(2)}", sa + m.start(1), sa + m.end(1))
            continue
        base_fy = None
        m = _FY_HEAD.search(s)
        if m:
            base_fy = m.group(1)
            add(var, f"FY{base_fy}", sa + m.start(2), sa + m.end(2))
        m = _Q_PAIR.search(s)
        if m:
            fy = m.group(2)
            add(var, f"Q{m.group(1)}:{fy}", sa + m.start(4), sa + m.end(4))
            add(var, f"Q{m.group(3)}:{fy}", sa + m.start(5), sa + m.end(5))
            continue
        m = _Q_QUAL.search(s)
        if m:
            fy = m.group(2)
            add(var, f"Q{m.group(1)}:{fy}", sa + m.start(3), sa + m.end(3))
            base_fy = base_fy or fy             # "...Q1:2026-27 is projected at 6.7 per cent and Q2 at 6.8"
            tail_from = m.end()
        else:
            tail_from = 0
        if base_fy:
            for q in _Q_ITEM.finditer(s, tail_from):
                add(var, f"Q{q.group(1)}:{base_fy}", sa + q.start(2), sa + q.end(2))
    return ex


# ---- decision, stance, votes -----------------------------------------------------------

_DECISION = re.compile(
    r"the MPC (?P<vote>voted(?: unanimously)?|unanimously voted)(?: by a majority of (?P<maj>\d+ to \d+))?"
    r" to (?P<act>keep|reduce|raise|increase|maintain|cut|hike)[^.]*?policy repo rate[^.]*?"
    rf"(?:unchanged at|to|at|by \d+ (?:basis points|bps) to) {PCT}")
_DECISION_ALT = re.compile(rf"the MPC (?P<vote>voted(?: unanimously)?|unanimously voted) to (?P<act>maintain|keep) the policy repo rate at {PCT}")
_UNANIMOUS = re.compile(r"the MPC (unanimously voted|voted unanimously) to \w+ the (?:policy )?repo rate")
_SDF = re.compile(rf"standing deposit facility \(SDF\) rate[^.;]*? {PCT}")
_MSF = re.compile(rf"marginal standing facility \(MSF\) rate and the Bank Rate[^.;]*? {PCT}")
_STANCE = re.compile(r"(?:continue with|retain|change to|changed to|adopt|maintain) the (?P<s>neutral|accommodative|"
                     r"withdrawal of accommodation|calibrated tightening|tightening)\b stance|stance (?:from \w+ )?to (?P<s2>neutral|accommodative)")
_MEMBERS = re.compile(r"chairmanship of (?P<gov>(?:Shri|Smt\.|Dr\.) [A-Z][\w.]*(?: [A-Z][\w.]*)+), Governor.*?The MPC members (?P<rest>.+?) attended the meeting")
_PERSON = re.compile(r"(?:Shri|Smt\.|Dr\.|Prof\.) [A-Z][\w.]*(?: [A-Z][\w.]*)*")
_DISSENT = re.compile(r"(?P<who>(?:Shri|Smt\.|Dr\.|Prof\.) [A-Z][\w.]*(?: [A-Z][\w.]*)*) (?:was of the view|retained (?:his|her) view|voted)"
                      r"(?P<view>[^.]*(?:\.\d[^.]*)*)\.")
_CRR = re.compile(rf"(?:cash reserve ratio \(CRR\)|CRR)[^.]*?(?:by (\d+ basis points)|to {PCT})")
_SLR = re.compile(rf"statutory liquidity ratio \(SLR\)[^.]*?(?:by (\d+ basis points)|to {PCT})")


def parse_resolution(content: bytes, meeting: date) -> tuple[str, list[Extraction]]:
    text = resolution_text(content)
    period = meeting.isoformat()
    ex: list[Extraction] = []

    def put(e: Extraction | None) -> None:
        if e is not None:
            ex.append(e)

    put(find(text, r"Date : (" + re.escape(page_date(meeting)) + ")", "mpc.doc_date", norm=False, unit="text",
             doc="resolution", period=period))
    m = _DECISION.search(text) or _DECISION_ALT.search(text)
    if m:
        ex.append(span(text, m.start("act"), m.end("act"), "mpc.action", unit="text", norm=False,
                       doc="resolution", period=period))
        vote = (m.start("vote"), m.end("vote"))
        if "unanimous" not in m.group("vote"):
            # Aug 2025: the decision paragraph says "voted"; unanimity is stated in the rationale.
            u = _UNANIMOUS.search(text)
            if u:
                vote = (u.start(1), u.end(1))
        ex.append(span(text, vote[0], vote[1], "mpc.rate_vote", unit="text", norm=False,
                       doc="resolution", period=period))
        if m.groupdict().get("maj"):
            ex.append(span(text, m.start("maj"), m.end("maj"), "mpc.rate_vote_split", unit="text", norm=False,
                           doc="resolution", period=period))
        g = len(m.groups())
        ex.append(span(text, m.start(g), m.end(g), "mpc.repo", unit="percent", doc="resolution", period=period))
        after = m.end()
    else:
        after = 0
    put(find(text, _SDF, "mpc.sdf", unit="percent", doc="resolution", period=period, start=after))
    msf = find(text, _MSF, "mpc.msf", unit="percent", doc="resolution", period=period, start=after)
    if msf:
        ex.append(msf)
        br = span(text, msf.char_start, msf.char_end, "mpc.bank_rate", unit="percent", doc="resolution",
                  period=period)
        br.meta["note"] = "stated jointly with MSF"
        ex.append(br)
    st = _STANCE.search(text)
    if st:
        g = "s" if st.group("s") else "s2"
        ex.append(span(text, st.start(g), st.end(g), "mpc.stance", unit="text", norm=False, doc="resolution",
                       period=period))
    mm = _MEMBERS.search(text)
    if mm:
        ex.append(span(text, mm.start("gov"), mm.end("gov"), "mpc.member", unit="text", norm=False,
                       doc="resolution", period=period))
        for p in _PERSON.finditer(mm.group("rest")):
            ex.append(span(text, mm.start("rest") + p.start(), mm.start("rest") + p.end(), "mpc.member",
                           unit="text", norm=False, doc="resolution", period=period))
    for d in _DISSENT.finditer(text):
        view = d.group("view").strip()
        kind = "mpc.stance_dissent" if "stance" in view else "mpc.rate_dissent"
        e = span(text, d.start("who"), d.end("who"), kind, unit="text", norm=False, doc="resolution", period=period)
        e.meta["view"] = text[d.start("view"):d.end("view")].strip()
        ex.append(e)
    for rx, field in ((_CRR, "mpc.crr"), (_SLR, "mpc.slr")):
        c = rx.search(text)
        if c:
            g = 1 if c.group(1) else 2
            ex.append(span(text, c.start(g), c.end(g), field + ("_change" if g == 1 else ""),
                           unit="text" if g == 1 else "percent", norm=g != 1, doc="resolution", period=period))
    ex += parse_projections(text, period)
    return text, ex


def consistency(ex: list[Extraction], prior: list[Extraction] | None = None) -> list[str]:
    """SDF and MSF sit 25 bp either side of repo (the LAF corridor since Apr 2022)."""
    from decimal import Decimal

    from .validator import check_large_move, first, good

    problems: list[str] = []
    repo, sdf, msf = good(ex, "mpc.repo"), good(ex, "mpc.sdf"), good(ex, "mpc.msf")
    if repo and sdf and repo.value_norm - sdf.value_norm != Decimal("0.25"):
        sdf.validation_error = f"SDF {sdf.value_text} not 25 bp below repo {repo.value_text}"
        problems.append(sdf.validation_error)
    if repo and msf and msf.value_norm - repo.value_norm != Decimal("0.25"):
        msf.validation_error = f"MSF {msf.value_text} not 25 bp above repo {repo.value_text}"
        problems.append(msf.validation_error)
    if prior:
        new, old = first(ex, "mpc.repo"), first(prior, "mpc.repo")
        if new is not None and old is not None:
            before = new.validation_error
            check_large_move(new, old, None)
            if new.validation_error != before:
                problems.append(new.validation_error or "")
    return problems
