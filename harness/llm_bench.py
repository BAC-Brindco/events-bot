"""Benchmark a small local model on a GitHub runner (llama-server, CPU) for the two LLM jobs.

1. Relevance triage of PIB releases (title + ministry), batched: one request classifies N items
   and returns a JSON array constrained by a schema, so prompt processing is paid once per batch.
2. Extractive key-sentence selection: the model returns sentence *indices* only, so every
   bullet is verbatim source text and the number guard passes by construction.

    uv run python harness/llm_bench.py --url http://127.0.0.1:8080 --label qwen3b
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PIB = ROOT / "docs/phase0/raw/india_t23/pib_scan_english_delhi_2315400-2317620.csv"

TRIAGE_SYS = (
    "You triage Government of India press releases for an Indian equities and macro desk. "
    "Mark an item relevant only if it can move Indian markets, sectors or macro: Cabinet/CCEA decisions, "
    "taxes and duties, subsidies, MSP, PLI, FDI, disinvestment, trade policy, energy and commodity prices, "
    "regulation of a listed sector, fiscal data, major infrastructure approvals. Ceremonies, greetings, "
    "visits, awards, sports, cultural events, condolences and generic schemes outreach are not relevant. "
    "Return one object per item, in order.")
SCHEMA = {
    "type": "array",
    "items": {"type": "object", "properties": {
        "i": {"type": "integer"},
        "relevant": {"type": "boolean"},
        "topic": {"type": "string", "enum": ["cabinet", "tax_duty", "trade", "agri_msp", "energy", "industry_pli",
                                              "fiscal", "infra", "regulation", "other", "none"]}},
        "required": ["i", "relevant", "topic"]}}


def chat(c: httpx.Client, url: str, system: str, user: str, schema: dict, max_tokens: int) -> tuple[dict, float, dict]:
    t0 = time.time()
    r = c.post(f"{url}/v1/chat/completions", json={
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0, "max_tokens": max_tokens, "cache_prompt": True,
        "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}})
    r.raise_for_status()
    j = r.json()
    return json.loads(j["choices"][0]["message"]["content"]), time.time() - t0, j.get("timings", {}) | j.get("usage", {})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--label", default="model")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--out", default="out/llm_bench")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [r for r in csv.DictReader(PIB.open(encoding="utf-8")) if r.get("title")][: a.n]
    c = httpx.Client(timeout=600)
    report = {"label": a.label, "triage": [], "extract": None}

    t_all = time.time()
    results = []
    for k in range(0, len(rows), a.batch):
        chunk = rows[k:k + a.batch]
        user = "\n".join(f"{i}. [{r['ministry']}] {r['title']}" for i, r in enumerate(chunk))
        res, dt, tim = chat(c, a.url, TRIAGE_SYS, user, SCHEMA, max_tokens=40 * len(chunk))
        report["triage"].append({"batch": k // a.batch, "items": len(chunk), "sec": round(dt, 2), "timings": tim})
        by_i = {o["i"]: o for o in res}
        for i, r in enumerate(chunk):
            o = by_i.get(i, {})
            results.append({"ministry": r["ministry"], "title": r["title"], "relevant": o.get("relevant"),
                            "topic": o.get("topic")})
        print(f"batch {k // a.batch}: {len(chunk)} items in {dt:.1f}s", flush=True)
    report["triage_total_sec"] = round(time.time() - t_all, 1)
    report["triage_items"] = len(results)
    report["relevant"] = sum(1 for r in results if r["relevant"])
    (out / f"triage_{a.label}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

    # Extractive: pick 5 key sentences from the 7 Oct MPC resolution
    from events_bot.extract.base import html_text
    text = html_text((ROOT / "tests/fixtures/rbi_mpc/pr_63742.html").read_bytes(), ".tablebg")
    sents = [s for s in re.split(r"(?<=[.])\s+(?=[A-Z])", text) if 40 < len(s) < 400][:45]
    user = "\n".join(f"{i}. {s}" for i, s in enumerate(sents))
    sys2 = ("Select the sentences a markets desk most needs from this central bank policy statement: the decision, "
            "stance, forward guidance, and the main growth and inflation judgements. Return 5 sentence numbers, most "
            "important first.")
    schema2 = {"type": "object", "properties": {"pick": {"type": "array", "items": {"type": "integer"},
                                                         "minItems": 5, "maxItems": 5}}, "required": ["pick"]}
    res, dt, tim = chat(c, a.url, sys2, user, schema2, max_tokens=40)
    report["extract"] = {"sec": round(dt, 2), "timings": tim, "picked": [sents[i] for i in res["pick"] if i < len(sents)]}
    print(f"extract: {dt:.1f}s", flush=True)
    (out / f"report_{a.label}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("triage",)}, indent=1, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
