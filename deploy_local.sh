#!/usr/bin/env bash
# =============================================================================
# Ouroboros — Local Deployment Script (DeepSeek API backend)
# =============================================================================
# Deploys the Ouroboros self-modifying agent locally, using DeepSeek API
# instead of OpenRouter. DeepSeek API is OpenAI-compatible, so no code
# changes are needed — only configuration.
#
# Usage:
#   chmod +x deploy_local.sh
#   ./deploy_local.sh              # interactive setup + launch
#   ./deploy_local.sh --setup      # only create .env and directories
#   ./deploy_local.sh --start      # skip setup, launch with existing .env
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colors & helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
fatal() { err "$@"; exit 1; }

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${SCRIPT_DIR}"
ENV_FILE="${REPO_DIR}/.env"
DATA_DIR="${REPO_DIR}/local_data"
VENV_DIR="${REPO_DIR}/.venv"
LOCAL_LAUNCHER="${REPO_DIR}/local_launcher.py"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
MODE="full"  # full | setup | start
while [[ $# -gt 0 ]]; do
    case "$1" in
        --setup) MODE="setup"; shift ;;
        --start) MODE="start"; shift ;;
        -h|--help)
            echo "Usage: $0 [--setup | --start | --help]"
            echo "  (no args)  Interactive setup + launch"
            echo "  --setup    Create .env and directories only"
            echo "  --start    Launch with existing .env (skip setup)"
            exit 0
            ;;
        *) fatal "Unknown argument: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# 1) Check Python
# ---------------------------------------------------------------------------
check_python() {
    info "Checking Python version..."
    local py=""
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" &>/dev/null; then
            py="$candidate"
            break
        fi
    done
    [[ -z "$py" ]] && fatal "Python 3.10+ not found. Install Python first."

    local version
    version=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [[ "$major" -lt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -lt 10 ]]; }; then
        fatal "Python 3.10+ required, found $version"
    fi

    PYTHON="$py"
    ok "Python $version ($PYTHON)"
}

# ---------------------------------------------------------------------------
# 2) Create virtual environment
# ---------------------------------------------------------------------------
setup_venv() {
    if [[ -d "$VENV_DIR" ]] && [[ -f "$VENV_DIR/bin/activate" ]]; then
        info "Virtual environment already exists at ${VENV_DIR}"
    else
        info "Creating virtual environment..."
        "$PYTHON" -m venv "$VENV_DIR"
        ok "Virtual environment created"
    fi

    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    ok "Activated venv: $(which python)"
}

# ---------------------------------------------------------------------------
# 3) Install dependencies
# ---------------------------------------------------------------------------
install_deps() {
    info "Installing Python dependencies..."
    pip install --upgrade pip -q
    pip install -q openai requests

    # Playwright is optional (browser automation) — install but don't fail
    if pip install -q playwright playwright-stealth 2>/dev/null; then
        python -m playwright install chromium 2>/dev/null || warn "Playwright chromium install failed (browser tools will be unavailable)"
    else
        warn "Playwright install failed (browser tools will be unavailable)"
    fi

    ok "Dependencies installed"
}

# ---------------------------------------------------------------------------
# 4) Create local data directories (replaces Google Drive)
# ---------------------------------------------------------------------------
create_data_dirs() {
    info "Creating local data directories..."
    for sub in state logs memory memory/owner_mailbox index locks archive; do
        mkdir -p "${DATA_DIR}/${sub}"
    done

    # Initialize empty chat log if missing
    local chat_log="${DATA_DIR}/logs/chat.jsonl"
    [[ -f "$chat_log" ]] || touch "$chat_log"

    ok "Data directory: ${DATA_DIR}"
}

# ---------------------------------------------------------------------------
# 5) Interactive .env setup
# ---------------------------------------------------------------------------
prompt_secret() {
    local var_name="$1" prompt_text="$2" required="${3:-false}" default="${4:-}"
    local value=""

    if [[ -n "$default" ]]; then
        read -rp "$(echo -e "${BOLD}${prompt_text}${NC} [${default}]: ")" value
        value="${value:-$default}"
    else
        read -rp "$(echo -e "${BOLD}${prompt_text}${NC}: ")" value
    fi

    if [[ "$required" == "true" ]] && [[ -z "$value" ]]; then
        fatal "${var_name} is required"
    fi

    echo "$value"
}

