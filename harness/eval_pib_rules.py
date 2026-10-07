"""Score the PIB keyword/ministry rules against the hand-labelled set.

    PYTHONPATH=. uv run python harness/eval_pib_rules.py [--show]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from events_bot.core.config import load_sources
from events_bot.core.models import RawItem
from events_bot.filter.keywords import KeywordFilter

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cfg = load_sources(ROOT / "config")["pib"]
    kf = KeywordFilter(ROOT / "config")
    items = json.loads((ROOT / "tests/golden/pib/triage_labels.json").read_text(encoding="utf-8"))["items"]
    c = Counter()
    rows = []
    for it in items:
        d = kf.decide(cfg, RawItem(source_id="pib", ext_id=str(it["prid"]), url="u", title=it["title"],
                                   meta={"ministry": it["ministry"]}))
        out = "keep" if d.keep else ("uncertain" if d.rule == "uncertain" else "reject")
        c[(out, it["relevant"])] += 1
        rows.append((out, it["relevant"], d.rule, d.reason, it["ministry"], it["title"]))
    print("decision x truth:", dict(c))
    keep_rel, keep_irr = c[("keep", True)], c[("keep", False)]
    rej_rel = c[("reject", True)]
    print(f"kept {keep_rel + keep_irr} (relevant {keep_rel}, noise {keep_irr}); "
          f"rejected relevant (MISSED) {rej_rel}; uncertain {c[('uncertain', True)] + c[('uncertain', False)]} "
          f"(of which relevant {c[('uncertain', True)]})")
    if "--show" in sys.argv:
        for r in rows:
            if (r[0] == "reject" and r[1]) or (r[0] == "keep" and not r[1]) or r[0] == "uncertain":
                print(f"{r[0]:9} rel={int(r[1])} {r[2]:16} {str(r[3])[:22]:22} | {r[4][:24]:24} | {r[5][:90]}")


if __name__ == "__main__":
    main()
