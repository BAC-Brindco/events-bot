"""Score a local model on the PIB items the rules leave 'uncertain' (the only items it would ever see).

One item per request; the system prompt (rubric + worked examples) is identical every time, so
llama-server's prompt cache reuses its KV state and each call only processes the item's own tokens.
Output is constrained to {"relevant": true|false}: a few tokens, nothing to parse wrongly.
Worked examples are invented, not drawn from the labelled set, so the score is not leaked.

    PYTHONPATH=. uv run python harness/llm_eval_pib.py --url http://127.0.0.1:8080 --label qwen3-4b
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from events_bot.core.config import load_sources
from events_bot.core.models import RawItem
from events_bot.filter.keywords import KeywordFilter

ROOT = Path(__file__).resolve().parents[1]

SYSTEM = """You screen Government of India press releases for an Indian equities and macro investment desk.
Answer relevant=true only if the release can plausibly move Indian markets, a listed sector, or the macro outlook:
policy or regulatory decisions, taxes, duties, prices, subsidies, trade measures or negotiations with major partners,
fiscal or economic data, approvals or contracts with money attached, sector rules, major bilateral economic calls.
Answer relevant=false for ceremonies, conferences, speeches without a decision, visits, awards, campaigns, MoUs on
training or culture, defence exercises, crime and security operations, welfare stories and outreach.

Examples:
[Ministry of Finance] Government notifies revised basic customs duty on crude edible oils -> true
[Ministry of Commerce & Industry] India and EU conclude round of free trade agreement negotiations in Brussels -> true
[Ministry of Steel] Government imposes safeguard duty on flat steel imports -> true
[Prime Minister's Office] Prime Minister holds telephone call with President of the United States on trade -> true
[Ministry of Power] Draft Electricity (Amendment) Rules 2026 released for consultation -> true
[Ministry of Defence] Indian Navy and French Navy conclude bilateral exercise -> false
[Ministry of Labour & Employment] Minister addresses national conference on industrial relations -> false
[Ministry of Tourism] India and Spain agree to strengthen cooperation in tourism -> false
[Ministry of Home Affairs] Police bust drug syndicate, seize narcotics worth Rs 500 crore -> false
[Ministry of Railways] Railways introduces new weekly train between Pune and Nagpur -> false"""

SCHEMA = {"type": "object", "properties": {"relevant": {"type": "boolean"}}, "required": ["relevant"]}


def classify(c: httpx.Client, url: str, ministry: str, title: str) -> tuple[bool | None, float]:
    t0 = time.time()
    r = c.post(f"{url}/v1/chat/completions", json={
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": f"[{ministry}] {title}"}],
        "temperature": 0, "max_tokens": 12, "cache_prompt": True,
        "response_format": {"type": "json_schema", "json_schema": {"name": "v", "schema": SCHEMA}}})
    r.raise_for_status()
    try:
        return bool(json.loads(r.json()["choices"][0]["message"]["content"])["relevant"]), time.time() - t0
    except (ValueError, KeyError):
        return None, time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--label", default="model")
    ap.add_argument("--out", default="out/llm_bench")
    a = ap.parse_args()
    cfg = load_sources(ROOT / "config")["pib"]
    kf = KeywordFilter(ROOT / "config")
    items = json.loads((ROOT / "tests/golden/pib/triage_labels.json").read_text(encoding="utf-8"))["items"]
    unsure = [it for it in items if kf.decide(cfg, RawItem(source_id="pib", ext_id="x", url="u", title=it["title"],
                                                           meta={"ministry": it["ministry"]})).rule == "uncertain"]
    c = httpx.Client(timeout=300)
    tp = fp = fn = tn = bad = 0
    secs, rows = [], []
    for it in unsure:
        pred, dt = classify(c, a.url, it["ministry"], it["title"])
        secs.append(dt)
        bad += pred is None
        truth = it["relevant"]
        tp += bool(pred) and truth
        fp += bool(pred) and not truth
        fn += (not pred) and truth
        tn += (not pred) and not truth
        rows.append({**it, "pred": pred, "sec": round(dt, 2)})
    rep = {"label": a.label, "items": len(unsure), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "unparsed": bad,
           "recall": round(tp / max(1, tp + fn), 3), "precision": round(tp / max(1, tp + fp), 3),
           "first_call_sec": round(secs[0], 2), "median_sec": round(sorted(secs)[len(secs) // 2], 2),
           "total_sec": round(sum(secs), 1),
           "missed": [r["title"] for r in rows if r["relevant"] and not r["pred"]],
           "false_alarms": [r["title"] for r in rows if r["pred"] and not r["relevant"]]}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"pib_eval_{a.label}.json").write_text(json.dumps({"report": rep, "rows": rows}, indent=1,
                                                             ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
