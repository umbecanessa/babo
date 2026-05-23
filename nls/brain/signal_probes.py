"""V5 Neural Signal Sensing — probe classifiers on frozen hidden states.

Small linear heads that read the model's hidden states to detect
signal-worthy moments (LEARN, EVALUATE, UNKNOWN, etc.) without
requiring the model to emit text tags.  This eliminates the behavior
adapter's interference with native tool calling and thinking.

The thalamus already proved that hidden-state observation works
(delta_ratio for meta_weight routing).  Signal probes extend the
same principle to the full signal vocabulary.

Architecture::

    Hidden States (last layer, mean-pooled)
        │
        ▼
    Linear(hidden_dim, 256) → ReLU → Dropout → Linear(256, n_signals) → Sigmoid
        │
        ▼
    Signal Vector: {LEARN: 0.87, UNKNOWN: 0.12, EVAL_correct: 0.03, ...}

Usage::

    from nls.brain.signal_probes import SignalProbeBank, load_probe_bank

    bank = load_probe_bank("/path/to/probe_weights.pt", config)
    signals = bank.predict(hidden_states)
    # signals = {"LEARN": 0.87, "UNKNOWN": 0.12, ...}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    from torch import Tensor

    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False
    nn = None  # type: ignore[assignment]
    Tensor = Any  # type: ignore[assignment,misc]


# ── Default config ──────────────────────────────────────────────────────

DEFAULT_SIGNALS = [
    "LEARN",
    "UNKNOWN",
    "LOOKUP_RECALL",
    "EVAL_POSITIVE",
    "EVAL_NEGATIVE",
    "EVAL_UNCERTAIN",
    "CURIOSITY",
    "FOCUS",
    "BONDING",
    "PLAN",
    "REFLECT",
    "DOUBT",
]

DEFAULT_THRESHOLDS: dict[str, float] = {
    "LEARN": 0.7,
    "UNKNOWN": 0.6,
    "LOOKUP_RECALL": 0.5,
    "EVAL_POSITIVE": 0.6,
    "EVAL_NEGATIVE": 0.7,
    "EVAL_UNCERTAIN": 0.6,
    "CURIOSITY": 0.5,
    "FOCUS": 0.5,
    "BONDING": 0.5,
    "PLAN": 0.5,
    "REFLECT": 0.5,
    "DOUBT": 0.6,
}

# Maps individual text tags to probe categories.
# Built from the signal_taxonomy_mapping in signal_probes.json.
TAG_TO_PROBE: dict[str, str] = {
    # LEARN
    "LEARN": "LEARN",
    # UNKNOWN
    "UNKNOWN": "UNKNOWN",
    # LOOKUP_RECALL
    "LOOKUP": "LOOKUP_RECALL",
    "RECALL:hit": "LOOKUP_RECALL",
    "RECALL:miss": "LOOKUP_RECALL",
    "RECALL_HIT": "LOOKUP_RECALL",
    "RECALL_MISS": "LOOKUP_RECALL",
    # EVAL_POSITIVE
    "EVALUATE:correct": "EVAL_POSITIVE",
    "EVALUATE:revised": "EVAL_POSITIVE",
    "EVALUATE:insightful": "EVAL_POSITIVE",
    "EVALUATE:understanding": "EVAL_POSITIVE",
    "EVALUATE:grasping": "EVAL_POSITIVE",
    "EVALUATE:crystallizing": "EVAL_POSITIVE",
    "EVALUATE:learning": "EVAL_POSITIVE",
    "EVALUATE:proud": "EVAL_POSITIVE",
    # EVAL_NEGATIVE
    "EVALUATE:incorrect": "EVAL_NEGATIVE",
    "EVALUATE:frustrated": "EVAL_NEGATIVE",
    "EVALUATE:struggling": "EVAL_NEGATIVE",
    "EVALUATE:overwhelmed": "EVAL_NEGATIVE",
    "EVALUATE:anxious": "EVAL_NEGATIVE",
    "EVALUATE:disappointed": "EVAL_NEGATIVE",
    # EVAL_UNCERTAIN
    "EVALUATE:uncertain": "EVAL_UNCERTAIN",
    "EVALUATE:confused": "EVAL_UNCERTAIN",
    "EVALUATE:conflicted": "EVAL_UNCERTAIN",
    # CURIOSITY
    "EVALUATE:curious": "CURIOSITY",
    "EVALUATE:intrigued": "CURIOSITY",
    "EVALUATE:wondering": "CURIOSITY",
    "EVALUATE:surprised": "CURIOSITY",
    "ACC": "CURIOSITY",
    # FOCUS
    "EVALUATE:processing": "FOCUS",
    "EVALUATE:synthesizing": "FOCUS",
    "EVALUATE:connecting": "FOCUS",
    "INSULA": "FOCUS",
    # BONDING
    "EVALUATE:warm": "BONDING",
    "EVALUATE:pleased": "BONDING",
    "EVALUATE:grateful": "BONDING",
    "EVALUATE:moved": "BONDING",
    "EVALUATE:tender": "BONDING",
    "EVALUATE:amused": "BONDING",
    "EVALUATE:playful": "BONDING",
    "EVALUATE:inspired": "BONDING",
    "EVALUATE:nostalgic": "BONDING",
    "EVALUATE:ironic": "BONDING",
    "BONDING": "BONDING",
    "CLOSER": "BONDING",
    # PLAN
    "PLAN:create": "PLAN",
    "PLAN:step": "PLAN",
    "PLAN_CREATE": "PLAN",
    "PLAN_STEP": "PLAN",
    # REFLECT
    "REFLECT": "REFLECT",
    "CONNECT": "REFLECT",
    "EVALUATE:aligned": "REFLECT",
    # DOUBT
    "DOUBT": "DOUBT",
    "EVALUATE:skeptical": "DOUBT",
    "EVALUATE:wary": "DOUBT",
    "EVALUATE:melancholic": "DOUBT",
}


# ── Probe bank (PyTorch module) ────────────────────────────────────────

class SignalProbeBank(nn.Module if _TORCH_AVAILABLE else object):  # type: ignore[misc]
    """Bank of linear probe classifiers over frozen hidden states.

    One forward pass produces independent sigmoid activations for each
    signal type.  Trained with BCE loss on labeled conversation data
    where existing text-tag signals serve as ground truth.

    Parameters
    ----------
    hidden_dim : int
        Dimensionality of the transformer hidden states (e.g. 5120 for
        Qwen3-32B).
    signal_names : list[str]
        Ordered list of signal names this bank detects.
    intermediate_dim : int
        Width of the hidden layer (default 256).
    dropout : float
        Dropout rate between layers (default 0.1).
    """

    def __init__(
        self,
        hidden_dim: int = 5120,
        signal_names: list[str] | None = None,
        intermediate_dim: int = 256,
        dropout: float = 0.1,
    ) -> None:
        if not _TORCH_AVAILABLE:
            raise RuntimeError("PyTorch is required for SignalProbeBank")
        super().__init__()
        self.signal_names = signal_names or list(DEFAULT_SIGNALS)
        self.hidden_dim = hidden_dim
        n_signals = len(self.signal_names)

        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim, intermediate_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(intermediate_dim, n_signals),
            nn.Sigmoid(),
        )

    def forward(self, hidden_states: Tensor) -> Tensor:
        """Run probes on hidden states.

        Parameters
        ----------
        hidden_states : Tensor
            Shape ``[seq_len, hidden_dim]`` (single example, already
            extracted from the last transformer layer) or
            ``[batch, seq_len, hidden_dim]``.

        Returns
        -------
        Tensor
            Shape ``[n_signals]`` or ``[batch, n_signals]`` with sigmoid
            activations in ``[0, 1]``.
        """
        if hidden_states.dim() == 2:
            pooled = hidden_states.mean(dim=0)
        elif hidden_states.dim() == 3:
            pooled = hidden_states.mean(dim=1)
        else:
            raise ValueError(
                f"Expected 2D or 3D hidden_states, got {hidden_states.dim()}D"
            )
        return self.classifier(pooled)

    def forward_pooled(self, pooled: Tensor) -> Tensor:
        """Run probes on already-pooled hidden states.

        Use this when hidden states have already been mean-pooled (e.g.
        during training where ``_collect_hidden_states`` pre-pools).

        Parameters
        ----------
        pooled : Tensor
            Shape ``[hidden_dim]`` (single) or ``[batch, hidden_dim]``
            (batched).  Already mean-pooled — no further pooling applied.

        Returns
        -------
        Tensor
            Shape ``[n_signals]`` or ``[batch, n_signals]``.
        """
        return self.classifier(pooled)

    def predict(self, hidden_states: Tensor) -> dict[str, float]:
        """Convenience: run forward and return a named dict.

        Parameters
        ----------
        hidden_states : Tensor
            Shape ``[seq_len, hidden_dim]`` from one example.

        Returns
        -------
        dict[str, float]
            Mapping of signal name to activation (0.0–1.0).
        """
        with torch.no_grad():
            activations = self.forward(hidden_states)
        if activations.dim() == 1:
            return {
                name: act.item()
                for name, act in zip(self.signal_names, activations)
            }
        return {
            name: act.item()
            for name, act in zip(self.signal_names, activations[0])
        }

    def predict_batch(self, hidden_states: Tensor) -> list[dict[str, float]]:
        """Batch prediction.

        Parameters
        ----------
        hidden_states : Tensor
            Shape ``[batch, seq_len, hidden_dim]``.

        Returns
        -------
        list[dict[str, float]]
            One signal dict per batch element.
        """
        with torch.no_grad():
            activations = self.forward(hidden_states)
        results = []
        for row in activations:
            results.append({
                name: act.item()
                for name, act in zip(self.signal_names, row)
            })
        return results


# ── Thresholding ────────────────────────────────────────────────────────

def threshold_signals(
    signal_vector: dict[str, float],
    thresholds: dict[str, float] | None = None,
) -> dict[str, bool]:
    """Apply thresholds to a signal vector.

    Returns a dict of signal_name -> whether it fired (above threshold).
    """
    thresholds = thresholds or DEFAULT_THRESHOLDS
    return {
        name: value >= thresholds.get(name, 0.5)
        for name, value in signal_vector.items()
    }


def fired_signals(
    signal_vector: dict[str, float],
    thresholds: dict[str, float] | None = None,
) -> list[str]:
    """Return only signal names that exceeded their threshold."""
    return [
        name
        for name, fired in threshold_signals(signal_vector, thresholds).items()
        if fired
    ]


# ── Loading / saving ───────────────────────────────────────────────────

def load_probe_config(config_path: str | Path | None = None) -> dict:
    """Load signal probe configuration.

    Falls back to bundled ``nls/config/signal_probes.json``.
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "signal_probes.json"
    config_path = Path(config_path)
    if not config_path.exists():
        logger.warning("Signal probe config not found at %s, using defaults", config_path)
        return {
            "probe_signals": list(DEFAULT_SIGNALS),
            "thresholds": dict(DEFAULT_THRESHOLDS),
            "model": {
                "hidden_dim": 5120,
                "intermediate_dim": 256,
                "dropout": 0.1,
            },
        }
    with open(config_path) as f:
        return json.load(f)


