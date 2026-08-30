"""Agent auto-load model compatibility for remote / BYO inference."""

from server.services.agent_manager import _models_compatible


def test_remote_byo_compatible_with_babo_hosted():
    assert _models_compatible("bring-your-own", "babo-hosted")
    assert _models_compatible("byo", "babo-hosted")
    assert _models_compatible("babo-hosted", "bring-your-own")


def test_openrouter_slug_compatible_with_byo():
    assert _models_compatible("bring-your-own", "google/gemini-2.5-flash")
    assert _models_compatible("google/gemini-2.5-flash", "babo-hosted")


def test_local_hf_architectures_still_filter():
    assert _models_compatible("Qwen/Qwen3-32B", "unsloth/Qwen3-32B-bnb-4bit")
    assert not _models_compatible("Qwen/Qwen3-32B", "meta-llama/Llama-3.1-8B")
    assert not _models_compatible("Qwen3-32B", "Llama-3.1-8B")
