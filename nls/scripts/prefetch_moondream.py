"""Pre-download the Visual Cortex VLM weights.

Run during desktop environment setup so the Visual Cortex can start
instantly at runtime.  Downloads the appropriate model for the detected
hardware into the local HuggingFace cache.

Hardware selection:
    Apple Silicon + mlx-vlm  -> FastVLM 0.5B  (mlx-community)
    Apple Silicon (no mlx)   -> SmolVLM 256M  (HuggingFace)
    CUDA >= 6 GB VRAM        -> Moondream 2B  (vikhyatk)
    Otherwise                -> SmolVLM 256M  (HuggingFace)

Prints structured PREFETCH: lines for the Electron VenvManager to
parse and relay to the setup progress bar.

Usage (called by VenvManager via runInVenv):
    python -m nls.scripts.prefetch_moondream
"""
from __future__ import annotations

import sys


def _detect() -> tuple[str, str]:
    """Return (model_id, backend_name) for the current hardware."""
    device = "cpu"
    try:
        import torch

        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
    except ImportError:
        pass

    if device == "mps":
        try:
            import mlx_vlm  # noqa: F401

            return "mlx-community/FastVLM-0.5B-bf16", "fastvlm-mlx"
        except ImportError:
            return "HuggingFaceTB/SmolVLM-256M-Instruct", "smolvlm"

    if device == "cuda":
        try:
            import torch

            vram = torch.cuda.get_device_properties(0).total_mem / (1024 ** 3)
            if vram >= 6.0:
                return "vikhyatk/moondream2", "moondream"
        except Exception:
            pass
        return "HuggingFaceTB/SmolVLM-256M-Instruct", "smolvlm"

    return "HuggingFaceTB/SmolVLM-256M-Instruct", "smolvlm"


def main() -> None:
    model_id, backend = _detect()
    print(f"PREFETCH:starting:{backend} model download", flush=True)
    print(f"PREFETCH:info:Downloading {model_id} (backend={backend})", flush=True)

    try:
        if backend == "fastvlm-mlx":
            from mlx_vlm import load

            print("PREFETCH:downloading:fetching FastVLM weights (MLX)...", flush=True)
            load(model_id)

        elif backend == "moondream":
            from transformers import AutoModelForCausalLM

            print("PREFETCH:downloading:fetching Moondream weights...", flush=True)
            AutoModelForCausalLM.from_pretrained(
                model_id,
                revision="2025-01-09",
                trust_remote_code=True,
                device_map={"": "cpu"},
            )

        else:
            from transformers import AutoModelForVision2Seq, AutoProcessor

            print("PREFETCH:downloading:fetching SmolVLM weights...", flush=True)
            AutoProcessor.from_pretrained(model_id)
            AutoModelForVision2Seq.from_pretrained(model_id)

        print("PREFETCH:done", flush=True)

    except ImportError as exc:
        print(
            f"PREFETCH:skip:required packages not installed ({exc})",
            flush=True,
        )
    except Exception as exc:
        print(f"PREFETCH:error:{exc}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