setup_env() {
    echo ""
    echo -e "${BOLD}========================================${NC}"
    echo -e "${BOLD}  Ouroboros — Local Deployment Setup${NC}"
    echo -e "${BOLD}  LLM Backend: DeepSeek API${NC}"
    echo -e "${BOLD}========================================${NC}"
    echo ""

    if [[ -f "$ENV_FILE" ]]; then
        echo -e "${YELLOW}Existing .env file found.${NC}"
        read -rp "Overwrite? [y/N]: " overwrite
        if [[ "${overwrite,,}" != "y" ]]; then
            info "Keeping existing .env"
            return
        fi
    fi

    echo ""
    echo -e "${CYAN}--- Required secrets ---${NC}"
    echo ""

    local deepseek_key
    deepseek_key=$(prompt_secret "DEEPSEEK_API_KEY" \
        "DeepSeek API key (https://platform.deepseek.com/api_keys)" "true")

    local telegram_token
    telegram_token=$(prompt_secret "TELEGRAM_BOT_TOKEN" \
        "Telegram bot token (@BotFather)" "true")

    local github_token
    github_token=$(prompt_secret "GITHUB_TOKEN" \
        "GitHub personal access token (repo scope)" "true")

    local github_user
    github_user=$(prompt_secret "GITHUB_USER" \
        "GitHub username" "true")

    local github_repo
    github_repo=$(prompt_secret "GITHUB_REPO" \
        "GitHub repo name" "false" "ouroboros")

    local total_budget
    total_budget=$(prompt_secret "TOTAL_BUDGET" \
        "Total budget limit (USD)" "false" "10")

    echo ""
    echo -e "${CYAN}--- DeepSeek model configuration ---${NC}"
    echo ""
    echo "  Available models:"
    echo "    deepseek-chat      — DeepSeek-V3 (fast, cheap)"
    echo "    deepseek-reasoner  — DeepSeek-R1 (reasoning/thinking)"
    echo ""

    local model_main
    model_main=$(prompt_secret "OUROBOROS_MODEL" \
        "Primary model" "false" "deepseek-chat")

    local model_code
    model_code=$(prompt_secret "OUROBOROS_MODEL_CODE" \
        "Code editing model" "false" "deepseek-chat")

    local model_light
    model_light=$(prompt_secret "OUROBOROS_MODEL_LIGHT" \
        "Lightweight model (consciousness, summaries)" "false" "deepseek-chat")

    echo ""
    echo -e "${CYAN}--- Optional settings ---${NC}"
    echo ""

    local max_workers
    max_workers=$(prompt_secret "OUROBOROS_MAX_WORKERS" \
        "Max parallel workers" "false" "3")

    local max_rounds
    max_rounds=$(prompt_secret "OUROBOROS_MAX_ROUNDS" \
        "Max LLM rounds per task" "false" "200")

    # Write .env file
    cat > "$ENV_FILE" << ENVEOF
# =============================================================================
# Ouroboros — Local Deployment Configuration (DeepSeek API)
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# =============================================================================

# --- DeepSeek API ---
# Base URL is OpenAI-compatible: https://api.deepseek.com/v1
DEEPSEEK_API_KEY=${deepseek_key}
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# The launcher maps DEEPSEEK_API_KEY → OPENROUTER_API_KEY internally,
# since the LLM client uses the OpenAI SDK with configurable base_url.
OPENROUTER_API_KEY=${deepseek_key}

# --- Telegram ---
TELEGRAM_BOT_TOKEN=${telegram_token}

# --- GitHub ---
GITHUB_TOKEN=${github_token}
GITHUB_USER=${github_user}
GITHUB_REPO=${github_repo}

# --- Budget ---
TOTAL_BUDGET=${total_budget}

# --- Models (DeepSeek) ---
# deepseek-chat     = DeepSeek-V3 (general, fast, $0.27/1M input, $1.10/1M output)
# deepseek-reasoner = DeepSeek-R1 (reasoning, $0.55/1M input, $2.19/1M output)
OUROBOROS_MODEL=${model_main}
OUROBOROS_MODEL_CODE=${model_code}
OUROBOROS_MODEL_LIGHT=${model_light}

# --- Workers & timeouts ---
OUROBOROS_MAX_WORKERS=${max_workers}
OUROBOROS_MAX_ROUNDS=${max_rounds}
OUROBOROS_SOFT_TIMEOUT_SEC=600
OUROBOROS_HARD_TIMEOUT_SEC=1800

# --- LLM client override ---
# Forces the LLM client to use DeepSeek endpoint instead of OpenRouter
OUROBOROS_LLM_BASE_URL=https://api.deepseek.com/v1

# --- Optional keys (leave empty if not used) ---
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ENVEOF

    chmod 600 "$ENV_FILE"
    ok ".env file created (permissions: 600)"
}