def load_probe_bank(
    weights_path: str | Path,
    config: dict | None = None,
    device: str = "cpu",
) -> SignalProbeBank:
    """Load a trained probe bank from disk.

    Parameters
    ----------
    weights_path : str | Path
        Path to the saved ``.pt`` state dict.
    config : dict | None
        Probe config dict (from ``load_probe_config``).  If None,
        loads the default config.
    device : str
        Target device.

    Returns
    -------
    SignalProbeBank
        Loaded and eval-ready probe bank.
    """
    if not _TORCH_AVAILABLE:
        raise RuntimeError("PyTorch is required to load probe bank")

    config = config or load_probe_config()
    model_cfg = config.get("model", {})

    bank = SignalProbeBank(
        hidden_dim=model_cfg.get("hidden_dim", 5120),
        signal_names=config.get("probe_signals", list(DEFAULT_SIGNALS)),
        intermediate_dim=model_cfg.get("intermediate_dim", 256),
        dropout=model_cfg.get("dropout", 0.1),
    )

    weights_path = Path(weights_path)
    if weights_path.exists():
        state_dict = torch.load(weights_path, map_location=device, weights_only=True)
        bank.load_state_dict(state_dict)
        logger.info("Loaded signal probe weights from %s", weights_path)
    else:
        logger.warning(
            "Signal probe weights not found at %s — using untrained probes",
            weights_path,
        )

    bank = bank.to(device)
    bank.eval()
    return bank


