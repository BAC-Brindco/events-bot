"""Write tests/golden/fomc/<date>.yaml from the fixtures (BUILD_PROMPT Phase 2 golden tests).

The YAMLs are generated once, then reviewed by a human; `needs_human_review: true`
stays until that review flips it. Re-running overwrites only files still under review.

    uv run python harness/make_golden_fomc.py
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from events_bot.extract import fomc
from events_bot.extract.validator import validate_all

ROOT = Path(__file__).resolve().parents[1]
FIX, OUT = ROOT / "tests" / "fixtures" / "fed", ROOT / "tests" / "golden" / "fomc"


def extract(d: str) -> dict:
    md = date(int(d[:4]), int(d[4:6]), int(d[6:]))
    st, e1 = fomc.parse_statement((FIX / f"monetary{d}a.htm").read_bytes(), md)
    it, e2 = fomc.parse_impl_note((FIX / f"monetary{d}a1.htm").read_bytes(), md)
    texts, ex = {"statement": st, "impl": it}, e1 + e2
    sep = FIX / f"fomcprojtabl{d}.htm"
    if sep.exists():
        s, e3 = fomc.parse_sep(sep.read_bytes(), md)
        texts["sep"], ex = s, ex + e3
    validate_all(ex, texts, expected_period=md.isoformat())
    out: dict = {"meeting": md.isoformat(), "needs_human_review": True, "has_sep": sep.exists(),
                 "invalid": [f"{e.field}: {e.validation_error}" for e in ex if not e.ok],
                 "fields": {}, "vote_for": [], "vote_against": []}
    for e in ex:
        if e.field == "fomc.vote_for":
            out["vote_for"].append(e.value_text)
        elif e.field == "fomc.vote_against":
            out["vote_against"].append({"name": e.value_text, "preferred": e.meta["preferred"]})
        elif e.field in ("fomc.dissent_pref", "fomc.balance_sheet_line"):
            continue
        else:
            out["fields"][e.field] = e.value_text
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for p in sorted(FIX.glob("monetary*a.htm")):
        d = p.stem[len("monetary"):-1]
        f = OUT / f"{d}.yaml"
        if f.exists() and not yaml.safe_load(f.read_text(encoding="utf-8")).get("needs_human_review", True):
            print("reviewed, kept:", f.name)
            continue
        f.write_text(yaml.safe_dump(extract(d), sort_keys=False, allow_unicode=True), encoding="utf-8")
        print("wrote", f.name)


if __name__ == "__main__":
    main()