# ---------------------------------------------------------------------------
# 6) Verify .env is loadable
# ---------------------------------------------------------------------------
verify_env() {
    [[ -f "$ENV_FILE" ]] || fatal ".env file not found. Run with --setup first."

    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a

    local missing=()
    [[ -z "${DEEPSEEK_API_KEY:-}" ]]    && missing+=("DEEPSEEK_API_KEY")
    [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]  && missing+=("TELEGRAM_BOT_TOKEN")
    [[ -z "${GITHUB_TOKEN:-}" ]]        && missing+=("GITHUB_TOKEN")
    [[ -z "${GITHUB_USER:-}" ]]         && missing+=("GITHUB_USER")

    if [[ ${#missing[@]} -gt 0 ]]; then
        fatal "Missing required variables in .env: ${missing[*]}"
    fi

    ok "Environment verified"
}

# ---------------------------------------------------------------------------
# 7) Quick API connectivity test
# ---------------------------------------------------------------------------
test_deepseek_api() {
    info "Testing DeepSeek API connectivity..."

    local response
    response=$(python -c "
import os, sys
from openai import OpenAI

client = OpenAI(
    api_key=os.environ['DEEPSEEK_API_KEY'],
    base_url='https://api.deepseek.com/v1',
)
try:
    resp = client.chat.completions.create(
        model='deepseek-chat',
        messages=[{'role': 'user', 'content': 'Reply with exactly: OK'}],
        max_tokens=5,
    )
    print(resp.choices[0].message.content.strip())
except Exception as e:
    print(f'FAIL: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1) || fatal "DeepSeek API test failed: ${response}"

    ok "DeepSeek API is reachable (response: ${response})"
}

# ---------------------------------------------------------------------------
# 8) Launch the agent
# ---------------------------------------------------------------------------
launch() {
    info "Starting Ouroboros with DeepSeek backend..."
    echo ""
    echo -e "${BOLD}  Model (main):  ${OUROBOROS_MODEL:-deepseek-chat}${NC}"
    echo -e "${BOLD}  Model (code):  ${OUROBOROS_MODEL_CODE:-deepseek-chat}${NC}"
    echo -e "${BOLD}  Model (light): ${OUROBOROS_MODEL_LIGHT:-deepseek-chat}${NC}"
    echo -e "${BOLD}  Workers:       ${OUROBOROS_MAX_WORKERS:-3}${NC}"
    echo -e "${BOLD}  Budget:        \$${TOTAL_BUDGET:-10}${NC}"
    echo -e "${BOLD}  Data dir:      ${DATA_DIR}${NC}"
    echo ""

    cd "$REPO_DIR"
    exec python "${LOCAL_LAUNCHER}" \
        --data-dir "$DATA_DIR" \
        --repo-dir "$REPO_DIR"
}

# ===========================================================================
# Main
# ===========================================================================

check_python

case "$MODE" in
    setup)
        setup_venv
        install_deps
        create_data_dirs
        setup_env
        echo ""
        ok "Setup complete. Run '$0 --start' to launch."
        ;;
    start)
        setup_venv
        create_data_dirs
        verify_env
        test_deepseek_api
        launch
        ;;
    full)
        setup_venv
        install_deps
        create_data_dirs
        setup_env
        verify_env
        test_deepseek_api
        launch
        ;;
esac
