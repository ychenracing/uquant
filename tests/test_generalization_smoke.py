from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from research import generalization_smoke as smoke_module
from research.generalization_smoke import build_smoke_scenarios, run_generalization_smoke
from uquant.validation import generalization as generalization_module
from uquant.validation.generalization import scenario_fingerprint


def test_smoke_runner_does_not_accept_external_pre_window_evidence() -> None:
    assert "pre_window_prices" not in inspect.signature(run_generalization_smoke).parameters


def test_smoke_runner_rejects_pre_2023_before_reading_economic_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_manifest_read(_data_dir: str) -> dict[str, Any]:
        raise AssertionError("old economic interval reached data verification")

    monkeypatch.setattr(smoke_module, "verify_data_manifest", unexpected_manifest_read)

    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        run_generalization_smoke(
            data_dir="unused",
            universe=("s00",),
            industries={"s00": "optical"},
            prior_symbols=("s00",),
            start="2018-01-02",
            end="2022-12-30",
        )


def _universe() -> tuple[str, ...]:
    return tuple(f"s{index:02d}" for index in range(24))


def _industries() -> dict[str, str]:
    labels = (
        "optical",
        "memory",
        "compute",
        "equipment",
        "materials",
        "pcb",
        "datacenter",
        "semiconductor",
        "passives",
        "packaging",
        "foundry",
    )
    return {symbol: labels[index % len(labels)] for index, symbol in enumerate(_universe())}


def _prices() -> dict[str, pd.Series]:
    dates = pd.bdate_range("2025-06-02", periods=153)
    return {
        symbol: pd.Series(
            [100.0 + index + day * (index + 1) / 100.0 for day in range(len(dates))],
            index=dates,
            dtype=float,
        )
        for index, symbol in enumerate(_universe())
    }


def _manifest() -> dict[str, Any]:
    return {
        "snapshot_id": "fixture-snapshot",
        "files_verified": 26,
        "manifest_sha256": "a" * 64,
        "checksums_sha256": "b" * 64,
    }


def _registry_identity(*, sha256: str = "f" * 64) -> dict[str, str]:
    return {
        "path": "benchmarks/reference_registry.json",
        "sha256": sha256,
        "commit": "1" * 40,
        "status": "committed",
    }


def test_smoke_rejects_dirty_or_untracked_reference_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generalization_module,
        "_git_stdout",
        lambda *_args, **_kwargs: "?? benchmarks/reference_registry.json\n",
    )

    with pytest.raises(RuntimeError, match="requires committed reference registry"):
        smoke_module._reference_registry_identity(Path("/fixture"))


def test_smoke_selects_exact_deterministic_24_case_matrix_without_future_evidence() -> None:
    prices = _prices()
    expected_names = (
        "base",
        "remove_one__s00",
        "remove_one__s01",
        "remove_one__s02",
        "remove_all_priors",
        "no_optical",
        "industry_only__compute",
        "industry_only__datacenter",
        "industry_only__equipment",
        "industry_only__foundry",
        "industry_only__materials",
        "industry_only__memory",
        "industry_only__optical",
        "industry_only__packaging",
        "industry_only__passives",
        "industry_only__pcb",
        "industry_only__semiconductor",
        "balanced_industries",
        "random_06__0000",
        "random_06__0001",
        "random_12__0000",
        "random_12__0001",
        "random_24__0000",
        "random_24__0001",
    )

    first = build_smoke_scenarios(
        prices,
        _universe(),
        _industries(),
        _universe()[:3],
        window_start="2026-01-05",
    )
    future_changed = {
        symbol: pd.concat(
            [
                series,
                pd.Series(
                    [1.0, 1_000_000.0],
                    index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
                    dtype=float,
                ),
            ]
        )
        for symbol, series in prices.items()
    }
    second = build_smoke_scenarios(
        future_changed,
        _universe(),
        _industries(),
        reversed(_universe()[:3]),
        window_start="2026-01-05",
    )

    assert len(first) == 24
    assert tuple(case.name for case in first) == expected_names
    assert tuple(case.family for case in first).count("industry_only") == 11
    assert tuple(case.family for case in first).count("random") == 6
    assert scenario_fingerprint(first) == scenario_fingerprint(second)
    assert all(case.evidence_as_of < "2026-01-05" for case in first)
    assert all(case.evidence_eligible_symbols == _universe() for case in first)
    assert all(not case.evidence_ineligible_symbols for case in first)