def save_probe_bank(bank: SignalProbeBank, weights_path: str | Path) -> None:
    """Save probe bank weights to disk."""
    weights_path = Path(weights_path)
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(bank.state_dict(), weights_path)
    logger.info("Saved signal probe weights to %s", weights_path)


# ── Hidden state extraction helpers ────────────────────────────────────

def extract_hidden_states(
    model: Any,
    tokenizer: Any,
    text: str,
    layers: list[int] | None = None,
    max_length: int = 4096,
    device: str | None = None,
) -> Tensor:
    """Run a forward pass and extract hidden states for probing.

    This is the post-generation sensing pass: given the full
    prompt+response text, extract the hidden states that signal
    probes will classify.

    Parameters
    ----------
    model : PreTrainedModel or PeftModel
        The base model (adapters should be disabled for clean reads).
    tokenizer : PreTrainedTokenizer
        Tokenizer for encoding text.
    text : str
        Full text (prompt + response) to sense.
    layers : list[int] | None
        Which hidden state layers to return (default: [-1] = last layer).
    max_length : int
        Max tokenization length.
    device : str | None
        Override device; defaults to model's device.

    Returns
    -------
    Tensor
        Hidden states of shape ``[seq_len, hidden_dim]`` (last layer,
        single example).
    """
    if not _TORCH_AVAILABLE:
        raise RuntimeError("PyTorch is required for hidden state extraction")

    layers = layers or [-1]
    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=max_length,
    )
    if device:
        inputs = {k: v.to(device) for k, v in inputs.items()}
    else:
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs, output_hidden_states=True)

    all_hidden = outputs.hidden_states
    if len(layers) == 1:
        hs = all_hidden[layers[0]][0]  # [seq_len, hidden_dim]
    else:
        hs = torch.cat(
            [all_hidden[i][0] for i in layers], dim=-1,
        )  # [seq_len, hidden_dim * n_layers]

    return hs.float()


