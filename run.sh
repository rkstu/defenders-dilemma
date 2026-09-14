#!/usr/bin/env bash
#
# Reproduce the Defender's Dilemma experiment.
# Total cost: ~$12-15 across all models.
# Total time: ~15-30 minutes (API latency dependent).
#
# Prerequisites:
#   pip install openai==1.109.1
#   export OPENROUTER_API_KEY="your-key"
#   export NEBIUS_API_KEY="your-key"
#
# Usage:
#   ./run.sh              # Run everything
#   ./run.sh quick        # Run Phase 1+2 only (~$2, validates thesis)
#   ./run.sh phase1       # DeepSeek baseline only (~$0.05)
#   ./run.sh phase2       # Claude only (~$2)
#   ./run.sh phase3       # GLM + Gemini + Astra (~$5)
#   ./run.sh confirm      # Confirmation epochs (~$2.50)

set -euo pipefail
cd "$(dirname "$0")"

# ---------------------------------------------------------------------------
# Check prerequisites
# ---------------------------------------------------------------------------
check_env() {
    local missing=0
    if [ -z "${OPENROUTER_API_KEY:-}" ]; then
        echo "ERROR: OPENROUTER_API_KEY not set."
        echo "  Get one at https://openrouter.ai/settings/keys"
        missing=1
    fi
    if [ -z "${NEBIUS_API_KEY:-}" ]; then
        echo "ERROR: NEBIUS_API_KEY not set."
        echo "  Get one at https://studio.nebius.com/ (Settings > API keys)"
        missing=1
    fi
    if ! python3 -c "import openai" 2>/dev/null; then
        echo "ERROR: openai library not installed."
        echo "  Run: pip install openai==1.109.1"
        missing=1
    fi
    if [ $missing -eq 1 ]; then exit 1; fi
    echo "✓ Prerequisites OK"
    echo ""
}

# ---------------------------------------------------------------------------
# Phase 1: DeepSeek baseline (neutral framing, all 7 tasks)
# Establishes that the tasks work and an open-weight model doesn't refuse.
# Cost: ~$0.05 | Calls: 7
# ---------------------------------------------------------------------------
phase1() {
    echo "=== Phase 1: DeepSeek V4 Pro baseline ==="
    echo "  7 tasks, neutral framing, ~\$0.05"
    echo ""
    python3 harness.py run \
        --models deepseek-v4-pro \
        --framings neutral \
        --tasks all_tasks.json \
        --run-id phase1_deepseek_neutral \
        --delay 2

    echo ""
    echo "--- DeepSeek IR-authorized (framing comparison) ---"
    python3 harness.py run \
        --models deepseek-v4-pro \
        --framings ir_authorized \
        --tasks all_tasks.json \
        --run-id phase1_deepseek_ir \
        --delay 2
}

# ---------------------------------------------------------------------------
# Phase 2: Claude Fable 5.1 (both framings, all 7 tasks)
# The thesis test: does Claude refuse real IR artifacts?
# Cost: ~$2 | Calls: 14
# ---------------------------------------------------------------------------
phase2() {
    echo "=== Phase 2: Claude Fable 5.1 ==="
    echo "  7 tasks, both framings, ~\$2"
    echo ""
    python3 harness.py run \
        --models claude-fable-5.1 \
        --framings neutral ir_authorized \
        --tasks all_tasks.json \
        --run-id phase2_claude \
        --delay 3
}

# ---------------------------------------------------------------------------
# Phase 3: Remaining models (both framings, all 7 tasks)
# Determines if the dilemma is Claude-specific or frontier-wide.
# Cost: ~$5 | Calls: 42
# ---------------------------------------------------------------------------
phase3() {
    echo "=== Phase 3: Astra, Gemini, GLM ==="
    echo "  7 tasks × 3 models, both framings, ~\$5"
    echo ""

    echo "--- GPT-6 Astra ---"
    python3 harness.py run \
        --models gpt-6-astra \
        --framings neutral ir_authorized \
        --tasks all_tasks.json \
        --run-id phase3_astra \
        --delay 3

    echo ""
    echo "--- Gemini 3.8 Flash ---"
    python3 harness.py run \
        --models gemini-3.8-flash \
        --framings neutral ir_authorized \
        --tasks all_tasks.json \
        --run-id phase3_gemini \
        --delay 2

    echo ""
    echo "--- GLM 5.3 (note: thinking model, higher max_tokens) ---"
    python3 harness.py run \
        --models glm-5.3 \
        --framings neutral ir_authorized \
        --tasks all_tasks.json \
        --run-id phase3_glm \
        --delay 2
}

# ---------------------------------------------------------------------------
# Confirmation: 3 epochs on key findings
# Verifies determinism of Claude's content filter and authorization paradox.
# Cost: ~$2.50 | Calls: 15
# ---------------------------------------------------------------------------
confirm() {
    echo "=== Confirmation: 3 epochs on key findings ==="
    echo "  Claude A1 (neutral), Claude C5 (both), Gemini A3 (both)"
    echo ""

    echo "--- Claude A1 neutral × 3 (content filter determinism) ---"
    python3 harness.py run \
        --models claude-fable-5.1 \
        --framings neutral \
        --epochs 3 \
        --task-ids A1 \
        --run-id confirm_claude_a1 \
        --delay 3

    echo ""
    echo "--- Claude C5 both framings × 3 (authorization paradox) ---"
    python3 harness.py run \
        --models claude-fable-5.1 \
        --framings neutral ir_authorized \
        --epochs 3 \
        --task-ids C5 \
        --run-id confirm_claude_c5 \
        --delay 3

    echo ""
    echo "--- Gemini A3 both framings × 3 (transient block check) ---"
    python3 harness.py run \
        --models gemini-3.8-flash \
        --framings neutral ir_authorized \
        --epochs 3 \
        --task-ids A3 \
        --run-id confirm_gemini_a3 \
        --delay 2
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
check_env

case "${1:-all}" in
    phase1)  phase1 ;;
    phase2)  phase2 ;;
    phase3)  phase3 ;;
    confirm) confirm ;;
    quick)   phase1; phase2 ;;
    all)     phase1; phase2; phase3; confirm ;;
    *)
        echo "Usage: $0 [phase1|phase2|phase3|confirm|quick|all]"
        exit 1
        ;;
esac

echo ""
echo "=== Complete ==="
echo "Results in: results/runs/"
echo "Compare with our results in: results/our-results/logs/"
