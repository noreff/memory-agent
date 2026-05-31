#!/bin/sh
# memory-agent one-command installer:
#   curl -fsSL https://raw.githubusercontent.com/noreff/memory-agent/main/install.sh | sh
# With the Claude Code CLI present: registers the plugin marketplace + installs the plugin
# (no source checkout to manage). Otherwise: clones to ~/.memory-agent and runs the classic
# installer (hooks + commands + macOS background collector).
set -e

if command -v claude >/dev/null 2>&1; then
  echo "Claude Code CLI found — installing as a plugin…"
  claude plugin marketplace add noreff/memory-agent || true   # idempotent re-runs
  claude plugin install memory-agent@memory-agent
  echo ""
  echo "✔ Installed. Open Claude Code and run /memory-setup — it takes it from there."
else
  echo "Claude Code CLI not found — falling back to a source install…"
  if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    echo "Need git and python3 for the fallback install. Install them (or the Claude Code CLI) and re-run." >&2
    exit 1
  fi
  DIR="${MEMORY_AGENT_HOME:-$HOME/.memory-agent}"
  if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only
  else
    git clone https://github.com/noreff/memory-agent "$DIR"
  fi
  python3 "$DIR/install.py"
fi
