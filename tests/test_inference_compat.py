"""Tests for micro-inference upstream helpers."""

from __future__ import annotations

from nls.runtime.inference_compat import (
    inject_no_think,
    micro_inference_extra_body,
    micro_inference_messages,
    model_is_babo_hosted_vllm,
    model_prefers_no_think,
    prepare_micro_inference,
)


def test_model_prefers_no_think_for_qwen_and_hosted():
    assert model_prefers_no_think("babo-hosted")
    assert model_prefers_no_think("qwen/qwen3.6")
    assert not model_prefers_no_think("google/gemini-2.5-flash")


def test_model_is_babo_hosted_vllm():
    assert model_is_babo_hosted_vllm("babo-hosted")
    assert not model_is_babo_hosted_vllm("google/gemini-2.5-flash")


def test_inject_no_think_on_last_user_message():
    msgs = inject_no_think([
        {"role": "system", "content": "classify"},
        {"role": "user", "content": "hello"},
    ])
    assert msgs[-1]["content"].startswith("/no_think\nhello")


def test_micro_inference_messages_adds_no_think_for_qwen():
    msgs = micro_inference_messages(
        [{"role": "user", "content": "task"}],
        model="babo-hosted",
    )
    assert msgs[0]["content"].startswith("/no_think\n")


def test_micro_inference_extra_body_babo_hosted():
    body = micro_inference_extra_body(
        "https://api.babo.agency/api/inference/v1",
        thinking=False,
        model="babo-hosted",
    )
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_micro_inference_extra_body_openrouter_disables_reasoning():
    body = micro_inference_extra_body(
        "https://openrouter.ai/api/v1",
        thinking=False,
        model="qwen/qwen3.6",
    )
    assert body["reasoning"] == {"effort": "none"}
    assert "chat_template_kwargs" not in body


def test_micro_inference_extra_body_openrouter_gemini_no_template_kwargs():
    body = micro_inference_extra_body(
        "https://api.babo.agency/api/inference/v1",
        thinking=False,
        model="google/gemini-2.5-flash",
    )
    assert body["reasoning"] == {"effort": "none"}
    assert "chat_template_kwargs" not in body


def test_micro_inference_extra_body_local_vllm():
    body = micro_inference_extra_body(
        "http://127.0.0.1:8000/v1",
        thinking=False,
        model="qwen3.6",
    )
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_prepare_micro_inference_combined():
    msgs, body = prepare_micro_inference(
        [{"role": "user", "content": "classify this"}],
        base_url="https://api.babo.agency/api/inference/v1",
        model="babo-hosted",
    )
    assert msgs[0]["content"].startswith("/no_think\n")
    assert body["chat_template_kwargs"]["enable_thinking"] is False
