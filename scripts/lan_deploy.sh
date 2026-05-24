#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Deploy this Hermes Agent checkout to another computer on the same LAN.

Usage:
  scripts/lan_deploy.sh [options] user@host [remote_dir]

Examples:
  scripts/lan_deploy.sh lufei@10.142.205.88
  scripts/lan_deploy.sh --copy-state lufei@10.142.205.88 ~/develop/hermes-agent
  scripts/lan_deploy.sh --takeover-weixin --start-gateway lufei@10.142.205.88
  scripts/lan_deploy.sh --start-dashboard-lan lufei@10.142.205.88

Options:
  --copy-state           Copy minimal ~/.hermes runtime state:
                         .env, config.yaml, auth.json, skills, plugins,
                         weixin account context files, and xhs cookies.
  --takeover-weixin      Copy Weixin state and stop the local gateway first.
                         Use when the remote computer should receive Weixin DMs.
                         Implies --copy-state.
  --start-gateway        Install/start Hermes gateway service on the remote.
  --start-dashboard-lan  Start remote dashboard on 0.0.0.0:9119 for LAN access.
                         This uses --insecure because dashboard exposes config/API keys.
  --no-install           Only sync files; skip remote dependency install.
  -h, --help             Show this help.

Notes:
  - SSH must be enabled on the target computer.
  - The script syncs uncommitted working-tree changes.
  - .venv, venv, node_modules, caches, logs, and large local caches are excluded.
  - Weixin iLink tokens can only be used by one gateway at a time. Do not run
    local and remote Weixin gateways concurrently with the same token.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

COPY_STATE=0
TAKEOVER_WEIXIN=0
START_GATEWAY=0
START_DASHBOARD_LAN=0
INSTALL_DEPS=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --copy-state)
      COPY_STATE=1
      shift
      ;;
    --takeover-weixin)
      COPY_STATE=1
      TAKEOVER_WEIXIN=1
      shift
      ;;
    --start-gateway)
      START_GATEWAY=1
      shift
      ;;
    --start-dashboard-lan)
      START_DASHBOARD_LAN=1
      shift
      ;;
    --no-install)
      INSTALL_DEPS=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage >&2
  exit 2
fi

TARGET="$1"
REMOTE_DIR="${2:-~/develop/hermes-agent}"
REMOTE_HOME='~/.hermes'

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

need_cmd ssh
need_cmd rsync

SSH_IDENTITY="${HERMES_DEPLOY_SSH_IDENTITY:-$HOME/.ssh/id_ed25519}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes)
if [[ -f "$SSH_IDENTITY" ]]; then
  SSH_OPTS+=(-i "$SSH_IDENTITY")
fi
RSYNC_SSH=(ssh "${SSH_OPTS[@]}")
printf -v RSYNC_RSH '%q ' "${RSYNC_SSH[@]}"

if [[ "$TAKEOVER_WEIXIN" == "1" ]]; then
  echo "→ takeover-weixin requested; stopping local gateway to release iLink token lock"
  if [[ -x "$REPO_DIR/.venv/bin/hermes" ]]; then
    "$REPO_DIR/.venv/bin/hermes" gateway stop || true
  elif command -v hermes >/dev/null 2>&1; then
    hermes gateway stop || true
  else
    echo "  Local hermes command not found; continuing without local stop." >&2
  fi
fi

echo "→ verifying SSH: $TARGET"
ssh "${SSH_OPTS[@]}" "$TARGET" 'printf "ok\n"' >/dev/null

echo "→ creating remote directory: $REMOTE_DIR"
ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p $REMOTE_DIR"

echo "→ syncing Hermes Agent checkout"
rsync -az --delete -e "$RSYNC_RSH" \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude 'node_modules/' \
  --exclude 'web/node_modules/' \
  --exclude 'ui-tui/node_modules/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$REPO_DIR"/ "$TARGET:$REMOTE_DIR"/

if [[ "$COPY_STATE" == "1" ]]; then
  echo "→ syncing minimal Hermes runtime state"
  ssh "$TARGET" "mkdir -p $REMOTE_HOME"
  rsync -az \
    --relative \
    --exclude 'cache/' \
    --exclude 'logs/' \
    --exclude 'sessions/' \
    --exclude 'audio_cache/' \
    --exclude 'image_cache/' \
    --exclude 'sandboxes/' \
    --exclude 'xhs-chrome-profile/' \
    "$HERMES_HOME"/./.env \
    "$HERMES_HOME"/./config.yaml \
    "$HERMES_HOME"/./auth.json \
    "$HERMES_HOME"/./skills \
    "$HERMES_HOME"/./plugins \
    "$HERMES_HOME"/./weixin \
    "$HERMES_HOME"/./xhs_cookies.json \
    "$TARGET:$REMOTE_HOME"/ 2>/dev/null || true
fi

if [[ "$INSTALL_DEPS" == "1" ]]; then
  echo "→ installing dependencies on remote"
  ssh "${SSH_OPTS[@]}" "$TARGET" "cd $REMOTE_DIR && bash -s" <<'REMOTE'
set -euo pipefail
export UV_NO_CONFIG=1
if ! command -v uv >/dev/null 2>&1; then
  echo "  installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv was not found after install; add ~/.local/bin or ~/.cargo/bin to PATH" >&2
  exit 1
fi
uv python install 3.11 >/dev/null
if [[ -d .venv ]]; then
  echo "  reusing existing .venv"
else
  uv venv .venv --python 3.11
fi
uv pip install -e ".[all,dev]" || uv pip install -e ".[all]"
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/.venv/bin/hermes" "$HOME/.local/bin/hermes"
echo "  hermes path: $PWD/.venv/bin/hermes"
REMOTE
fi

if [[ "$START_GATEWAY" == "1" ]]; then
  echo "→ installing/starting remote gateway"
  ssh "${SSH_OPTS[@]}" "$TARGET" "cd $REMOTE_DIR && ./.venv/bin/hermes gateway install && ./.venv/bin/hermes gateway start && ./.venv/bin/hermes gateway status"
fi

if [[ "$START_DASHBOARD_LAN" == "1" ]]; then
  echo "→ starting remote dashboard on LAN: 0.0.0.0:9119"
  ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p ~/.hermes/logs; cd $REMOTE_DIR; nohup ./.venv/bin/hermes dashboard --host 0.0.0.0 --port 9119 --insecure --no-open --tui > ~/.hermes/logs/dashboard-lan.log 2>&1 &"
fi

echo ""
echo "✓ LAN deployment finished"
echo "  target:     $TARGET"
echo "  remote dir: $REMOTE_DIR"
echo ""
echo "Remote quick checks:"
echo "  ssh $TARGET 'cd $REMOTE_DIR && ./.venv/bin/hermes --version'"
echo "  ssh $TARGET 'cd $REMOTE_DIR && ./.venv/bin/hermes gateway status'"
if [[ "$START_DASHBOARD_LAN" == "1" ]]; then
  echo "  dashboard:  http://<remote-lan-ip>:9119"
fi
