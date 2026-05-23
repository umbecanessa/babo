"""NLS Identity — Self-Models & Personality.

Sub-modules:
    agent_identity  – Name detection from signals
    narrative_self  – vmPFC autobiography (NarrativeSelf)
    soul            – Core values / axioms (Soul)
    temporal_self   – Temporal layer (TemporalSelf)
    theory_of_mind  – User modeling (TheoryOfMind)
"""

from .agent_identity import detect_name_from_signals
from .narrative_self import NarrativeSelf
from .soul import SOUL_AXIOMS, SOUL_SELF_KNOWLEDGE
from .temporal_self import TemporalSelf
from .theory_of_mind import TheoryOfMind

__all__ = [
    "detect_name_from_signals",
    "NarrativeSelf",
    "SOUL_AXIOMS", "SOUL_SELF_KNOWLEDGE",
    "TemporalSelf",
    "TheoryOfMind",
]
