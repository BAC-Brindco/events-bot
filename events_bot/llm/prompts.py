"""Prompts for the local LLM. One source of truth for production and harness/llm_eval_pib.py.

Changing TRIAGE_SYSTEM changes every cache key (llm_cache) and must be re-scored with
`llm-bench` against tests/golden/pib/triage_labels.json before it ships.
The worked examples are invented, not taken from the labelled set, so the score is not leaked.
"""
from __future__ import annotations

TRIAGE_SYSTEM = """You screen Government of India press releases for an Indian equities and macro investment desk.
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

TRIAGE_SCHEMA = {"type": "object", "properties": {"relevant": {"type": "boolean"}}, "required": ["relevant"]}


def triage_user(item) -> str:
    """The only per-item tokens the model processes: ministry tag + verbatim title."""
    return f"[{(item.meta or {}).get('ministry') or 'Government of India'}] {item.title}"
