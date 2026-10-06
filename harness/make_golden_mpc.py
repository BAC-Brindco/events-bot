"""Write tests/golden/mpc/<date>.yaml from the RBI resolution fixtures (needs_human_review: true).

    PYTHONPATH=. uv run python harness/make_golden_mpc.py
"""
from __future__ import annotations

from pathlib import Path

import yaml

from events_bot.extract import mpc
from events_bot.extract.validator import validate_all
from tests.mpc_meetings import MEETINGS

ROOT = Path(__file__).resolve().parents[1]
FIX, OUT = ROOT / "tests" / "fixtures" / "rbi_mpc", ROOT / "tests" / "golden" / "mpc"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for d, prid in MEETINGS.items():
        f = OUT / f"{d.isoformat()}.yaml"
        if f.exists() and not yaml.safe_load(f.read_text(encoding="utf-8")).get("needs_human_review", True):
            print("reviewed, kept:", f.name)
            continue
        t, ex = mpc.parse_resolution((FIX / f"pr_{prid['resolution']}.html").read_bytes(), d)
        validate_all(ex, {"resolution": t}, expected_period=d.isoformat())
        g = {"meeting": d.isoformat(), "prid": prid["resolution"], "needs_human_review": True,
             "invalid": [f"{e.field}: {e.validation_error}" for e in ex if not e.ok], "fields": {},
             "members": [e.value_text for e in ex if e.field == "mpc.member"],
             "dissents": [{"field": e.field, "member": e.value_text, "view": e.meta["view"]}
                          for e in ex if e.field.endswith("_dissent")]}
        for e in ex:
            if e.field != "mpc.member" and not e.field.endswith("_dissent"):
                g["fields"][e.field] = e.value_text
        f.write_text(yaml.safe_dump(g, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print("wrote", f.name)


if __name__ == "__main__":
    main()
