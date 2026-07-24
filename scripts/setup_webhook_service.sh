#!/usr/bin/env bash
#
# setup_webhook_service.sh
# ========================
# Tu dong tao systemd service cho Super Agent Webhook Daemon (Phase 7)
#
# Usage:
#   chmod +x scripts/setup_webhook_service.sh
#   sudo ./scripts/setup_webhook_service.sh
#
# Hoac tu dong:
#   sudo ./scripts/setup_webhook_service.sh --user ubuntu --dir /home/ubuntu/super-agent
#
# Environment variables doc truoc khi chay:
#   GITHUB_PERSONAL_ACCESS_TOKEN    (bat buoc, neu chua co trong env)
#   WEBHOOK_SECRET                  (optional, secret cho GitHub webhook)
#   WEBHOOK_PORT                    (optional, mac dinh 11999)

set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

# ─── Parse arguments ──────────────────────────────────────────────────────
DEPLOY_USER=""
DEPLOY_DIR=""
WEBHOOK_PORT="${WEBHOOK_PORT:-11999}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)     DEPLOY_USER="$2"; shift 2 ;;
        --dir)      DEPLOY_DIR="$2";  shift 2 ;;
        --port)     WEBHOOK_PORT="$2"; shift 2 ;;
        --secret)   WEBHOOK_SECRET="$2"; shift 2 ;;
        --help|-h)  echo "Usage: sudo $0 [--user <user>] [--dir <path>] [--port <port>] [--secret <secret>]"; exit 0 ;;
        *)          log_error "Unknown argument: $1"; exit 1 ;;
    esac
done

# ─── Auto-detect user & dir ───────────────────────────────────────────────
if [[ -z "$DEPLOY_USER" ]]; then
    # Try to get the non-root user who invoked sudo
    DEPLOY_USER="${SUDO_USER:-$(whoami)}"
fi

if [[ -z "$DEPLOY_DIR" ]]; then
    # Default to current directory
    DEPLOY_DIR="$(pwd)"
fi

# Validate
if [[ "$(id -u)" -ne 0 ]]; then
    log_error "This script must be run as root (sudo)."
    echo "  Try: sudo $0 --user ${DEPLOY_USER} --dir ${DEPLOY_DIR}"
    exit 1
fi

if ! id "$DEPLOY_USER" &>/dev/null; then
    log_error "User '$DEPLOY_USER' does not exist."
    exit 1
fi

if [[ ! -d "$DEPLOY_DIR" ]]; then
    log_error "Directory '$DEPLOY_DIR' does not exist."
    exit 1
fi

# ─── Check dependencies ───────────────────────────────────────────────────
log_step "1/5 Checking dependencies..."

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    log_error "Python 3 not found. Install: apt install python3"
    exit 1
fi
log_info "Python: $($PYTHON_BIN --version)"

# Check gh CLI
if ! command -v gh &>/dev/null; then
    log_warn "gh CLI not found. Install: apt install gh  or  https://cli.github.com/"
    log_warn "Webhook daemon can still run, but log fetch and PR creation will fail."
fi

# Check webhook_handler.py exists
HANDLER_PATH="${DEPLOY_DIR}/scripts/webhook_handler.py"
if [[ ! -f "$HANDLER_PATH" ]]; then
    log_error "webhook_handler.py not found at ${HANDLER_PATH}"
    echo "  Ensure this script is run from the super-agent-plugin directory."
    exit 1
fi
log_info "Found: $HANDLER_PATH"

# ─── Check GITHUB_TOKEN ───────────────────────────────────────────────────
log_step "2/5 Checking GitHub token..."

