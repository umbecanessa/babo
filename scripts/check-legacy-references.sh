#!/usr/bin/env bash
# Fail CI if lab-only symbols reappear outside documentation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v rg >/dev/null 2>&1; then
  echo "ripgrep (rg) required for legacy reference check"
  exit 1
fi

PATTERN='sleep_trainer|nls_vllm_plugin|QLoRA|SlotRegistry|mint_moe_genesis|ToolOnboarder|nls\._legacy|from nls\.engine\.tool_onboarding|moe_enabled|MoERuntime|gate_moe|MoEGateResult|moe_signals|moe_slots|gx10AgentId|Gx10Service|sendToGx10|X-GX10-Secret'
EXCLUDES=(
  --glob '!docs/**'
  --glob '!site/**'
  --glob '!scripts/check-legacy-references.sh'
)

if rg -i "$PATTERN" "${EXCLUDES[@]}" .; then
  echo "Legacy lab references found (see above). Remove or document as historical."
  exit 1
fi

echo "No forbidden legacy references in product tree."
