"""Tests for hybrid LAN/cloud inference routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from server.routes.chat.helpers import normalize_model_route


def test_normalize_model_route():
    assert normalize_model_route("local") == "local"
    assert normalize_model_route("CLOUD") == "cloud"
    assert normalize_model_route("  cloud  ") == "cloud"
    assert normalize_model_route("invalid") is None
    assert normalize_model_route(None) is None
    assert normalize_model_route("") is None


class _FakeClient:
    def __init__(self, base_url: str, default_model: str = ""):
        self.base_url = base_url
        self.default_model = default_model


@pytest.fixture
def runtime_cls():
    from nls.runtime.agent_runtime import AgentRuntime

    return AgentRuntime


def _minimal_runtime(runtime_cls, *, vllm_base: str, env: dict):
    with patch.dict("os.environ", env, clear=False):
        rt = runtime_cls.__new__(runtime_cls)
        rt.agent_id = "test-agent"
        rt.vllm_client = _FakeClient(vllm_base, env.get("NLS_HF_MODEL", "gpt-4o-mini"))
        rt._babo_cloud_vllm_client = None
        rt._lan_vllm_client = None
        rt._active_inference_route = None
        rt.session_orchestrator_model = None
        rt.session_orchestrator_route = None
        rt.session_delegate_model = None
        rt.session_delegate_route = None
        rt.session_delegate_lock_orchestrator = True
        rt.delegate_model = None
        return rt


def test_babo_hosted_always_routes_cloud(runtime_cls):
    cloud = _FakeClient("https://api.babo.agency/api/inference/v1")
    rt = _minimal_runtime(
        runtime_cls,
        vllm_base="http://192.168.1.10:8000/v1",
        env={
            "NLS_BABO_CLOUD_INFERENCE_URL": "https://api.babo.agency/api/inference/v1",
            "NLS_LAN_INFERENCE_URL": "http://192.168.1.10:8000/v1",
        },
    )
    rt._babo_cloud_vllm_client = cloud

    client, adapter = rt._vllm_for_message("babo-hosted", "local")
    assert client is cloud
    assert adapter == "babo-hosted"


def test_explicit_local_route_uses_lan_client(runtime_cls):
    lan = _FakeClient("http://192.168.1.10:8000/v1")
    rt = _minimal_runtime(
        runtime_cls,
        vllm_base="https://api.babo.agency/api/inference/v1",
        env={
            "NLS_LAN_INFERENCE_URL": "http://192.168.1.10:8000/v1",
            "NLS_BABO_CLOUD_INFERENCE_URL": "https://api.babo.agency/api/inference/v1",
        },
    )
    rt._lan_vllm_client = lan

    client, adapter = rt._vllm_for_message("Qwen/Qwen3.6-35B-A3B-FP8", "local")
    assert client is lan
    assert adapter == "Qwen/Qwen3.6-35B-A3B-FP8"


def test_hybrid_heuristic_routes_lan_model_without_explicit_route(runtime_cls):
    lan = _FakeClient(
        "http://192.168.1.10:8000/v1",
        default_model="Qwen/Qwen3.6-35B-A3B-FP8",
    )
    rt = _minimal_runtime(
        runtime_cls,
        vllm_base="https://api.babo.agency/api/inference/v1",
        env={
            "NLS_HF_MODEL": "google/gemini-2.5-flash",
            "NLS_LAN_INFERENCE_URL": "http://192.168.1.10:8000/v1",
            "NLS_BABO_CLOUD_INFERENCE_URL": "https://api.babo.agency/api/inference/v1",
        },
    )
    rt._lan_vllm_client = lan
    rt.session_orchestrator_model = "Qwen/Qwen3.6-35B-A3B-FP8"
    rt.session_orchestrator_route = "local"

    client, _ = rt._vllm_for_message("Qwen/Qwen3.6-35B-A3B-FP8", None)
    assert client is lan


def test_delegate_route_when_unlocked(runtime_cls):
    lan = _FakeClient("http://192.168.1.10:8000/v1")
    cloud = _FakeClient("https://api.babo.agency/api/inference/v1")
    rt = _minimal_runtime(
        runtime_cls,
        vllm_base="https://api.babo.agency/api/inference/v1",
        env={
            "NLS_LAN_INFERENCE_URL": "http://192.168.1.10:8000/v1",
            "NLS_BABO_CLOUD_INFERENCE_URL": "https://api.babo.agency/api/inference/v1",
        },
    )
    rt._lan_vllm_client = lan
    rt._babo_cloud_vllm_client = cloud
    rt.session_delegate_lock_orchestrator = False
    rt.session_delegate_model = "Qwen/Qwen3.6-35B-A3B-FP8"
    rt.session_delegate_route = "local"

    del_client, del_adapter = rt.delegate_inference_pipeline("google/gemini-2.5-flash")
    assert del_client is lan
    assert del_adapter == "Qwen/Qwen3.6-35B-A3B-FP8"
