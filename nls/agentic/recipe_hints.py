"""Match composition recipes to task text for preflight injection."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_RECIPES_ROOT = Path(__file__).resolve().parent.parent / "config" / "recipes"

_GITHUB_KEYWORDS = (
    "github",
    "gh repo",
    "git repo",
    "repository",
    "create repo",
    "push to github",
)


@lru_cache(maxsize=32)
def _load_recipe(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("recipe load failed %s: %s", path, exc)
        return None


def _format_recipe_hint(recipe: dict) -> str:
    name = recipe.get("name", "recipe")
    desc = recipe.get("description", "")
    lines = [f"- **{name}**: {desc}"]
    for step in recipe.get("steps", [])[:4]:
        tool = step.get("tool", "")
        example = step.get("example_params", {})
        cmd = example.get("command") or example.get("path", "")
        if cmd:
            preview = str(cmd)[:100]
            lines.append(f"  • {step.get('description', tool)}: {tool}({preview})")
        elif step.get("description"):
            lines.append(f"  • {step.get('description')}")
    recovery = recipe.get("steps", [{}])[0].get("recovery_hints") or []
    if recovery:
        lines.append(f"  Recovery: {'; '.join(recovery[:2])}")
    return "\n".join(lines)


def match_recipe_hints(text: str) -> str | None:
    """Return a preflight block when task text matches known recipes."""
    if not text or not _RECIPES_ROOT.is_dir():
        return None

    lower = text.lower()
    hints: list[str] = []

    if any(k in lower for k in _GITHUB_KEYWORDS):
        gh_path = str(_RECIPES_ROOT / "devops" / "github_repo.json")
        recipe = _load_recipe(gh_path)
        if recipe:
            hints.append(_format_recipe_hint(recipe))
            hints.append(
                "GitHub auth: if gh says 'auth login', use "
                "bash('echo TOKEN | gh auth login --with-token') with the "
                "user's token, or wm(action='borrow', domain='Project.Credential.GitHub'). "
                "If stuck, clawhub(action='search', query='github') or "
                "discover_tools(query='github')."
            )

    if not hints:
        return None

    parts = ["--- COMPOSITION RECIPES (matched to this task) ---"]
    parts.extend(hints)
    parts.append("--- END RECIPES ---")
    return "\n".join(parts)
