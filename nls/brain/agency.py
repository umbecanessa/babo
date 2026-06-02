"""NLS Agency Engine -- Runtime-driven proactive agency for Babo.

The runtime (not the model) decides when to act based on ANS signals
and hormonal state. This is more robust than relying on an 8B model's
function-calling ability.

Decision rules (all config-driven):
  - UNKNOWN signal + high norepinephrine -> web search
  - Repeated EVALUATE:incorrect + high cortisol -> web search failing domain
  - Idle timeout + pending research topics -> proactive research
  - Post-sleep -> announce what was learned
  - User asks about agent state -> expose internal state directly
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agency Actions
# ---------------------------------------------------------------------------


class AgencyAction:
    """A single action the agency engine wants the runtime to execute."""

    def __init__(
        self,
        action_type: str,
        *,
        query: str = "",
        message: str = "",
        domain: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        self.type = action_type
        self.query = query
        self.message = message
        self.domain = domain
        self.metadata = metadata or {}
        self.result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "type": self.type,
            "query": self.query,
            "message": self.message,
            "domain": self.domain,
        }
        if self.result:
            d["result"] = self.result
        if self.metadata:
            d["metadata"] = self.metadata
        return d


# ---------------------------------------------------------------------------
# Agency Engine
# ---------------------------------------------------------------------------


class AgencyEngine:
    """Runtime-driven agency that analyzes signals and hormones to decide actions.

    The engine runs after every model response and between turns,
    observing the agent's internal state and deciding whether to:
    - Search the web for unknown domains
    - Research failing topics
    - Announce post-sleep knowledge
    - Suggest proactive research topics
    """

    def __init__(
        self,
        config: dict[str, Any],
        tools: Any | None = None,
    ):
        self.config = config
        self.tools = tools

        # Thresholds from config
        self.unknown_search_threshold = config.get("unknown_search_threshold", 0.6)
        self.error_study_threshold = config.get("error_study_threshold", 0.7)
        self.idle_initiative_seconds = config.get("idle_initiative_seconds", 300)
        self.max_tool_calls = config.get("max_tool_calls_per_turn", 3)
        self.proactive_enabled = config.get("proactive_enabled", True)

        # Internal tracking
        self._pending_research: list[str] = []
        self._recent_unknowns: list[dict[str, str]] = []
        self._tool_calls_this_turn = 0
        self._post_sleep_announcement: str | None = None
        self._event_logger = None  # Set by runtime for research logging

    # =======================================================================
    # Per-message analysis
    # =======================================================================

    def analyze(
        self,
        response: str,
        signals: list,
        hypothalamus: Any | None = None,
        ans: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Analyze the current response and signals, decide on actions.

        Called after every model response. Returns a list of action dicts
        that the runtime should execute.

        The engine supports all tools in the registry, dispatching based
        on signal type, tool category, and hormonal state. Tool selection
        uses the tool's manifest metadata (category, hormone_affinity)
        rather than hardcoded names.
        """
        self._tool_calls_this_turn = 0
        actions: list[dict[str, Any]] = []

        if not self.proactive_enabled:
            return actions

        # Get hormonal levels
        norepi = 0.0
        cortisol = 0.0
        if hypothalamus is not None:
            try:
                norepi = hypothalamus.hormones.get("norepinephrine", None)
                if norepi is not None:
                    norepi = norepi.level
                else:
                    norepi = 0.0

                cortisol_h = hypothalamus.hormones.get("cortisol", None)
                if cortisol_h is not None:
                    cortisol = cortisol_h.level
                else:
                    cortisol = 0.0
            except (AttributeError, TypeError):
                pass

        # Determine which tool to use for knowledge queries
        # Prefer web_search if available, fall back to any "sense" category tool
        search_tool_name = self._find_search_tool()

        # Rule 1: UNKNOWN signals + high norepinephrine -> search/sense tool
        for sig in signals:
            if self._tool_calls_this_turn >= self.max_tool_calls:
                break

            sig_type = getattr(sig, "signal_type", "")
            domain = getattr(sig, "domain_path", "") or ""
            content = getattr(sig, "content", "") or ""

            if sig_type == "UNKNOWN" and norepi > self.unknown_search_threshold:
                if search_tool_name:
                    search_query = domain or content or "unknown topic"
                    actions.append({
                        "type": search_tool_name,
                        "query": search_query,
                        "reason": f"UNKNOWN signal + norepinephrine={norepi:.2f}",
                        "domain": domain,
                    })
                    self._tool_calls_this_turn += 1
                    self._recent_unknowns.append({
                        "domain": domain,
                        "content": content,
                        "time": datetime.utcnow().isoformat(),
                    })

        # Rule 2: EVALUATE:incorrect + high cortisol -> research the domain
        error_signals = [
            s for s in signals
            if getattr(s, "signal_type", "") == "EVALUATE"
            and "incorrect" in (getattr(s, "content", "") or "").lower()
        ]
        if error_signals and cortisol > self.error_study_threshold:
            if self._tool_calls_this_turn < self.max_tool_calls and search_tool_name:
                domain = getattr(error_signals[0], "domain_path", "") or "general"
                actions.append({
                    "type": search_tool_name,
                    "query": f"{domain} correct information",
                    "reason": f"Repeated errors + cortisol={cortisol:.2f}",
                    "domain": domain,
                })
                self._tool_calls_this_turn += 1

        # Track unknown domains for future research
        for sig in signals:
            sig_type = getattr(sig, "signal_type", "")
            domain = getattr(sig, "domain_path", "") or ""
            if sig_type == "UNKNOWN" and domain:
                if domain not in self._pending_research:
                    self._pending_research.append(domain)

        return actions

    def _find_search_tool(self) -> str | None:
        """Find the best search/sense tool in the registry.

        Prefers 'web_search' if available, then falls back to any tool
        in the 'sense' category that can handle queries.
        """
        if self.tools is None:
            return None

        available = getattr(self.tools, "available", [])
        if not available:
            return None

        # Prefer web_search by name
        if "web_search" in available:
            return "web_search"

        # Fall back to any sense-category tool
        try:
            from nls.engine.tools import ToolCategory
            sense_tools = self.tools.get_tools_by_category(ToolCategory.SENSE)
            if sense_tools:
                return sense_tools[0]
        except (ImportError, AttributeError):
            pass

        return None

    # =======================================================================
    # Proactive initiative (between turns)
    # =======================================================================

    def check_initiative(
        self,
        hypothalamus: Any | None = None,
        ans: Any | None = None,
        idle_seconds: float = 0.0,
    ) -> dict[str, Any] | None:
        """Check if the agent should take proactive action between turns.

        Called by the runtime during idle periods.
        """
        if not self.proactive_enabled:
            return None

        # Post-sleep announcement
        if self._post_sleep_announcement:
            msg = self._post_sleep_announcement
            self._post_sleep_announcement = None
            return {
                "type": "announcement",
                "message": msg,
            }

        # Idle research: if idle long enough and we have pending topics
        if (
            idle_seconds > self.idle_initiative_seconds
            and self._pending_research
        ):
            topic = self._pending_research.pop(0)
            # Humanize internal domain paths for user-facing messages
            human_topic = " ".join(
                w.lower() for w in topic.replace(".", " ").split()
                if w.lower() not in ("the", "a", "an", "system")
            ) if "." in topic else topic
            return {
                "type": "suggestion",
                "message": (
                    f"I noticed I don't know much about {human_topic}. "
                    f"Would you like me to research it?"
                ),
                "domain": topic,
            }

        return None

    # =======================================================================
    # Post-sleep hooks
    # =======================================================================

    def on_wake(self, sleep_report: Any | None = None):
        """Called by the runtime after a sleep cycle completes.

        Prepares a post-sleep announcement describing what was learned.
        """
        if sleep_report is None:
            self._post_sleep_announcement = (
                "I just woke up from a sleep cycle. "
                "My knowledge has been updated."
            )
            return

        duration = getattr(sleep_report, "duration_seconds", 0.0)
        n_signals = getattr(sleep_report, "total_signals_processed", 0)

        self._post_sleep_announcement = (
            f"I just completed a sleep cycle ({duration:.0f}s). "
            f"Processed {n_signals} signals. "
            f"My knowledge and routing have been updated."
        )

        logger.info("Agency: post-sleep announcement prepared")

    # =======================================================================
    # Internal state queries (built into runtime, not tools)
    # =======================================================================

    @staticmethod
    def get_internal_state(
        hypothalamus: Any | None = None,
        ans: Any | None = None,
        calibrator: Any | None = None,
    ) -> str:
        """Generate a human-readable report of the agent's internal state.

        This is called directly by the runtime when the user asks about
        the agent's state, moods, or knowledge -- not via a tool.
        """
        parts = ["=== Internal State Report ===\n"]

        # Hormonal state
        if hypothalamus is not None:
            parts.append("Hormonal State:")
            for name, h in hypothalamus.hormones.items():
                defn = hypothalamus.config.hormones[name]
                delta = h.level - defn.baseline
                arrow = "+" if delta > 0 else ""
                parts.append(f"  {name}: {h.level:.3f} ({arrow}{delta:.3f})")
            parts.append("")

        # ANS state
        if ans is not None:
            summary = ans.get_buffer_summary()
            parts.append("Autonomic State:")
            parts.append(f"  State: {summary['state']}")
            parts.append(f"  Turns: {summary['turn_count']}")
            parts.append(f"  Signals: {summary['total_signals']}")
            parts.append(f"  Learnable: {summary['learnable_signals']}")
            parts.append(f"  Error rate: {summary['error_rate']}")
            parts.append("")

        if calibrator is not None and hasattr(calibrator, "domain_tracker"):
            dt = calibrator.domain_tracker
            parts.append("Domain experience:")
            parts.append(f"  Domains tracked: {len(dt.domains)}")
            parts.append(f"  Skills tracked: {len(dt.skill_encounters)}")
            parts.append("")

        return "\n".join(parts)

    # =======================================================================
    # Drive-to-action execution
    # =======================================================================

    def execute_drive_goal(
        self,
        goal: Any,
        tools: Any | None = None,
        hypothalamus: Any | None = None,
    ) -> dict[str, Any]:
        """Convert a DriveGoal into a concrete action and execute it.

        Called by the runtime when the Drive Engine releases a goal
        through the effort gate. Maps action_type to tool calls or
        internal operations.

        The web_search action now uses the Playwright-backed browser
        for real page rendering and deep browsing.

        Returns:
            dict with keys: action_type, success, result, domain, details
        """
        action_type = getattr(goal, "action_type", "reflect")
        domain = getattr(goal, "domain", "")
        query = getattr(goal, "query", "")
        message = getattr(goal, "message", "")

        result = {
            "action_type": action_type,
            "drive": getattr(goal, "drive_name", ""),
            "domain": domain,
            "success": False,
            "result": "",
            "details": {},
        }

        if action_type == "web_search":
            # Curiosity drive: search the web and read page content.
            # Strategy: try Wikipedia first (cleaner, more factual),
            # fall back to general Bing search if Wikipedia fails.
            if tools is not None and query:
                wiki_tried = False
                wiki_success = False

                # Step 1: Try Wikipedia API (fast, structured, no ads)
                try:
                    wiki_content = self._try_wikipedia(query)
                    wiki_tried = True
                    if wiki_content:
                        result["result"] = wiki_content
                        result["success"] = True
                        result["details"] = {
                            "query": query,
                            "source": "wikipedia",
                        }
                        wiki_success = True
                except Exception as exc:
                    logger.debug("Wikipedia lookup failed for '%s': %s", query, exc)

                # Step 2: Fall back to Bing if Wikipedia didn't help
                if not wiki_success:
                    search_result = tools.execute(
                        "web_search",
                        {"query": query, "depth": 1},
                    )
                    result["result"] = search_result
                    result["success"] = not search_result.startswith("[ERROR]")
                    result["details"] = {
                        "query": query,
                        "source": "bing",
                        "wiki_attempted": wiki_tried,
                    }
            else:
                result["result"] = "No search tool available or empty query."

        elif action_type == "read_page":
            # Direct page read -- navigate to a specific URL and extract content
            url = getattr(goal, "url", "") or query
            if tools is not None and url:
                page_result = tools.execute(
                    "web_search",
                    {"url": url},
                )
                result["result"] = page_result
                result["success"] = not page_result.startswith("[ERROR]")
                result["details"] = {"url": url}
            else:
                result["result"] = "No browser tool available or empty URL."

        elif action_type == "deep_browse":
            # Deep browsing -- search and read multiple pages + follow links
            if tools is not None and query:
                browse_result = tools.execute(
                    "web_search",
                    {"query": query, "depth": 2},
                )
                result["result"] = browse_result
                result["success"] = not browse_result.startswith("[ERROR]")
                result["details"] = {"query": query, "depth": 2}
            else:
                result["result"] = "No browser tool available or empty query."

        elif action_type == "self_test":
            # Competence drive: the agent tests itself
            # This produces a self-query that gets processed through the
            # cognitive pipeline on the next tick
            result["result"] = query
            result["success"] = True
            result["details"] = {"self_test_prompt": query}

        elif action_type == "self_check":
            # Homeostasis drive: internal consistency check
            state_report = self.get_internal_state(
                hypothalamus=hypothalamus,
            )
            result["result"] = state_report
            result["success"] = True
            result["details"] = {"report_length": len(state_report)}

        elif action_type == "reach_out":
            # Social drive: compose a message to the user
            result["result"] = message or f"I've been thinking about {domain}."
            result["success"] = True
            result["details"] = {"message": result["result"]}

        elif action_type == "reflect":
            # Self-direction drive: introspective reflection
            result["result"] = query or f"Reflecting on {domain}..."
            result["success"] = True
            result["details"] = {"reflection_prompt": query}

        elif action_type == "disconfirm":
            # Epistemic integrity: search for counter-evidence to challenge
            # high-confidence schemas.  Reuses the web_search path with a
            # disconfirmation-oriented query.
            search_query = query or f"evidence against {domain}"
            if tools is not None and search_query:
                wiki_content = None
                try:
                    wiki_content = self._try_wikipedia(search_query)
                except Exception:
                    pass

                if wiki_content:
                    result["result"] = wiki_content
                    result["success"] = True
                    result["details"] = {
                        "query": search_query,
                        "source": "wikipedia",
                        "mode": "disconfirm",
                    }
                else:
                    search_result = tools.execute(
                        "web_search",
                        {"query": search_query, "depth": 1},
                    )
                    result["result"] = search_result
                    result["success"] = not search_result.startswith("[ERROR]")
                    result["details"] = {
                        "query": search_query,
                        "source": "bing",
                        "mode": "disconfirm",
                    }
            else:
                result["result"] = "No search tool available or empty query."

        else:
            result["result"] = f"Unknown action type: {action_type}"

        logger.info(
            "Agency: executed drive goal: type=%s domain=%s success=%s",
            action_type, domain, result["success"],
        )

        # Research logging
        if self._event_logger is not None:
            self._event_logger.log_agency_action(
                action_type=action_type,
                query=query,
                domain=domain,
                success=result["success"],
                result_preview=str(result.get("result", ""))[:500],
            )

        return result

    # ── Wikipedia helper ─────────────────────────────────────────────

    _WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
    _WIKI_SEARCH_API = "https://en.wikipedia.org/w/api.php"

    def _try_wikipedia(self, query: str) -> str | None:
        """Query Wikipedia REST API for a concise summary.

        Tries exact title match first, then falls back to search.
        Returns the extract text (typically 1-3 paragraphs) or None
        if nothing useful was found.

        Preferred over general web search for factual/encyclopedic
        domains because Wikipedia provides clean, structured text
        without ads, JavaScript, or bot detection issues.
        """
        import json as _json
        import urllib.request
        import urllib.parse

        # 1. Try direct page summary (fast, works for well-known topics)
        slug = urllib.parse.quote(query.replace(" ", "_"), safe="/_")
        try:
            req = urllib.request.Request(
                f"{self._WIKI_API}{slug}",
                headers={"User-Agent": "NLS-Brain/1.0 (research project)"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            extract = data.get("extract", "").strip()
            if extract and len(extract) > 100:
                title = data.get("title", query)
                return f"--- Wikipedia: {title} ---\n\n{extract}"
        except Exception:
            pass  # 404 or network error — try search

        # 2. Fall back to Wikipedia search API
        try:
            params = urllib.parse.urlencode({
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": "3",
                "format": "json",
                "utf8": "1",
            })
            req = urllib.request.Request(
                f"{self._WIKI_SEARCH_API}?{params}",
                headers={"User-Agent": "NLS-Brain/1.0 (research project)"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            hits = data.get("query", {}).get("search", [])
            if not hits:
                return None

            # Try the top search result's page summary
            top_title = hits[0].get("title", "")
            if not top_title:
                return None
            slug2 = urllib.parse.quote(top_title.replace(" ", "_"), safe="/_")
            req2 = urllib.request.Request(
                f"{self._WIKI_API}{slug2}",
                headers={"User-Agent": "NLS-Brain/1.0 (research project)"},
            )
            with urllib.request.urlopen(req2, timeout=8) as resp2:
                data2 = _json.loads(resp2.read().decode("utf-8"))
            extract2 = data2.get("extract", "").strip()
            if extract2 and len(extract2) > 100:
                title2 = data2.get("title", top_title)
                return f"--- Wikipedia: {title2} ---\n\n{extract2}"
        except Exception:
            pass

        return None

    @property
    def pending_research_topics(self) -> list[str]:
        """List of domains the agent wants to research."""
        return list(self._pending_research)

    def clear_pending_research(self):
        """Clear the pending research queue."""
        self._pending_research.clear()
