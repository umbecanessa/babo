"""Domain Taxonomy Seed — structured knowledge scaffolding for NLS agents.

Maps to the **human educational system** that provides every child with
a baseline mental map before personal experience shapes it further.

The taxonomy seed is a YAML hierarchy with keyword hints for each branch.
When a LEARN signal arrives with a model-generated domain path, the
taxonomy can suggest a better path based on keyword matching against the
fact content.  This prevents obvious misclassification (e.g. "shark" → Math)
while still allowing the agent to create novel domains for genuinely new
categories.

Public API
----------
``TaxonomySeed``
    Load from YAML, match facts to taxonomy branches, suggest corrections.

    - ``load()``           — parse YAML file into searchable tree
    - ``suggest_path()``   — given fact text + model path, suggest best match
    - ``matches()``        — check if a domain path exists in the seed
    - ``keyword_search()`` — find best taxonomy branch for a text snippet
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TaxonomyNode:
    """A single node in the taxonomy tree."""

    name: str
    keywords: list[str] = field(default_factory=list)
    children: dict[str, "TaxonomyNode"] = field(default_factory=dict)
    full_path: str = ""  # e.g. "Agent.Knowledge.Biology.Marine"

    def all_keywords_lower(self) -> set[str]:
        """Return all keywords (lowercased) for this node and ancestors."""
        return {k.lower() for k in self.keywords}


@dataclass
class TaxonomyMatch:
    """Result of a taxonomy keyword search."""

    path: str  # Full domain path, e.g. "Agent.Knowledge.Biology.Marine"
    score: float  # 0.0–1.0 confidence
    matched_keywords: list[str]  # Which keywords triggered the match
    depth: int  # How deep in the tree (deeper = more specific = better)


# ---------------------------------------------------------------------------
# Taxonomy Seed
# ---------------------------------------------------------------------------

class TaxonomySeed:
    """Load and query a domain taxonomy seed.

    The seed provides keyword-based domain classification guidance.
    It does NOT constrain — novel domains not in the seed pass through
    unchanged.  The seed only intervenes when it has a confident match
    that disagrees with the model's suggestion.
    """

    def __init__(self) -> None:
        self._root: dict[str, TaxonomyNode] = {}
        self._all_nodes: list[TaxonomyNode] = []  # Flat index for search
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    # ----- Loading -----

    def load(self, path: str | Path) -> None:
        """Load taxonomy from a YAML file."""
        import yaml

        path = Path(path)
        if not path.exists():
            logger.warning("Taxonomy seed not found at %s", path)
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            logger.warning("Taxonomy seed is not a valid YAML dict: %s", path)
            return

        self._root = {}
        self._all_nodes = []
        for top_name, top_data in raw.items():
            node = self._parse_node(top_name, top_data, parent_path="")
            self._root[top_name] = node

        self._loaded = True
        logger.info(
            "Taxonomy seed loaded: %d top-level branches, %d total nodes",
            len(self._root), len(self._all_nodes),
        )

    def enrich_from_fact_bank(self, facts_dir: str | Path) -> int:
        """Dynamically ingest domain paths from curriculum fact-bank files.

        The fact bank (``nls/curricula/facts/*.json``) defines domains
        like ``Agent.Knowledge.AgentSkills.Identity`` that may not exist
        in the static YAML seed.  This method walks every fact-bank file,
        extracts the ``domain`` fields, and ensures the taxonomy tree
        contains those paths — so the normaliser can correct
        ``AgentSkill`` → ``AgentSkills`` dynamically rather than
        requiring every domain to be manually hardcoded in the YAML.

        This mirrors how a school curriculum naturally *defines* the
        mental map the student should build.  The seed provides the
        broad scaffold; the curriculum fills in the specific branches.

        Returns the number of new nodes added.
        """
        import json as _json

        facts_dir = Path(facts_dir)
        if not facts_dir.is_dir():
            return 0

        added = 0
        for fact_file in sorted(facts_dir.glob("*.json")):
            try:
                with open(fact_file, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                tiers = data.get("tiers", {})
                if isinstance(tiers, dict):
                    tier_lists = tiers.values()
                elif isinstance(tiers, list):
                    tier_lists = [tiers]
                else:
                    continue
                for tier_facts in tier_lists:
                    if not isinstance(tier_facts, list):
                        continue
                    for fact in tier_facts:
                        domain = fact.get("domain", "") if isinstance(fact, dict) else ""
                        if domain:
                            added += self._ensure_path(domain)
            except Exception as exc:
                logger.debug("Fact-bank enrich skipped %s: %s", fact_file.name, exc)

        if added:
            logger.info(
                "Taxonomy enriched from fact bank: +%d nodes (%d total)",
                added, len(self._all_nodes),
            )
        return added

    def enrich_from_domain_db(self, domain_db: Any) -> int:
        """Ingest existing domain paths from the agent's DomainDB.

        Called at startup so that domains the agent discovered during
        its lifetime (through conversation, cortical reorg, etc.) are
        available for future normalization — even after a server restart.

        This is the "long-term memory informs the mental map" pathway:
        the agent's accumulated knowledge shapes its categorization
        system.

        Returns the number of new nodes added.
        """
        if domain_db is None:
            return 0

        added = 0
        try:
            all_facts = domain_db.get_facts_by_prefix("")
            for fact in all_facts:
                dp = getattr(fact, "domain_path", "") or ""
                if dp:
                    added += self._ensure_path(dp)
        except Exception as exc:
            logger.debug("DomainDB enrich failed: %s", exc)

        if added:
            logger.info(
                "Taxonomy enriched from DomainDB: +%d nodes (%d total)",
                added, len(self._all_nodes),
            )
        return added

    def _ensure_path(self, domain_path: str) -> int:
        """Ensure a domain path exists in the taxonomy tree.

        Walks each segment; if it already exists (exact or fuzzy), use
        the existing node.  If not, create a new node.  Returns the
        number of new nodes created.
        """
        segments = domain_path.split(".")
        current = self._root
        parent_path = ""
        created = 0

        for seg in segments:
            full = f"{parent_path}.{seg}" if parent_path else seg
            canon = self._fuzzy_match_segment(seg, current)
            if canon is not None:
                parent_path = current[canon].full_path
                current = current[canon].children
            else:
                node = TaxonomyNode(
                    name=seg,
                    keywords=[],
                    full_path=full,
                )
                current[seg] = node
                self._all_nodes.append(node)
                parent_path = full
                current = node.children
                created += 1

        return created

    def _parse_node(
        self,
        name: str,
        data: dict[str, Any] | None,
        parent_path: str,
    ) -> TaxonomyNode:
        """Recursively parse a taxonomy node from YAML data."""
        full_path = f"{parent_path}.{name}" if parent_path else name

        if data is None:
            data = {}

        keywords = data.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []

        node = TaxonomyNode(
            name=name,
            keywords=keywords,
            full_path=full_path,
        )
        self._all_nodes.append(node)

        # Parse children (anything that's not "keywords" is a child branch)
        children_data = data.get("children", {})
        if isinstance(children_data, dict):
            for child_name, child_data in children_data.items():
                child = self._parse_node(child_name, child_data, full_path)
                node.children[child_name] = child

        # Also check for direct dict entries that aren't "keywords" or "children"
        # (supports both explicit `children:` key and flat structure)
        for key, val in data.items():
            if key in ("keywords", "children"):
                continue
            if isinstance(val, dict):
                child = self._parse_node(key, val, full_path)
                node.children[key] = child

        return node

    # ----- Query API -----

    @staticmethod
    def _fuzzy_match_segment(
        segment: str,
        candidates: dict[str, "TaxonomyNode"],
    ) -> str | None:
        """Match a segment against taxonomy node names, handling singular/plural.

        The agent commonly singularises domain path segments (e.g.
        ``Physics`` → ``Physic``, ``Mechanics`` → ``Mechanic``).  This
        helper resolves such near-misses so that the taxonomy can still
        recognise and normalise the path.

        Returns the canonical (taxonomy) name on match, or ``None``.
        """
        # Exact match — fast path
        if segment in candidates:
            return segment
        # Try adding trailing 's' (Physic → Physics)
        if segment + "s" in candidates:
            return segment + "s"
        # Try removing trailing 's' (unlikely but safe)
        if segment.endswith("s") and segment[:-1] in candidates:
            return segment[:-1]
        # Try adding 'es' (e.g. Box → Boxes, unlikely but safe)
        if segment + "es" in candidates:
            return segment + "es"
        # Case-insensitive fallback
        seg_lower = segment.lower()
        for name in candidates:
            if name.lower() == seg_lower:
                return name
            # Case-insensitive + singular/plural
            if name.lower() == seg_lower + "s":
                return name
            if name.lower().rstrip("s") == seg_lower:
                return name
        return None

    def matches(self, domain_path: str) -> bool:
        """Check if a domain path exists (as prefix or exact) in the seed.

        Uses fuzzy segment matching to handle singular/plural variations
        that the agent commonly produces.
        """
        if not self._loaded:
            return False
        segments = domain_path.split(".")
        current = self._root
        for seg in segments:
            canon = self._fuzzy_match_segment(seg, current)
            if canon is not None:
                node = current[canon]
                current = node.children
            else:
                return False
        return True

    def normalize_path(self, domain_path: str) -> str:
        """Normalise a domain path to canonical taxonomy names.

        Walks the path through the taxonomy tree using fuzzy segment
        matching.  Segments that match a taxonomy node are replaced with
        the canonical name; segments beyond the taxonomy depth are kept
        as-is.

        Examples::

            "Agent.Knowledge.Physic.Mechanic"
            → "Agent.Knowledge.Physics.Mechanics"

            "Agent.Knowledge.Art.VisualArt"
            → "Agent.Knowledge.Arts.VisualArts"

        If the taxonomy is not loaded, returns the path unchanged.
        """
        if not self._loaded:
            return domain_path
        segments = domain_path.split(".")
        normalised: list[str] = []
        current = self._root
        for seg in segments:
            canon = self._fuzzy_match_segment(seg, current)
            if canon is not None:
                normalised.append(canon)
                node = current[canon]
                current = node.children
            else:
                # Beyond taxonomy depth — keep original segment
                normalised.append(seg)
                current = {}  # no more children to walk
        return ".".join(normalised)

    def keyword_search(self, text: str) -> TaxonomyMatch | None:
        """Find the best taxonomy branch for a text snippet.

        Scores each node by how many of its keywords appear in the text.
        Prefers deeper (more specific) matches.  Returns None if no
        keywords match.
        """
        if not self._loaded or not text:
            return None

        text_lower = text.lower()
        # Tokenize text into words for whole-word matching
        text_words = set(re.findall(r'[a-z0-9]+', text_lower))

        best: TaxonomyMatch | None = None

        for node in self._all_nodes:
            if not node.keywords:
                continue

            matched = []
            for kw in node.keywords:
                kw_lower = kw.lower()
                # Multi-word keywords: check as substring
                if " " in kw_lower:
                    if kw_lower in text_lower:
                        matched.append(kw)
                else:
                    # Single-word: check as whole word
                    if kw_lower in text_words:
                        matched.append(kw)

            if not matched:
                continue

            # Score: number of matched keywords, weighted by depth
            depth = node.full_path.count(".")
            score = len(matched) * (1.0 + depth * 0.3)

            if best is None or score > best.score:
                best = TaxonomyMatch(
                    path=node.full_path,
                    score=score,
                    matched_keywords=matched,
                    depth=depth,
                )

        return best

    def suggest_path(
        self,
        fact_text: str,
        model_path: str,
        confidence_threshold: float = 1.0,
    ) -> tuple[str, str]:
        """Suggest a domain path for a fact, possibly correcting the model.

        Returns (suggested_path, reason) where reason is one of:
        - "model"     — model's path used as-is (taxonomy agrees or no match)
        - "taxonomy"  — taxonomy suggested a different, better path
        - "hybrid"    — model's path kept but taxonomy appended specificity
        - "normalised" — model's path was a singular/plural variant of a
          taxonomy path and has been normalised to canonical form
        """
        if not self._loaded:
            return model_path, "model"

        # If model's path already matches the taxonomy exactly, trust it
        if self.matches(model_path):
            # Even if it matches fuzzily, normalise to canonical form
            # so that "Physic.Mechanic" → "Physics.Mechanics"
            normalised = self.normalize_path(model_path)
            if normalised != model_path:
                logger.info(
                    "Taxonomy normalised: '%s' -> '%s'",
                    model_path, normalised,
                )
                return normalised, "normalised"
            return model_path, "model"

        # Search taxonomy by keywords in the fact text
        match = self.keyword_search(fact_text)

        if match is None:
            # No taxonomy match — trust the model (novel domain)
            return model_path, "model"

        if match.score < confidence_threshold:
            # Low confidence — trust the model
            return model_path, "model"

        # Check if model's top-level branch agrees with taxonomy
        model_top = model_path.split(".")[0] if "." in model_path else model_path
        taxonomy_top = match.path.split(".")[0] if "." in match.path else match.path

        if model_top == taxonomy_top:
            # Same top-level branch — model is in the right area.
            # If taxonomy match is deeper/more specific, use it as base
            # but append any model-specific segments.
            model_segments = model_path.split(".")
            tax_segments = match.path.split(".")

            # If taxonomy is more specific, build hybrid path
            if len(tax_segments) > len(model_segments):
                # Taxonomy is more detailed — try to append model's
                # unique leaf segments
                unique_model = [
                    s for s in model_segments[1:]
                    if s not in tax_segments
                ]
                if unique_model:
                    hybrid = match.path + "." + ".".join(unique_model[-2:])
                    return hybrid, "hybrid"
                return match.path, "taxonomy"
            return model_path, "model"

        # NEVER override the top-level namespace when both are protected
        # entity namespaces (User vs Agent).  These represent fundamentally
        # different entities.  The model knows whether a fact is about the
        # user or about itself; the taxonomy should not invert that.
        _PROTECTED_NAMESPACES = {"User", "Agent"}
        if model_top in _PROTECTED_NAMESPACES and taxonomy_top in _PROTECTED_NAMESPACES:
            if model_top != taxonomy_top:
                logger.debug(
                    "Taxonomy: preserving namespace '%s' (would have been '%s')",
                    model_path, match.path,
                )
                return model_path, "model"

        # Different top-level branch — this is likely a misclassification.
        # e.g., model said "Agent.Math.Shark" but taxonomy says Biology.Marine.
        # Use taxonomy path and append model's leaf (the specific fact label).
        model_leaf = model_path.split(".")[-1] if "." in model_path else ""
        if model_leaf and model_leaf not in match.path:
            suggested = f"{match.path}.{model_leaf}"
        else:
            suggested = match.path

        logger.info(
            "Taxonomy correction: '%s' -> '%s' (matched: %s, score=%.1f)",
            model_path, suggested, match.matched_keywords, match.score,
        )
        return suggested, "taxonomy"

    # ----- Utility -----

    def get_all_branches(self, min_depth: int = 2) -> list[str]:
        """Return all taxonomy paths at or below a minimum depth."""
        return [
            n.full_path for n in self._all_nodes
            if n.full_path.count(".") >= min_depth - 1
        ]

    def get_branch_keywords(self, path: str) -> list[str]:
        """Return keywords for a specific taxonomy branch."""
        for node in self._all_nodes:
            if node.full_path == path:
                return node.keywords
        return []

    def stats(self) -> dict[str, Any]:
        """Return taxonomy statistics."""
        return {
            "loaded": self._loaded,
            "top_level_branches": len(self._root),
            "total_nodes": len(self._all_nodes),
            "nodes_with_keywords": sum(
                1 for n in self._all_nodes if n.keywords
            ),
            "total_keywords": sum(
                len(n.keywords) for n in self._all_nodes
            ),
        }
