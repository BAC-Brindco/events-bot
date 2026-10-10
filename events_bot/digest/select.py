"""Deterministic picks from a release body: operative paragraphs, key figures, applicability, dates.

Nothing here writes text. Every value returned is an exact substring of `Body.text`:
  operative()  the paragraphs that carry the decision (directive verbs, figures, dates), in source order
  figures()    amounts, percentages and basis points as Extractions (character spans, validated
               verbatim like every other extraction), each with the sentence it sits in
  applies_to() the addressee / applicability lines ("All Commercial Banks", "applicable to ...")
  effective()  the sentence that says when it takes effect
  reference()  circular / notification / press-release numbers
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..extract.base import Extraction, parse_number
from ..extract.validator import validate

_BOILER = re.compile(
    r"^(?:Yours faithfully|Sd/?-|Chief General Manager|General Manager|Deputy General Manager|"
    r"Press Release\s*:|Encl|Copy to|Tel\b|Fax\b|E-?mail|Page \d|\(Release ID|Visitor Counter|Follow us|"
    r"Table of Contents|Chapter [IVX]+|Disclaimer|Note\s*:|\*{3}|Annex|Ministry of [A-Z][a-z]+$|"
    r"(?:\d+\.\s+)?Table [A-Z0-9]+\b.{0,90}$)", re.I)
_SIGNOFF = re.compile(r"^\((?:[A-Z][\w.]*\s?){1,5}\)\s*(?:Chief General Manager|General Manager|Director)?", re.I)
_DIRECTIVE = re.compile(
    r"\bshall\b|\bhereby\b|\bdecided\b|\bdirected\b|\bapproved?\b|\bnotified\b|\bamend(?:ed|ment)?\b|"
    r"\bsubstituted\b|\breplaced\b|with effect from|come into (?:effect|force)|\beffective\b|\bmandatory\b|"
    r"\brequired to\b|\bpermitted\b|\ballowed\b|\brevised\b|\bincrease[ds]?\b|\breduce[ds]?\b|\bcut\b|"
    r"\brecommend(?:ed|s|ation)\b|\bannounce[ds]?\b|\bintroduc(?:e|ed|tion)\b|\bproposed?\b|\bextended\b|"
    r"\bexempt(?:ed|ion)?\b|\brationalis|\brationaliz|\bprojected\b|\bforecast\b|\bestimated\b|\bgrew\b|"
    r"\bgrowth\b|\bdeclined\b|\brose\b|\bfell\b", re.I)
_FIGURE_RX = re.compile(
    r"(?:₹|Rs\.?\s?|INR\s?)\s?\d(?:[\d,]*\d)?(?:\.\d+)?(?:\s?[–-]\s?\d(?:[\d,]*\d)?(?:\.\d+)?)?"
    r"(?:\s?(?:lakh crore|crore|lakh|billion|million|bn|mn))?"
    r"|(?:US\$|USD\s?|\$)\s?\d(?:[\d,]*\d)?(?:\.\d+)?(?:\s?(?:billion|million|trillion|bn|mn))?"
    r"|\b\d(?:[\d,]*\d)?(?:\.\d+)?\s?(?:lakh crore|crore|lakh tonnes?|million tonnes?|LMT|MT|GW|MW|billion units)\b"
    r"|(?<![\w.])-?\d+(?:\.\d+)?\s?(?:%|per\s?cent\b|percent\b)"
    r"|\b\d+(?:\.\d+)?\s?(?:bps|basis points)\b", re.I)
_DATE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)? (?:January|February|March|April|May|June|July|August|September|"
                   r"October|November|December),? \d{4}\b|\b(?:January|February|March|April|May|June|July|August|"
                   r"September|October|November|December) \d{1,2},? \d{4}\b", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z(“\"])")
_ABBREV_END = re.compile(r"(?:\b(?:No|Nos|Rs|Dr|Mr|Ms|Smt|Shri|Prof|viz|i\.e|e\.g|etc|Sr|Jr|Ltd|Co|vs|para|Govt)\.)$")
_ENUMERATOR = re.compile(r"\(?(?:\d+(?:\.\d+)*|[ivxIVX]{1,5}|[a-zA-Z])[.)]?\)?")      # "2." "(ii)" "4.1" "a)"
WINDOW = 40          # long instruments: the operative part is near the top (applicability, the change itself)


@dataclass
class Figure:
    ex: Extraction
    context: str            # the sentence containing the figure, verbatim
    clause: str = ""        # the short clause around the figure: a verbatim slice of `context`
    cut_left: bool = False  # the clause starts after the sentence start (shown with a leading "…")
    cut_right: bool = False


CLAUSE_WORDS = 20
CLUSTER_GAP = 45            # chars between two figures of one sentence that share a clause
_CLAUSE_STOP = ";:()"


def _is_boundary(s: str, i: int, commas: bool = False) -> bool:
    c = s[i]
    if c in _CLAUSE_STOP:
        return True
    if c == ",":           # a clause comma: not "4,030" and not "Act, 2017" / "October 3, 2026"
        return commas and not (i + 1 < len(s) and (s[i + 1].isdigit() or s[i + 1:i + 2] == " "
                                                   and s[i + 2:i + 3].isdigit()))
    if c == ".":                                   # a sentence-internal full stop, not a decimal or "Rs."
        return i + 1 < len(s) and s[i + 1] == " " and not _ABBREV_END.search(s[max(0, i - 6):i + 1])
    return False


def _around(sent: str, a: int, b: int, commas: bool, skip: int = 0) -> tuple[int, int]:
    """Nearest boundaries left of `a` and right of `b`, skipping `skip` boundaries on each side."""
    lefts = [i + 1 for i in range(a - 1, -1, -1) if _is_boundary(sent, i, commas)]
    rights = [i for i in range(b, len(sent)) if _is_boundary(sent, i, commas)]
    return (lefts[skip] if len(lefts) > skip else 0), (rights[skip] if len(rights) > skip else len(sent))


def clause(sent: str, a: int, b: int, words: int = CLAUSE_WORDS) -> tuple[str, bool, bool]:
    """The clause of `sent` around the figure at [a, b): cut at ; : ( ) and, only when that is still long,
    at clause commas; then to at most `words` words around the figure. Returns (slice, cut_left, cut_right);
    the slice is verbatim."""
    left, right = _around(sent, a, b, commas=False)
    if len(sent[left:right].split()) > words:
        l2, r2 = _around(sent, a, b, commas=True)
        if len(sent[l2:r2].split()) >= 6:
            left, right = l2, r2
    if len(sent[left:right].split()) < 6:          # "(Rs. 20 crore under CGST": widen by one clause each side
        left, right = _around(sent, a, b, commas=False, skip=1)
    # word budget: about 8 words before the figure and the rest after
    before = [m.start() for m in re.finditer(r"(?<=\s)\S", sent[left:a])]
    if len(sent[left:a].split()) > 8:
        left += before[-8]
    after_ws = [m.start() for m in re.finditer(r"\s", sent[b:right])]
    budget = max(4, words - len(sent[left:b].split()))
    if len(after_ws) >= budget:
        right = b + after_ws[budget - 1]
    seg = sent[left:right]
    lead = len(seg) - len(seg.lstrip(" ,;:"))
    seg = seg.strip(" ,;:")
    left += lead
    right = left + len(seg)
    tail = sent[right:].strip(" .,;:")
    return seg, left > 0, bool(tail)


def sentences(par: str) -> list[str]:
    """Verbatim sentence slices of `par`; no split after "No." / "Rs." / "Dr." and similar."""
    out: list[str] = []
    start = 0
    for m in _SENT_SPLIT.finditer(par):
        seg = par[start:m.start()]
        if _ABBREV_END.search(seg) or _ENUMERATOR.fullmatch(seg.strip()):
            continue
        s = par[start:m.start()].strip()
        if s:
            out.append(s)
        start = m.end()
    tail = par[start:].strip()
    if tail:
        out.append(tail)
    return out


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _boiler(p: str, title: str = "") -> bool:
    if _BOILER.match(p) or _SIGNOFF.match(p):
        return True
    if re.search(r"[ऀ-ॿ]", p):                       # Hindi text / a PDF font-mapping artefact
        return True
    letters = [c for c in p if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) > 0.6 * len(letters):   # cover page, headings
        return True
    if _REF.match(p) and len(p) < 160:                       # "RBI/2026-27/289 DOR.MRG..." reference line
        return True
    t = _alnum(title)
    if t and len(p) < len(title) + 60:                       # the title (or "Subject: <title>") repeated
        pa = _alnum(re.sub(r"^\s*(?:Subject|Sub)\s*:\s*", "", p, flags=re.I))
        if pa and (pa in t or t in pa):
            return True
    return False


def score(p: str, idx: int) -> float:
    s = 0.0
    s += min(6, 2 * len(_DIRECTIVE.findall(p)))
    s += min(4, len(_FIGURE_RX.findall(p)))
    s += min(2, len(_DATE.findall(p)))
    if idx < 3:
        s += 3                       # the lede states what the release is
    if 150 <= len(p) <= 1200:
        s += 1
    if len(p) > 2500:
        s -= 3                       # a whole annex in one block
    return s


def operative(paragraphs: list[str], n: int = 6, min_n: int = 3, title: str = "",
              exclude: list[str] | tuple = (), window: int = WINDOW) -> list[str]:
    cands = [(i, p) for i, p in enumerate(paragraphs[:window]) if not _boiler(p, title) and p not in exclude]
    if not cands:
        return []
    ranked = sorted(cands, key=lambda ip: (-score(ip[1], ip[0]), ip[0]))
    keep = {i for i, _ in ranked[:n]}
    # the first substantive paragraph always leads
    keep.add(cands[0][0])
    out = [p for i, p in cands if i in keep]
    if len(out) < min_n:
        out = [p for _, p in cands[:min_n]]
    return out[: max(n, min_n) + 1]


def figures(text: str, paragraphs: list[str], limit: int = 10, doc: str = "release") -> list[Figure]:
    """Figures inside the chosen paragraphs, located in `text` and validated verbatim."""
    out: list[Figure] = []
    seen: set[str] = set()
    for par in paragraphs:
        base = text.find(par)
        if base < 0:
            continue
        for sent in sentences(par):
            if sent not in text:
                continue
            s0 = base + par.find(sent)
            found: list[Extraction] = []
            for m in _FIGURE_RX.finditer(sent):
                val = m.group(0).strip()
                if val in seen or not re.search(r"\d", val):     # a figure repeated later adds nothing
                    continue
                seen.add(val)
                a = s0 + m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
                b = a + len(val)
                core = re.search(r"-?\d[\d,]*(?:\.\d+)?", val)
                ex = Extraction(field="digest.figure", value_text=text[a:b],
                                value_norm=parse_number(core.group(0)) if core else None,
                                unit="text", char_start=a, char_end=b, snippet=sent, doc=doc)
                validate(ex, text)
                if ex.ok:
                    found.append(ex)
                if len(out) + len(found) >= limit:
                    break
            # figures close together ("from 58.0% to 58.2%") share one clause, so they read as one row
            clusters: list[list[Extraction]] = []
            for ex in found:
                if clusters and ex.char_start - clusters[-1][-1].char_end <= CLUSTER_GAP:
                    clusters[-1].append(ex)
                else:
                    clusters.append([ex])
            for cl_exs in clusters:
                n_words = min(CLAUSE_WORDS + 6 * (len(cl_exs) - 1), 34)
                seg, cl, cr = clause(sent, cl_exs[0].char_start - s0, cl_exs[-1].char_end - s0, words=n_words)
                out += [Figure(ex, sent, seg, cl, cr) for ex in cl_exs]
            if len(out) >= limit:
                return out[:limit]
    return out


_ADDRESSEE = re.compile(r"^(?:All|The Chairm|The Managing|The Chief Executive|The CEO|Chief Executive)\b.{3,300}$")
_APPLIES = re.compile(r"\b(?:shall apply to|shall be applicable to|shall be applicable on|applicable to all|"
                      r"apply to all|are applicable to|is applicable to)\b|^Applicability\b", re.I)


def applies_to(text: str, limit: int = 2) -> list[str]:
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines[:30]):
        if re.fullmatch(r"To,?", line.strip()) and i + 1 < len(lines) and len(lines[i + 1]) <= 400:
            out.append(lines[i + 1])                         # the addressee block of a circular
        elif _ADDRESSEE.match(line) and line not in out:
            out.append(line)
    for line in lines[:WINDOW + 20]:
        if len(out) >= limit:
            break
        if _APPLIES.search(line):
            for s in sentences(line):
                if _APPLIES.search(s) and s not in out and len(s) <= 600:
                    out.append(s)
                    break
    return out[:limit]


_EFFECTIVE = re.compile(r"come into (?:effect|force)|with effect from|effective from|\bw\.e\.f\.|"
                        r"shall be effective|will be effective|take effect", re.I)


def effective(text: str) -> str | None:
    for line in text.split("\n"):
        if _EFFECTIVE.search(line):
            for s in sentences(line):
                if _EFFECTIVE.search(s) and len(s) <= 400:
                    return s
    return None


_REF = re.compile(
    r"RBI/[\w./-]*\d{4}-\d{2,4}/\d+"
    r"|\b(?:DOR|DoR|FIDD|DPSS|FMRD|FED|DOS|CO\.DPSS|A\.P\. \(DIR Series\))[\w.()/ -]{0,40}?\d{4}-\d{2}"
    r"|Press Release:? \d{4}-\d{4}/\d+"
    r"|SEBI/HO/[\w./-]+"
    r"|\bHO/[\w./-]+/\d{4}"
    r"|Notification No\.? ?\d+/\d{4}[\w() -]{0,30}"
    r"|Circular No\.? ?\d+/\d{4}[\w() -]{0,30}"
    r"|\bG\.S\.R\. ?\d+\s?\(E\)")


def reference(text: str, limit: int = 2) -> list[str]:
    out: list[str] = []
    for m in _REF.finditer(text[:6000]):
        v = m.group(0).strip(" -")
        if v not in out:
            out.append(v)
        if len(out) >= limit:
            break
    return out
