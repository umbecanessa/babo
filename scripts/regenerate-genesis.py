#!/usr/bin/env python3
"""Regenerate genesis_templates/standard-v1 from nls/config (desktop build step)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.services.genesis_seed import (  # noqa: E402
    STANDARD_V1_CONFIG_FILES,
    regenerate_standard_v1_template,
)


def main() -> int:
    target = regenerate_standard_v1_template()
    runtime_cfg = target / "config" / "runtime.json"
    print(f"Regenerated {target}")
    print(f"  config files: {len(STANDARD_V1_CONFIG_FILES)} tracked")
    print(f"  runtime.json: {runtime_cfg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
