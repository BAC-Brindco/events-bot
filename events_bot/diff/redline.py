"""Statement redline: align at sentence level, then diff changed sentences word by word.

Output is HTML with <del>/<ins>, every word escaped. Only text from the two source
documents appears; nothing is paraphrased.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# Split after . ? ! or : when followed by space + capital/quote, but not after initials ("Jerome H. Powell")
# or common abbreviations.
_SENT = re.compile(r"(?<=[.?!:])(?<!\b[A-Z]\.)(?<!p\.m\.)(?<!a\.m\.)(?<!U\.S\.)(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bSmt\.)(?<!\bProf\.)(?<!\bNo\.)\s+(?=[\"'“A-Z])")
_WORD = re.compile(r"\s+|[^\s]+")


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in text.split("\n"):
        out += [s.strip() for s in _SENT.split(para) if s.strip()]
    return out


def _norm(s: str) -> str:
    """Comparison key: hyphen variants and spacing do not count as changes."""
    return re.sub(r"\s+", " ", re.sub("[‐‑‒–—−]", "-", s)).strip()


@dataclass
class RedlineStats:
    unchanged: int = 0
    changed: int = 0
    added: int = 0
    removed: int = 0

    @property
    def any_change(self) -> bool:
        return bool(self.changed or self.added or self.removed)


def _word_diff(a: str, b: str) -> str:
    wa, wb = _WORD.findall(a), _WORD.findall(b)
    sm = SequenceMatcher(None, [_norm(w) for w in wa], [_norm(w) for w in wb], autojunk=False)
    parts: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        old, new = "".join(wa[i1:i2]), "".join(wb[j1:j2])
        if op == "equal":
            parts.append(html.escape(new))
        if op in ("delete", "replace") and old.strip():
            parts.append(f"<del>{html.escape(old)}</del>")
        if op in ("insert", "replace") and new.strip():
            parts.append(f"<ins>{html.escape(new)}</ins>")
        elif op in ("insert", "replace"):
            parts.append(html.escape(new))
    return "".join(parts)


def redline(prior: str, current: str, *, similar: float = 0.5) -> tuple[str, RedlineStats]:
    """HTML redline of `current` against `prior` (both plain text, one paragraph per line)."""
    a, b = sentences(prior), sentences(current)
    st = RedlineStats()
    sm = SequenceMatcher(None, [_norm(s) for s in a], [_norm(s) for s in b], autojunk=False)
    out: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            st.unchanged += i2 - i1
            out += [f"<p>{html.escape(s)}</p>" for s in b[j1:j2]]
            continue
        olds, news = a[i1:i2], b[j1:j2]
        # pair similar sentences inside a replaced block for word-level diffs
        used: set[int] = set()
        for nb in news:
            best, score = None, similar
            for k, oa in enumerate(olds):
                if k in used:
                    continue
                r = SequenceMatcher(None, _norm(oa), _norm(nb), autojunk=False).ratio()
                if r > score:
                    best, score = k, r
            if best is None:
                st.added += 1
                out.append(f"<p><ins>{html.escape(nb)}</ins></p>")
            else:
                # removed sentences that sit before this match keep their place
                for k in range(best):
                    if k not in used:
                        used.add(k)
                        st.removed += 1
                        out.append(f"<p><del>{html.escape(olds[k])}</del></p>")
                used.add(best)
                st.changed += 1
                out.append(f"<p>{_word_diff(olds[best], nb)}</p>")
        for k, oa in enumerate(olds):
            if k not in used:
                st.removed += 1
                out.append(f"<p><del>{html.escape(oa)}</del></p>")
    return "\n".join(out), st
