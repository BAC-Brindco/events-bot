"""NSE ASM/GSM: snapshot diff, baseline on first sight, one consolidated alert per change."""
import copy
import json
from datetime import datetime, timezone

import httpx

from events_bot.sources.india import nse_surv

from .conftest import FIXTURES, mock_fetcher

FX = FIXTURES / "nse"
ASM = json.loads((FX / "asm_2026-10-07.json").read_text(encoding="utf-8"))
GSM = json.loads((FX / "gsm_2026-10-07.json").read_text(encoding="utf-8"))


def _mutated():
    asm = copy.deepcopy(ASM)
    lt = asm["longterm"]["data"]
    removed = lt.pop(0)                                         # removal
    lt[0]["asmSurvIndicator"] = "Stage IV"                     # stage change
    lt.append({**lt[1], "symbol": "SUNPHARMA", "companyName": "Sun Pharmaceutical Industries Limited",
               "asmSurvIndicator": "Stage I"})                  # addition (on the example watchlist)
    return asm, removed["symbol"], lt[0]["symbol"]


def test_snapshot_and_diff():
    old = nse_surv.snapshot("asm", ASM)
    assert len(old) == len(ASM["longterm"]["data"]) + len(ASM["shortterm"]["data"])
    new_payload, gone, moved = _mutated()
    ch = nse_surv.diff(old, nse_surv.snapshot("asm", new_payload))
    kinds = {(c["change"], c["symbol"]) for c in ch}
    assert ("added", "SUNPHARMA") in kinds and ("removed", gone) in kinds and ("stage change", moved) in kinds
    assert [c["change"] for c in ch] == sorted([c["change"] for c in ch], key=["added", "stage change", "removed"].index)
    assert len(nse_surv.snapshot("gsm", GSM)) == len(GSM)


def test_first_snapshot_is_baseline_then_one_alert_with_watchlist_flag(make_app, tmp_path):
    app = make_app("dry_run")
    wl = tmp_path / "watchlist.csv"
    wl.write_text("ticker,name,aliases\nSUNPHARMA,Sun Pharmaceutical Industries,Sun Pharma\n", encoding="utf-8")
    app.pipeline.watchlist.path = wl
    app.pipeline.watchlist.reload()
    state = {"asm": ASM}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("reportASM"):
            return httpx.Response(200, content=json.dumps(state["asm"]).encode())
        if req.url.path.endswith("reportGSM"):
            return httpx.Response(200, content=json.dumps(GSM).encode())
        return httpx.Response(200, content=b"<html>ok</html>")

    mock_fetcher(app, handler)
    a = app.adapter("nse_surv")
    assert a.poll() == []                                       # baseline: nothing to diff against
    state["asm"], _, _ = _mutated()
    items = a.poll()
    assert len(items) == 1 and items[0].title.startswith("NSE ASM lists updated (07-Oct-2026): 1 added, 1 removed, 1")
    assert "SUNPHARMA" in items[0].summary
    assert app.pipeline.watchlist.tags(items[0]) == ["watch:SUNPHARMA"]
    msg = a.render_stage1(items[0], first_seen=datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc), mode="dry_run")
    assert msg.subject.startswith("[NSE] Surveillance |") and "WATCHLIST" in msg.body_html
    again = a.poll()                            # same snapshot again: same ext_id, so the pipeline dedupes it
    assert [i.ext_id for i in again] == [items[0].ext_id]
