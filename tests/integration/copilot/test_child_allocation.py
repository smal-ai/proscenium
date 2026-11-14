import json
import runpy
from pathlib import Path

import pytest


def test_native_child_budget_is_shared_but_callback_deduplication_is_root_scoped(
    tmp_path, monkeypatch
):
    namespace = runpy.run_path(str(Path("tests/native/copilot/production_child_consumption.py")))
    reserve = namespace["reserve"]
    ledger = tmp_path / "allocation.jsonl"
    monkeypatch.setitem(reserve.__globals__, "LEDGER", ledger)
    first, second = tmp_path / "proof-one", tmp_path / "proof-two"
    reserve("child", "same-native-tool-id", first)
    reserve("child", "same-native-tool-id", first)
    reserve("child", "same-native-tool-id", second)
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [row["root"] for row in rows] == [first.name, second.name]
    with pytest.raises(RuntimeError, match="allocation is exhausted"):
        reserve("child", "third-tool-id", second)
