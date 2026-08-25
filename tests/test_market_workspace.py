from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.data import DataStore
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.leader import REFERENCE_UNIVERSE, STABLE_REFERENCE_UNIVERSE
from uquant.market import MarketWorkspace, ReplayHarness, ReplayUniverse

ROOT = Path(__file__).parents[1]
BASELINE_PATH = ROOT / "tests" / "fixtures" / "market_contract_baseline.json"


def _baseline() -> dict[str, Any]:
    value = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _frame_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(
        index=True,
        date_format="%Y-%m-%d",
        float_format="%.17g",
        na_rep="NaN",
        lineterminator="\n",
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _captured(call: Any) -> dict[str, str]:
    try:
        call()
    except Exception as exc:
        return {"type": type(exc).__name__, "message": str(exc)}
    raise AssertionError("characterization call did not raise")


def _outcome(call: Any) -> tuple[str, object]:
    try:
        return ("return", call())
    except Exception as exc:
        return ("exception", (type(exc).__name__, str(exc)))


def _immutable_task1_price() -> Any:
    metadata = _baseline()["baseline"]
    source = subprocess.run(
        ["git", "show", f"{metadata['commit']}:uquant/engine.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tree = ast.parse(source, filename="immutable-task1:uquant/engine.py")
    engine = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProductionEngine"
    )
    price = next(
        node
        for node in engine.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_price"
    )
    namespace: dict[str, object] = {"pd": pd}
    exec(compile(ast.Module(body=[price], type_ignores=[]), "<immutable-task1-price>", "exec"), namespace)
    return namespace["_price"]


def _production_universe(*tradable: str) -> ReplayUniverse:
    return ReplayUniverse.from_symbols(
        tradable_symbols=tradable,
        reference_symbols=REFERENCE_UNIVERSE,
        index_symbols=INDEX_SYMBOLS,
    )


def test_market_fixture_is_anchored_to_immutable_task1_bytes() -> None:
    fixture = _baseline()
    metadata = fixture["baseline"]
    assert metadata == {
        "commit": "f9fd489806a86b3a56f62b8668aafa252012d405",
        "engine_blob": "4bc2917f3c5e06943a5f1f17ebb520619163b6b0",
        "data_tree_sha256": "40383be1fb85aad22b082170467a93c4b45fd7a7cdd9037c8a7339e19bf96a7b",
    }
    observed_blob = subprocess.run(
        ["git", "rev-parse", f"{metadata['commit']}:uquant/engine.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert observed_blob == metadata["engine_blob"]
    inventory = json.loads(
        (ROOT / "artifacts" / "architecture_refactor" / "baseline_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    assert inventory["baseline"]["commit"] == metadata["commit"]
    assert inventory["baseline"]["data"]["tree_sha256"] == metadata["data_tree_sha256"]


def test_replay_universe_is_canonical_immutable_and_fail_closed() -> None:
    universe = ReplayUniverse.from_symbols(
        tradable_symbols=("300308", "SZ300308", "sh688008"),
        reference_symbols=("sh600487", "SH600487"),
        index_symbols=("000682", "000300"),
    )
    assert universe.tradable_symbols == ("sh688008", "sz300308")
    assert universe.reference_symbols == ("sh600487",)
    assert universe.index_symbols == ("sh000300", "sh000682")
    # Hand-derived from strict UTF-8 JSON with sorted object keys and compact
    # separators; the implementation under test does not author this literal.
    assert universe.identity_sha256 == "4c7451dbfb67783cf389c38de844b03a54fbf1ccc47d54c0a5eab916423010ee"
    assert universe.__slots__ == (
        "tradable_symbols",
        "reference_symbols",
        "index_symbols",
        "identity_sha256",
    )
    with pytest.raises(AttributeError):
        universe.identity_sha256 = "0" * 64  # type: ignore[misc]
    with pytest.raises(ValueError, match="identity"):
        ReplayUniverse(
            tradable_symbols=universe.tradable_symbols,
            reference_symbols=universe.reference_symbols,
            index_symbols=universe.index_symbols,
            identity_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="canonical"):
        ReplayUniverse(
            tradable_symbols=("sz300308", "sh688008"),
            reference_symbols=("sh600487",),
            index_symbols=("sh000300", "sh000682"),
            identity_sha256=universe.identity_sha256,
        )


def test_production_universe_identity_preserves_fixed_reference_contract() -> None:
    universe = _production_universe("sz300308")
    assert REFERENCE_UNIVERSE is STABLE_REFERENCE_UNIVERSE
    assert universe.reference_symbols == REFERENCE_UNIVERSE
    assert universe.index_symbols == INDEX_SYMBOLS
    assert DEFAULT_CONFIG.to_dict().get("reference_universe") is None


def test_workspace_load_features_price_sessions_and_manifest_match_task1_baseline() -> None:
    expected = _baseline()
    workspace = MarketWorkspace(ROOT / "data" / "frozen", DEFAULT_CONFIG)
    workspace.prepare(
        ReplayUniverse.from_symbols(
            tradable_symbols=("300308",),
            reference_symbols=(),
            index_symbols=("000682", "000300"),
        )
    )
    first_raw = workspace._owned_raw_frame("sz300308")
    first_features = workspace._owned_feature_frame("sz300308")
    workspace.prepare(
        ReplayUniverse.from_symbols(
            tradable_symbols=("SZ300308",),
            reference_symbols=(),
            index_symbols=("sh000300", "sh000682"),
        )
    )

    load = expected["load"]
    assert workspace.loaded_symbols == tuple(load["keys"])
    assert workspace._owned_raw_frame("sz300308") is first_raw
    assert workspace._owned_feature_frame("sz300308") is first_features
    assert list(first_raw.shape) == load["raw_shape"]
    assert list(first_raw.columns) == load["raw_columns"]
    assert _frame_sha256(first_raw) == load["raw_sha256"]
    assert list(first_features.shape) == load["feature_shape"]
    assert _frame_sha256(first_features) == load["feature_sha256"]
    assert str(first_raw.index.min().date()) == load["index_first"]
    assert str(first_raw.index.max().date()) == load["index_last"]

    prices = expected["price"]
    assert workspace.price("sz300308", "2023-01-07") == prices["latest_before"]
    assert workspace.price("sz300308", "2023-01-06", "open") == prices["open_exact"]
    assert _captured(lambda: workspace.price("sz300308", "2000-01-01")) == prices[
        "before_first"
    ]
    assert _captured(lambda: workspace.price("sh999999", "2023-01-01")) == prices[
        "missing_symbol"
    ]
    assert _captured(lambda: workspace.price("sz300308", "2023-01-06", "missing")) == prices[
        "missing_field"
    ]

    sessions = workspace.common_sessions("sh000300", "sh000682")
    assert len(sessions) == expected["common_sessions"]["count"]
    assert str(sessions[0].date()) == expected["common_sessions"]["first"]
    assert str(sessions[-1].date()) == expected["common_sessions"]["last"]


@pytest.mark.parametrize(
    ("symbol", "date", "field"),
    (
        ("sz300308", pd.Timestamp("2023-01-07"), "close"),
        ("sz300308", pd.Timestamp("2023-01-06"), "open"),
        ("SZ300308", pd.Timestamp("2023-01-07"), "close"),
        ("300308", pd.Timestamp("2023-01-07"), "close"),
        ("sh999999", pd.Timestamp("2023-01-07"), "close"),
        ("SZ300308", pd.Timestamp("2000-01-01"), "missing"),
        ("sh999999", pd.Timestamp("2000-01-01"), "missing"),
        ("sz300308", pd.Timestamp("2000-01-01"), "missing"),
        ("sz300308", pd.Timestamp("2023-01-07"), "missing"),
    ),
)
def test_workspace_price_matches_immutable_task1_symbol_date_field_order(
    symbol: str,
    date: pd.Timestamp,
    field: str,
) -> None:
    raw = DataStore(ROOT / "data" / "frozen").load("sz300308")
    baseline = SimpleNamespace(_raw={"sz300308": raw})
    task1_price = _immutable_task1_price()
    workspace = MarketWorkspace(ROOT / "data" / "frozen", DEFAULT_CONFIG)
    workspace.load(("sz300308",))

    expected = _outcome(lambda: task1_price(baseline, symbol, date, field))
    observed = _outcome(lambda: workspace.price(symbol, date, field))
    assert observed == expected


def test_workspace_reference_returns_and_manifest_match_task1_baseline() -> None:
    expected = _baseline()
    universe = _production_universe("sz300308")
    workspace = MarketWorkspace(ROOT / "data" / "frozen", DEFAULT_CONFIG)
    workspace.prepare(universe)
    returns = workspace.reference_returns()
    reference = expected["reference_returns"]
    assert list(returns.shape) == reference["shape"]
    assert tuple(returns.columns) == REFERENCE_UNIVERSE
    assert _frame_sha256(returns) == reference["sha256"]
    assert str(returns.index.min().date()) == reference["index_first"]
    assert str(returns.index.max().date()) == reference["index_last"]
    assert returns.iloc[0].isna().all()

    manifest = workspace.manifest(universe.all_symbols, as_of="2026-06-30")
    sealed = expected["manifest"]
    assert manifest.digest == sealed["digest"]
    assert manifest.start == sealed["start"]
    assert manifest.end == sealed["end"]
    assert len(manifest.symbols) == sealed["symbol_count"]
    assert workspace.manifest(universe.all_symbols, as_of="2026-06-30") is manifest


@pytest.mark.parametrize("kind", ("raw", "feature", "reference"))
def test_public_frames_cannot_mutate_workspace_owned_economic_state(kind: str) -> None:
    workspace = MarketWorkspace(ROOT / "data" / "frozen", DEFAULT_CONFIG)
    workspace.prepare(_production_universe("sz300308"))
    date = pd.Timestamp("2026-06-30")
    if kind == "raw":
        public = workspace.raw_frame("sz300308")
        owned = workspace._owned_raw_frame("sz300308")
    elif kind == "feature":
        public = workspace.feature_frame("sz300308")
        owned = workspace._owned_feature_frame("sz300308")
    else:
        public = workspace.reference_returns()
        owned = workspace._owned_reference_returns()
    before = owned.copy(deep=True)
    public.iloc[:, :] = -999.0
    public.drop(public.index, inplace=True)
    pd.testing.assert_frame_equal(owned, before)
    assert workspace.price("sz300308", date) == pytest.approx(1270.0)


def test_engine_owns_workspace_and_legacy_test_seams_are_branch_free_aliases() -> None:
    engine = ProductionEngine(ROOT / "data" / "frozen")
    assert engine.workspace.data is engine.data
    assert engine._raw is engine.workspace._raw
    assert engine._features is engine.workspace._features
    engine._load(("sz300308",))
    assert engine._price("sz300308", pd.Timestamp("2023-01-07")) == engine.workspace.price(
        "sz300308", "2023-01-07"
    )


def test_rebinding_frozen_engine_data_attribute_updates_workspace_authority() -> None:
    engine = ProductionEngine(ROOT / "data" / "frozen")
    replacement = DataStore(ROOT / "data" / "frozen")
    engine.data = replacement
    assert engine.data is replacement
    assert engine.workspace.data is replacement
    assert engine._raw == {}
    engine._load(("sz300308",))
    assert engine.workspace._owned_raw_frame("sz300308") is engine._raw["sz300308"]


def test_replay_harness_prepares_explicit_universe_without_engine_private_frames() -> None:
    workspace = MarketWorkspace(ROOT / "data" / "frozen", DEFAULT_CONFIG)
    universe = _production_universe("sz300308")
    harness = ReplayHarness(workspace=workspace, universe=universe)
    sessions = harness.sessions(start="2026-06-30", end="2026-07-03")
    panel = harness.raw_panel(("sz300308",))
    assert tuple(str(item.date()) for item in sessions) == (
        "2026-06-30",
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    )
    assert tuple(panel) == ("sz300308",)
    panel["sz300308"].iloc[:, :] = -1.0
    assert workspace.price("sz300308", "2026-07-03") == pytest.approx(1116.0)