def test_smoke_runner_covers_every_case_with_immutable_diagnostic_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_instances: list[FakeEngine] = []
    manifest_calls: list[str] = []
    data_reads: list[str] = []

    class FakeData:
        def load(self, symbol: str) -> pd.DataFrame:
            data_reads.append(symbol)
            return _prices()[symbol].to_frame("close")

    class FakeEngine:
        def __init__(self, data_dir: str) -> None:
            self.data_dir = data_dir
            self.data = FakeData()
            self.calls: list[tuple[tuple[str, ...], str, str]] = []
            engine_instances.append(self)

        def backtest(
            self,
            *,
            symbols: Iterable[str],
            start: str,
            end: str,
        ) -> Mapping[str, Any]:
            selected = tuple(symbols)
            self.calls.append((selected, start, end))
            wealth = 2.0 + len(self.calls) / 100.0
            deployed = selected[0]
            return {
                "final_wealth": wealth,
                "max_drawdown": 0.10,
                "account_orders": len(self.calls),
                "final_account": {
                    "fills": [
                        {
                            "symbol": deployed,
                            "side": "BUY",
                            "shares": 100,
                            "lifecycle": "CORE",
                            "reason_code": "leader_entry",
                        }
                    ]
                },
            }

    def verify_manifest(data_dir: str) -> dict[str, Any]:
        manifest_calls.append(data_dir)
        return _manifest()

    monkeypatch.setattr("research.generalization_smoke.ProductionEngine", FakeEngine)
    monkeypatch.setattr("research.generalization_smoke.verify_data_manifest", verify_manifest)
    monkeypatch.setattr(generalization_module, "_production_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(
        generalization_module,
        "_production_source_fingerprint",
        lambda _root: "d" * 64,
    )
    monkeypatch.setattr(smoke_module, "code_fingerprint", lambda: "e" * 64)
    monkeypatch.setattr(
        smoke_module,
        "_reference_registry_identity",
        lambda _root: _registry_identity(),
    )

    payload = run_generalization_smoke(
        data_dir="fixture-data",
        universe=_universe(),
        industries=_industries(),
        prior_symbols=_universe()[:3],
        start="2026-01-05",
        end="2026-07-20",
    )

    assert set(payload) == {
        "schema_version",
        "diagnostic_only",
        "scenario_fingerprint",
        "provenance",
        "pre_window_evidence",
        "aggregate",
        "prior_dependence",
        "observations",
    }
    assert payload["schema_version"] == 2
    assert payload["diagnostic_only"] is True
    assert payload["scenario_fingerprint"] == scenario_fingerprint(
        build_smoke_scenarios(
            _prices(),
            _universe(),
            _industries(),
            _universe()[:3],
            window_start="2026-01-05",
        )
    )
    assert payload["provenance"] == {
        "data": _manifest(),
        "dataset": {
            "universe": list(_universe()),
            "industries": dict(sorted(_industries().items())),
            "prior_symbols": ["s00", "s01", "s02"],
            "start": "2026-01-05",
            "end": "2026-07-20",
        },
        "execution": {
            "engine": "uquant.engine.ProductionEngine",
            "decision": "daily_close_t",
            "execution": "next_tradable_open",
            "intraday_exit": False,
            "prelisting": "invisible_until_first_observable_row",
            "initial_cash": 2_000_000.0,
        },
        "production": {
            "repository": "ychenracing/uquant",
            "commit": "c" * 40,
            "source_sha256": "d" * 64,
        },
        "decision_inputs": {
            "engine_code_sha256": "e" * 64,
            "reference_registry": _registry_identity(),
        },
    }
    assert payload["pre_window_evidence"] == {
        "as_of": "2025-12-31",
        "eligible_symbols": list(_universe()),
        "ineligible_symbols": [],
    }
    assert len(engine_instances) == 1
    assert data_reads == list(_universe())
    assert len(engine_instances[0].calls) == 24
    assert all(call[1:] == ("2026-01-05", "2026-07-20") for call in engine_instances[0].calls)
    assert manifest_calls == ["fixture-data", "fixture-data"]
    assert len(payload["observations"]) == 24
    assert {item["name"] for item in payload["observations"]} == {
        case.name
        for case in build_smoke_scenarios(
            _prices(),
            _universe(),
            _industries(),
            _universe()[:3],
            window_start="2026-01-05",
        )
    }
    assert all(item["deployed_exposure"] for item in payload["observations"])
    assert "thresholds" not in payload
    assert "competitor_best" not in payload
    assert "promotion" not in payload


