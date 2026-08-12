from __future__ import annotations

import importlib.util
import sys
from email.message import Message
from pathlib import Path
from types import ModuleType

import pytest


def _load_backfill_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_tencent_history.py"
    spec = importlib.util.spec_from_file_location("uquant_test_backfill_tencent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Tencent backfill module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_backfill_module()


def test_tencent_redirect_handler_rejects_cross_origin_destination() -> None:
    with pytest.raises(backfill.HTTPError, match="refusing cross-origin Tencent redirect"):
        backfill._TencentRedirectHandler().redirect_request(
            backfill.Request(backfill.ENDPOINT),
            None,
            302,
            "Found",
            Message(),
            "http://127.0.0.1/internal",
        )


def test_tencent_redirect_handler_accepts_same_https_origin() -> None:
    redirected = backfill._TencentRedirectHandler().redirect_request(
        backfill.Request(backfill.ENDPOINT),
        None,
        302,
        "Found",
        Message(),
        backfill.ENDPOINT + "?redirected=1",
    )
    assert redirected is not None
    assert redirected.full_url.startswith(backfill.ENDPOINT)
