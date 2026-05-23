"""Ensure deleted lab-only server modules are not importable."""

import importlib
import os

import pytest

os.environ.setdefault("NLS_PRODUCT_MODE", "1")

FORBIDDEN_MODULES = [
    "server.services.worker_pool",
    "server.services.slot_registry",
    "server.services.adapter_composer",
    "server.services.education_scheduler",
    "server.services.sleep_trainer",
    "server.services.deltanet_manager",
    "nls.engine.tool_onboarding",
    "nls.runtime.moe_runtime",
]


@pytest.mark.parametrize("module_name", FORBIDDEN_MODULES)
def test_forbidden_module_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