def extract_hidden_states_from_outputs(
    model_outputs: Any,
    layers: list[int] | None = None,
) -> Tensor:
    """Extract hidden states from already-computed model outputs.

    Useful when piggybacking on a forward pass that already ran
    (e.g. the thalamus sense pass).
    """
    if not _TORCH_AVAILABLE:
        raise RuntimeError("PyTorch required")

    layers = layers or [-1]
    all_hidden = model_outputs.hidden_states
    if len(layers) == 1:
        hs = all_hidden[layers[0]][0]
    else:
        hs = torch.cat([all_hidden[i][0] for i in layers], dim=-1)
    return hs.float()


# ── Training label extraction from text-tagged data ────────────────────

import re

_SIGNAL_TAG_RE = re.compile(r"\[([A-Za-z_]+)(?:[:.]([^\]]*))?\]")


def extract_labels_from_output(
    output_text: str,
    signal_names: list[str] | None = None,
) -> dict[str, float]:
    """Parse text-tag signals from a training pair output and map to probe labels.

    Given a training output like::

        [LEARN:User.Name|Umberto] [EVALUATE:pleased]
        That's fantastic — congratulations!

    Returns a binary label dict for probe categories::

        {"LEARN": 1.0, "BONDING": 1.0, "UNKNOWN": 0.0, ...}

    Parameters
    ----------
    output_text : str
        The ``output`` field from a training pair (Alpaca format).
    signal_names : list[str] | None
        Probe category names (default: ``DEFAULT_SIGNALS``).

    Returns
    -------
    dict[str, float]
        Binary labels (0.0 or 1.0) per probe category.
    """
    signal_names = signal_names or list(DEFAULT_SIGNALS)
    labels = {name: 0.0 for name in signal_names}

    for match in _SIGNAL_TAG_RE.finditer(output_text):
        base_type = match.group(1)
        subtype = match.group(2) or ""

        # Build the tag forms to check against TAG_TO_PROBE
        full_tag = f"{base_type}:{subtype}" if subtype else base_type
        tag_no_pipe = full_tag.split("|")[0] if "|" in full_tag else full_tag

        probe_cat = TAG_TO_PROBE.get(tag_no_pipe) or TAG_TO_PROBE.get(base_type)
        if probe_cat and probe_cat in labels:
            labels[probe_cat] = 1.0

    return labels