def test_smoke_runner_rejects_data_mutation_during_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifests = iter([_manifest(), {**_manifest(), "manifest_sha256": "e" * 64}])

    class FakeData:
        def load(self, symbol: str) -> pd.DataFrame:
            return _prices()[symbol].to_frame("close")

    class FakeEngine:
        def __init__(self, _data_dir: str) -> None:
            self.data = FakeData()

        def backtest(self, *, symbols: Iterable[str], start: str, end: str) -> Mapping[str, Any]:
            selected = tuple(symbols)
            return {
                "final_wealth": 2.0,
                "max_drawdown": 0.1,
                "account_orders": 1,
                "final_account": {"fills": []},
                "symbol_pnl": {selected[0]: 1.0},
            }

    monkeypatch.setattr("research.generalization_smoke.ProductionEngine", FakeEngine)
    monkeypatch.setattr(
        "research.generalization_smoke.verify_data_manifest",
        lambda _data_dir: next(manifests),
    )
    monkeypatch.setattr(generalization_module, "_production_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(
        generalization_module,
        "_production_source_fingerprint",
        lambda _root: "d" * 64,
    )
    monkeypatch.setattr(smoke_module, "code_fingerprint", lambda: "e" * 64)
    monkeypatch.setattr(
        smoke_module,
        "_reference_registry_identity",
        lambda _root: _registry_identity(),
    )

    with pytest.raises(RuntimeError, match="source or data changed during smoke replay"):
        run_generalization_smoke(
            data_dir="fixture-data",
            universe=_universe(),
            industries=_industries(),
            prior_symbols=_universe()[:3],
            start="2026-01-05",
            end="2026-07-20",
        )


@pytest.mark.parametrize("changed", ["registry", "engine_code"])
def test_smoke_runner_rejects_decision_input_mutation_during_replay(
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    class FakeData:
        def load(self, symbol: str) -> pd.DataFrame:
            return _prices()[symbol].to_frame("close")

    class FakeEngine:
        def __init__(self, _data_dir: str) -> None:
            self.data = FakeData()

        def backtest(self, *, symbols: Iterable[str], start: str, end: str) -> Mapping[str, Any]:
            selected = tuple(symbols)
            return {
                "final_wealth": 2.0,
                "max_drawdown": 0.1,
                "account_orders": 1,
                "final_account": {"fills": []},
                "symbol_pnl": {selected[0]: 1.0},
            }

    registries = iter(
        [
            _registry_identity(),
            _registry_identity(sha256=("0" * 64 if changed == "registry" else "f" * 64)),
        ]
    )
    engine_codes = iter(["e" * 64, ("0" * 64 if changed == "engine_code" else "e" * 64)])
    monkeypatch.setattr(smoke_module, "ProductionEngine", FakeEngine)
    monkeypatch.setattr(smoke_module, "verify_data_manifest", lambda _data_dir: _manifest())
    monkeypatch.setattr(generalization_module, "_production_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(
        generalization_module,
        "_production_source_fingerprint",
        lambda _root: "d" * 64,
    )
    monkeypatch.setattr(smoke_module, "code_fingerprint", lambda: next(engine_codes))
    monkeypatch.setattr(
        smoke_module,
        "_reference_registry_identity",
        lambda _root: next(registries),
    )

    with pytest.raises(RuntimeError, match="decision inputs changed during smoke replay"):
        run_generalization_smoke(
            data_dir="fixture-data",
            universe=_universe(),
            industries=_industries(),
            prior_symbols=_universe()[:3],
            start="2026-01-05",
            end="2026-07-20",
        )
