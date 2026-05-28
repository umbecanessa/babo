"""NLS Data Models — Block, AKU, Fact, ChainState, and related structures.

These Pydantic models define every data structure that flows through the NLS pipeline:
from the Merkle chain blocks to the Atomic Knowledge Units extracted by the Bridge.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SovereigntyMode(str, Enum):
    """Privacy mode for the Cloud Bridge distillation pipeline."""

    LOCAL = "local"  # 100% local distillation via the 8B model
    MASKED = "masked"  # PII-scrubbed logs sent to cloud
    FULL = "full"  # Raw logs sent to cloud for maximum IQ


class BlockType(str, Enum):
    """Type of block in the Merkle-Delta chain."""

    GENESIS = "genesis"  # Agent birth — soul + base model fingerprint (height 0)
    DELTA = "delta"  # Incremental memory block from a consolidation session
    EPOCH = "epoch"  # Merged block from TIES-Merging of multiple deltas


# ---------------------------------------------------------------------------
# Atomic Knowledge Unit (AKU) — The Bridge's output
# ---------------------------------------------------------------------------


class SyntheticPair(BaseModel):
    """A single instruction/output pair for consolidation replay."""

    instruction: str
    output: str


class AKU(BaseModel):
    """Atomic Knowledge Unit — the smallest learnable fact extracted by the Bridge.

    Each AKU is tagged with a hierarchical domain path (dot notation) for
    conflict detection and may include synthetic pairs for sleep consolidation.
    """

    domain_path: str = Field(
        ...,
        description="Hierarchical dot-notation path, e.g. 'User.Tech.Framework.Frontend'",
    )
    fact: str = Field(..., description="The core piece of information.")
    logic_change: str = Field(
        ...,
        description="How the model should adjust reasoning, e.g. 'Prioritize brevity'.",
    )
    synthetic_pairs: list[SyntheticPair] = Field(
        default_factory=list,
        description="Instruction/output pairs for consolidation replay.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for this AKU (1.0 = certain).",
    )
    source_block: int | None = Field(
        default=None,
        description="Block height that produced this AKU, if from a previous session.",
    )


# ---------------------------------------------------------------------------
# Domain Ledger — Fact tracking in SQLite
# ---------------------------------------------------------------------------


class Fact(BaseModel):
    """A tracked fact in the Domain Ledger (knowledge.db).

    Facts are indexed by their hierarchical domain_path. The flip_count
    and is_fluid fields implement the Ping-Pong Protection / Fluidity Filter.

    The ``canonical_question`` field stores the question this fact answers
    (e.g. "What is the capital of France?" for value "Paris").  This enables:

    * **Cortical reorganization during sleep** — when two facts collide
      on a domain key, comparing their canonical questions determines
      whether it's a genuine contradiction (same question, different
      answer) or a mis-categorization (different questions filed under
      the same domain).
    * **DMN self-testing** — the Default Mode Network can pick a stored
      question and quiz the model, detecting memory drift.
    * **Curiosity-driven verification** — when self-test reveals
      uncertainty, the curiosity drive fires a web search to verify.
    """

    id: int | None = None
    domain_path: str = Field(
        ...,
        description="Hierarchical dot-notation path, e.g. 'User.UI.Theme.Color'",
    )
    current_value: str
    canonical_question: str | None = Field(
        default=None,
        description=(
            "The question this fact answers, e.g. 'What is your favorite book?' "
            "Used for contradiction detection during sleep (cortical reorganization) "
            "and DMN self-testing during daydreaming."
        ),
    )
    block_height: int = Field(
        default=0,
        description="Chain height when this fact was last updated.",
    )
    flip_count: int = Field(
        default=0,
        description="Number of times this fact has changed value.",
    )
    is_fluid: bool = Field(
        default=False,
        description="If True, this fact is unstable and barred from mining.",
    )
    meta_layer: str | None = Field(
        default=None,
        description=(
            "Metacognitive layer that processed this fact at collection time "
            "(e.g. 'pfc_judgment', 'acc_epistemic', 'amygdala_affective'). "
            "Used as a 'gut feeling' pre-filter during cortical reorganization: "
            "facts processed by different cognitive systems likely don't belong "
            "in the same domain."
        ),
    )
    hormonal_fingerprint: str | None = Field(
        default=None,
        description=(
            "JSON-encoded hormonal snapshot at collection time, e.g. "
            '\'{"cortisol": 0.1, "dopamine": 0.6, ...}\'. '
            "Used alongside meta_layer for the System 1 'gut feeling' "
            "pre-filter during sleep cortical reorganization."
        ),
    )
    strength: float = Field(
        default=1.0,
        description=(
            "Memory strength (synaptic weight). Increases on re-encounter "
            "(reinforcement), decays each sleep cycle (forgetting curve). "
            "Facts below a threshold are archived and pruned."
        ),
    )
    emotional_valence: float = Field(
        default=0.0,
        description=(
            "Computed emotional valence at storage time: (serotonin - cortisol) * 2.0 "
            "clamped to [-1, 1]. Enables mood-congruent recall where the agent's "
            "current mood biases which facts surface during data_lookup."
        ),
    )
    last_modified: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scope: str = Field(
        default="global",
        description=(
            "Fact scope layer: 'global' (User.*, Agent.*, System.*, Social.*), "
            "'project' (Project.*), or 'domain' (Domain.*, Base.*). "
            "Mirrors the Cryptex ring categories."
        ),
    )
    project_id: str = Field(
        default="",
        description="Cryptex project ID this fact belongs to (empty = global/domain).",
    )


# ---------------------------------------------------------------------------
# Merkle-Delta Chain — Block structure
# ---------------------------------------------------------------------------


class BlockMetadata(BaseModel):
    """Optional metadata attached to a mined block."""

    dominant_skill: str | None = None
    relationship_status: str | None = None
    pruning_threshold: float = 0.05
    # Front-brain context at training time (IR-10.1)
    avg_pe: float = 0.0
    narrative_coherence: float = 0.7
    energy_level: float = 1.0
    resonance_peak: float = 0.0
    extra: dict[str, Any] = Field(default_factory=dict)


class Block(BaseModel):
    """A single block in the Merkle-Delta chain.

    Each block wraps a memory delta artifact and is cryptographically linked
    to its parent via SHA-256 hashing.
    """

    height: int = Field(..., description="Position in the chain (0 = genesis).")
    block_hash: str = Field(..., description="SHA-256 hash of this block.")
    parent_hash: str = Field(
        ...,
        description="SHA-256 hash of the parent block (genesis uses a zero-hash).",
    )
    block_type: BlockType = BlockType.DELTA
    delta_path: str = Field(
        ...,
        description="Relative path to the .safetensors or .gguf adapter file.",
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    aku_count: int = Field(default=0, description="Number of AKUs baked into this block.")
    metadata: BlockMetadata = Field(default_factory=BlockMetadata)


# ---------------------------------------------------------------------------
# Chain State — The full agent identity snapshot
# ---------------------------------------------------------------------------


class ChainState(BaseModel):
    """Complete state of an agent's Merkle-Delta chain.

    This is the in-memory representation of ledger.yaml — it tracks the
    current chain height, all blocks, and the active weight root.
    """

    agent_id: str
    base_model: str = Field(
        ...,
        description="Path or name of the GGUF base model (the Genesis Block).",
    )
    sovereignty_mode: SovereigntyMode = SovereigntyMode.LOCAL
    current_height: int = Field(
        default=0,
        description="Current block height (0 means no deltas yet).",
    )
    genesis_hash: str = Field(
        default="",
        description="SHA-256 hash of the genesis state (base model fingerprint).",
    )
    soul_hash: str = Field(
        default="",
        description="SHA-256 hash of the values adapter (Genesis Soul fingerprint). "
        "Sealed after bootstrap Phase 0 and verified at every runtime startup.",
    )
    active_epoch: Block | None = Field(
        default=None,
        description="The latest merged epoch block, if any.",
    )
    active_deltas: list[Block] = Field(
        default_factory=list,
        description="Delta blocks since the last epoch merge.",
    )

    # --- Tiered Memory Consolidation ---
    # Mirrors the brain's multi-timescale memory system:
    #   Tier 2: frozen_epochs  — recent, vivid (cortical columns)
    #   Tier 3: consolidated   — faded long-term (deep cortical traces)
    # Tier 1 = active_deltas (working memory), Tier 4 = oldest
    # consolidated entry acts as deep_memory when compacted.
    frozen_epochs: list[Block] = Field(
        default_factory=list,
        description="Frozen epoch blocks (Tier 2). Oldest are merged into "
        "consolidated tiers when count exceeds max_frozen_epochs.",
    )
    consolidated: list[Block] = Field(
        default_factory=list,
        description="Consolidated adapters (Tier 3). Created by SLERP-merging "
        "the oldest frozen epochs. Oldest entry acts as deep_memory (Tier 4) "
        "when further compacted.",
    )

    # Stability / fluidity control
    flip_threshold: int = Field(
        default=2,
        description="Max flips before a fact is marked fluid.",
    )
    flip_window_days: int = Field(
        default=30,
        description="Rolling window (days) for counting flips.",
    )

    # Cloud Bridge config (only used in masked/full modes)
    bridge_provider: str | None = None
    bridge_model: str | None = None


# ---------------------------------------------------------------------------
# Conversation / Buffer models
# ---------------------------------------------------------------------------


class ConversationTurn(BaseModel):
    """A single turn in the active conversation buffer."""

    role: Literal["system", "user", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class BufferState(BaseModel):
    """State of the Active Buffer (Layer III)."""

    turns: list[ConversationTurn] = Field(default_factory=list)
    pending_akus: list[AKU] = Field(default_factory=list)
    total_tokens_estimate: int = 0


# ---------------------------------------------------------------------------
# Bridge response models
# ---------------------------------------------------------------------------


class BridgeResponse(BaseModel):
    """Dual-stream response from the Cloud Bridge.

    Contains both the user-facing response and the synaptic deltas
    (AKUs) for the mining pipeline.
    """

    user_response: str
    synaptic_deltas: list[AKU] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Inference Interceptor models
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Genesis Template — Pre-minted agent template for instant creation
# ---------------------------------------------------------------------------


class GenesisManifest(BaseModel):
    """Manifest for a pre-minted genesis template.

    Created by ``nls mint-genesis`` from a fully bootstrapped agent.
    Contains all shared adapter references and default state files
    needed to instantiate a new agent in ~2 seconds without per-agent
    bootstrap training.
    """

    version: str = Field(
        ...,
        description="Template version slug (e.g. '8b-v1', '32b-v1').",
    )
    base_model: str = Field(
        ...,
        description="HuggingFace model name the template was minted from.",
    )
    soul_hash: str = Field(
        ...,
        description="SHA-256 hash of the values adapter (sealed at bootstrap).",
    )
    genesis_hash: str = Field(
        ...,
        description="SHA-256 fingerprint of the base model.",
    )
    minted_at: datetime = Field(default_factory=datetime.utcnow)
    description: str = Field(
        default="",
        description="Human-readable description of this genesis version.",
    )
    adapters: list[str] = Field(
        default_factory=lambda: ["values", "behavior", "metacognition"],
        description="List of shared adapter names included in this template.",
    )
    config_files: list[str] = Field(
        default_factory=lambda: [
            "runtime.json", "hormones.json", "autonomic.json",
            "drives.json", "dmn.json", "signals.json",
        ],
        description="Brain config JSON files snapshotted into this template.",
    )
    profile: str = Field(
        default="",
        description="Hardware/scenario profile baked into configs (empty = base configs).",
    )
    education: dict | None = Field(
        default=None,
        description=(
            "Education metadata for pre-educated genesis templates. "
            "Contains school name, graduation date, facts learned count, etc. "
            "None for blank (uneducated) genesis templates."
        ),
    )

    model_config = {"extra": "ignore"}


class AgentStatus(str, Enum):
    """Lifecycle status of an agent on the multi-agent platform."""

    CREATING = "creating"
    ALIVE = "alive"
    CHATTING = "chatting"
    SLEEPING = "sleeping"
    OFFLINE = "offline"
    EVICTED = "evicted"


# ---------------------------------------------------------------------------
# Server Runtime Events — Multi-agent platform event types
# ---------------------------------------------------------------------------


class SleepRequest(BaseModel):
    """Emitted by ServerRuntime when the ANS determines sleep is needed.

    Instead of training inline (like BaboRuntime does), the server runtime
    emits this event.  The server's SleepScheduler queues it for Model B.

    After Model B finishes, the scheduler calls
    ``ServerRuntime.notify_sleep_complete()`` with the new adapter paths.
    """

    agent_id: str = Field(..., description="Agent requesting sleep.")
    reason: str = Field(default="", description="Why sleep was triggered (signal count, cortisol, etc.).")
    signal_count: int = Field(default=0, description="Number of learnable signals buffered.")
    hormones: dict[str, float] = Field(
        default_factory=dict,
        description="Hormonal snapshot at the time of the sleep request.",
    )
    source: str = Field(
        default="",
        description="Origin of the sleep request: 'conversation', 'manual', 'scheduler', etc.",
    )
    requested_at: datetime = Field(default_factory=datetime.utcnow)


class DreamFinding(BaseModel):
    """A high-value finding from an active dream (tool-using daydream).

    Active dreams forage the internet and filesystem for project-relevant
    information.  When the REFLECT phase scores a finding above the
    relevance threshold, it's packaged as a DreamFinding and queued for
    delivery to the user.

    The frontend renders these as proactive notifications: "While you
    were away, I researched X and found Y..."
    """

    agent_id: str = Field(..., description="Agent that produced the finding.")
    dream_type: str = Field(
        default="browse",
        description="Active dream type: browse, bash_explore, practice.",
    )
    wonder_prompt: str = Field(
        default="",
        description="The research intention that spawned this dream.",
    )
    findings_summary: str = Field(
        default="",
        description="Human-readable summary of what was found.",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="URLs or file paths that were consulted.",
    )
    relevance_score: float = Field(
        default=0.0,
        description="REFLECT-phase relevance score (0.0-1.0).",
    )
    domain: str = Field(
        default="Dream.Research",
        description="DomainDB domain for this finding.",
    )
    tool_calls_made: int = Field(
        default=0,
        description="Number of tool calls executed during the dream.",
    )
    elapsed_seconds: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    delivered: bool = Field(
        default=False,
        description="Whether this finding has been sent to the user.",
    )


class SleepComplete(BaseModel):
    """Passed to ServerRuntime.notify_sleep_complete() after Model B finishes.

    Contains paths to the new adapter(s) produced during consolidation so
    the runtime can update its internal state without reloading the model.
    """

    agent_id: str = Field(..., description="Agent whose sleep completed.")
    new_delta_path: str = Field(
        default="",
        description="Relative path to the new delta adapter produced by training.",
    )
    new_epoch_path: str = Field(
        default="",
        description="Relative path to a new epoch if TIES merge was triggered.",
    )
    training_time_seconds: float = Field(default=0.0)
    signals_processed: int = Field(default=0)
    completed_at: datetime = Field(default_factory=datetime.utcnow)


class ProcessResult(BaseModel):
    """Structured result from ServerRuntime.process_message().

    Contains everything the server needs to build an API response:
    the generated text, NLS metadata (signals, hormones, routing),
    and any sleep trigger event.
    """

    response: str = Field(default="", description="Generated response text.")
    meta_weight: float = Field(default=0.0, description="Thalamic meta_weight used for this turn.")
    signals: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Extracted NLS signals (type, domain, content).",
    )
    hormones: dict[str, float] = Field(
        default_factory=dict,
        description="Hormonal levels after this turn.",
    )
    sleep_request: SleepRequest | None = Field(
        default=None,
        description="Non-None if the ANS triggered sleep on this turn.",
    )
    agency_actions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Agency tool actions executed this turn (web_search, etc.).",
    )
    facts_in_memory: int = Field(default=0, description="Total facts in the agent's DomainDB.")
    latency_ms: float = Field(default=0.0, description="End-to-end processing latency.")
    turn_number: int = Field(default=0, description="Cumulative turn counter for this agent.")
    name_update: str | None = Field(
        default=None,
        description="Non-None if the agent accepted a name this turn. "
        "The value is the new name string.",
    )
    structured_tool_calls: list[dict[str, Any]] | None = Field(
        default=None,
        description="OpenAI-format structured tool calls from the model, "
        "when using vLLM's native function-calling support.",
    )


# ---------------------------------------------------------------------------
# Inference Interceptor models
# ---------------------------------------------------------------------------


class InterceptionEvent(BaseModel):
    """Record of a single inference interception where the SQLite Domain Ledger
    replaced low-confidence model output.

    Logged when the Inference Interceptor detects that the model's token-level
    confidence has dropped below threshold while generating a response in a
    known factual domain.  The SQLite ``current_value`` is injected instead.

    Over time, as the same fact is reinforced via cumulative training (LTP),
    the model's logprobs for that fact increase and interceptions become rarer
    — the fact "graduates" from referential to semantic memory.
    """

    domain_path: str = Field(
        ...,
        description="Domain path of the fact that was intercepted, e.g. 'User.Personal.ServerPassword'.",
    )
    injected_value: str = Field(
        ...,
        description="The value injected from the SQLite Domain Ledger.",
    )
    original_tokens: list[str] = Field(
        default_factory=list,
        description="The low-confidence tokens the model was generating before interception.",
    )
    avg_logprob: float = Field(
        ...,
        description="Average log-probability of the uncertain tokens that triggered interception.",
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