def curate_training_dataset(
    training_pairs: list[dict],
    signal_names: list[str] | None = None,
    min_text_length: int = 10,
    min_samples_per_signal: int = 0,
    negative_ratio: float = 0.0,
) -> list[dict]:
    """Convert Alpaca-format training pairs into probe training samples.

    Each output sample has:
    - ``instruction``: the prompt text (used to generate hidden states)
    - ``output``: the response text (included in hidden state context)
    - ``labels``: binary dict of probe category activations
    - ``active_probes``: list of probe categories that are active

    Filters out pairs with very short text.  Pairs with no detectable
    signals are kept as negative examples for probe training.

    Parameters
    ----------
    training_pairs : list[dict]
        Alpaca-format pairs with ``instruction``, ``input``, ``output``.
    signal_names : list[str] | None
        Probe categories to label for.
    min_text_length : int
        Minimum combined text length to include a pair.
    min_samples_per_signal : int
        Warn if any signal category has fewer positive examples than this.
        Set to 0 to disable the check.
    negative_ratio : float
        Cap the number of negative examples (samples with no active probes)
        to ``negative_ratio * mean_positive_count``.  Prevents negatives
        from overwhelming positives.  Set to 0 to keep all negatives.

    Returns
    -------
    list[dict]
        Curated dataset ready for probe training.
    """
    import random as _rng

    signal_names = signal_names or list(DEFAULT_SIGNALS)
    positives: list[dict] = []
    negatives: list[dict] = []

    for pair in training_pairs:
        instruction = pair.get("instruction", "")
        inp = pair.get("input", "")
        output = pair.get("output", "")

        prompt = f"{instruction}\n{inp}".strip() if inp else instruction
        if len(prompt) + len(output) < min_text_length:
            continue

        labels = extract_labels_from_output(output, signal_names)
        active = [k for k, v in labels.items() if v > 0.5]

        sample = {
            "instruction": prompt,
            "output": output,
            "labels": labels,
            "active_probes": active,
            "weight": pair.get("weight", 1.0),
        }

        if active:
            positives.append(sample)
        else:
            negatives.append(sample)

    # --- Class balancing: cap negatives ---
    if negative_ratio > 0 and positives and negatives:
        counts = {name: 0 for name in signal_names}
        for s in positives:
            for name in s["active_probes"]:
                counts[name] += 1
        active_counts = [v for v in counts.values() if v > 0]
        mean_pos = sum(active_counts) / len(active_counts) if active_counts else 0
        max_negatives = int(negative_ratio * mean_pos)
        if len(negatives) > max_negatives:
            _rng.shuffle(negatives)
            logger.info(
                "Class balancing: capping negatives from %d to %d "
                "(negative_ratio=%.1f, mean_positive=%.1f)",
                len(negatives), max_negatives, negative_ratio, mean_pos,
            )
            negatives = negatives[:max_negatives]

    dataset = positives + negatives

    # --- Distribution logging + min_samples warning ---
    counts = {name: 0 for name in signal_names}
    for sample in dataset:
        for name in sample["active_probes"]:
            counts[name] += 1

    if min_samples_per_signal > 0:
        low_signals = {
            k: v for k, v in counts.items()
            if 0 < v < min_samples_per_signal
        }
        if low_signals:
            logger.warning(
                "Low sample count for signals (min_samples_per_signal=%d): %s",
                min_samples_per_signal, low_signals,
            )

    logger.info(
        "Curated %d probe training samples (%d positive, %d negative). "
        "Distribution: %s",
        len(dataset), len(positives), len(negatives),
        {k: v for k, v in sorted(counts.items(), key=lambda x: -x[1]) if v > 0},
    )

    return dataset