# Doc token tu nhieu nguon
GITHUB_TOKEN=""
if [[ -n "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]]; then
    GITHUB_TOKEN="$GITHUB_PERSONAL_ACCESS_TOKEN"
elif [[ -n "${GITHUB_TOKEN:-}" ]]; then
    GITHUB_TOKEN="$GITHUB_TOKEN"
elif [[ -n "${GH_TOKEN:-}" ]]; then
    GITHUB_TOKEN="$GH_TOKEN"
fi

if [[ -z "$GITHUB_TOKEN" ]]; then
    log_warn "No GitHub token found in environment."
    log_warn "Set GITHUB_PERSONAL_ACCESS_TOKEN before running, or edit the service file later."
    log_warn "Without it, gh CLI calls will fail (log fetch + PR creation)."
    USE_TOKEN=false
else
    TOKEN_PREFIX="${GITHUB_TOKEN:0:8}"
    log_info "GitHub token found: ${TOKEN_PREFIX}..."
    USE_TOKEN=true
fi

# ─── Create systemd service file ──────────────────────────────────────────
log_step "3/5 Creating systemd service file..."

SERVICE_NAME="super-agent-webhook.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

# Build ExecStart
EXEC_START="${PYTHON_BIN} ${HANDLER_PATH} --daemon --port ${WEBHOOK_PORT}"
if [[ -n "$WEBHOOK_SECRET" ]]; then
    EXEC_START="${EXEC_START} --secret ${WEBHOOK_SECRET}"
fi

# Build Environment lines
ENV_LINES=""
ENV_LINES+="Environment=PYTHONUNBUFFERED=1"$'\n'
ENV_LINES+="Environment=GIT_REMOTE=origin"$'\n'
ENV_LINES+="Environment=GIT_BASE_BRANCH=dev"$'\n'
ENV_LINES+="Environment=MAX_LOG_CHARS=3000"$'\n'
ENV_LINES+="Environment=GITHUB_REPOSITORY=thetime1102/nhatvicake-core"$'\n'

if [[ "$USE_TOKEN" == true ]]; then
    ENV_LINES+="Environment=GH_TOKEN=${GITHUB_TOKEN}"$'\n'
    ENV_LINES+="Environment=GITHUB_TOKEN=${GITHUB_TOKEN}"$'\n'
fi

cat > "$SERVICE_PATH" << SERVICEEOF
[Unit]
Description=Super Agent Webhook Daemon (Phase 7 — CI/CD Auto-Fix)
Documentation=https://github.com/thetime1102/super-agent-plugin
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${DEPLOY_USER}
Group=$(id -gn "$DEPLOY_USER")
WorkingDirectory=${DEPLOY_DIR}
ExecStart=${EXEC_START}
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

# Environment
${ENV_LINES}
Environment=HOME=/home/${DEPLOY_USER}

# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=${DEPLOY_DIR}

# Logging
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICEEOF

chmod 644 "$SERVICE_PATH"
log_info "Created: ${SERVICE_PATH}"

# ─── Reload systemd & enable service ──────────────────────────────────────
log_step "4/5 Enabling and starting service..."

systemctl daemon-reload
log_info "systemctl daemon-reload: OK"

systemctl enable "${SERVICE_NAME}"
log_info "systemctl enable ${SERVICE_NAME}: OK"

systemctl start "${SERVICE_NAME}"
log_info "systemctl start ${SERVICE_NAME}: OK"

# ─── Verify ────────────────────────────────────────────────────────────────
log_step "5/5 Verifying service status..."

sleep 2

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    log_info "Service is ACTIVE."
else
    log_warn "Service is not active. Checking status..."
fi

echo ""
echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Super Agent Webhook Daemon installed successfully!${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Service name:  ${SERVICE_NAME}"
echo "  Deploy user:   ${DEPLOY_USER}"
echo "  Deploy dir:    ${DEPLOY_DIR}"
echo "  Python:        ${PYTHON_BIN}"
echo "  Port:          ${WEBHOOK_PORT}"
echo "  Token set:     ${USE_TOKEN}"
echo ""
echo "  Commands:"
echo "    Start:   sudo systemctl start ${SERVICE_NAME}"
echo "    Stop:    sudo systemctl stop ${SERVICE_NAME}"
echo "    Restart: sudo systemctl restart ${SERVICE_NAME}"
echo "    Status:  sudo systemctl status ${SERVICE_NAME}"
echo ""
echo "  Logs:"
echo "    Follow:  sudo journalctl -u ${SERVICE_NAME} -f"
echo "    Recent:  sudo journalctl -u ${SERVICE_NAME} --since '5 minutes ago'"
echo "    All:     sudo journalctl -u ${SERVICE_NAME}"
echo ""
echo "  GitHub Webhook URL:"
echo "    https://<cloudflare-tunnel-url>/webhook"
echo "    Events: Workflow runs"
echo "    Secret: ${WEBHOOK_SECRET:-<not set>}"
echo ""
echo -e "${YELLOW}  💡 Tip: To view live logs, run:${NC}"
echo -e "${YELLOW}    sudo journalctl -u ${SERVICE_NAME} -f --output cat${NC}"
echo ""
