#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(CDPATH="" cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF' >&2
Usage: .specify/scripts/bash/load.sh <phase>

Resolve the installed Spec Kit command file for the active coding agent.
The script returns JSON with the resolved file path and optional framework
metadata from .specify/extensions.yml.
EOF
}

json_escape() {
    local value="${1-}"
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    value=${value//$'\t'/\\t}
    printf '%s' "$value"
}

normalize_agent() {
    local agent="${1:-}"
    case "$agent" in
        cursor)
            echo "cursor-agent"
            ;;
        kiro)
            echo "kiro-cli"
            ;;
        github-copilot|github_copilot)
            echo "copilot"
            ;;
        *)
            echo "$agent"
            ;;
    esac
}

resolve_agent_path() {
    local repo_root="$1"
    local agent="$2"
    local phase="$3"

    case "$agent" in
        copilot)
            printf '%s/.github/agents/speckit.%s.agent.md\n' "$repo_root" "$phase"
            ;;
        claude)
            printf '%s/.claude/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        gemini)
            printf '%s/.gemini/commands/speckit.%s.toml\n' "$repo_root" "$phase"
            ;;
        cursor-agent)
            printf '%s/.cursor/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        qwen)
            printf '%s/.qwen/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        opencode)
            printf '%s/.opencode/command/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        codex)
            printf '%s/.codex/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        windsurf)
            printf '%s/.windsurf/workflows/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        kilocode)
            printf '%s/.kilocode/rules/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        auggie)
            printf '%s/.augment/rules/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        roo)
            printf '%s/.roo/rules/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        codebuddy)
            printf '%s/.codebuddy/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        amp)
            printf '%s/.agents/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        shai)
            printf '%s/.shai/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        kiro-cli)
            printf '%s/.kiro/prompts/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        bob)
            printf '%s/.bob/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        qodercli)
            printf '%s/.qoder/commands/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        tabnine)
            printf '%s/.tabnine/agent/commands/speckit.%s.toml\n' "$repo_root" "$phase"
            ;;
        kimi)
            printf '%s/.kimi/skills/speckit.%s.md\n' "$repo_root" "$phase"
            ;;
        generic)
            if [[ -n "${SPECIFY_AI_COMMANDS_DIR:-}" ]]; then
                printf '%s/%s/speckit.%s.md\n' "$repo_root" "$SPECIFY_AI_COMMANDS_DIR" "$phase"
            fi
            ;;
    esac
}

extract_framework() {
    local config_file="$1"

    [[ -f "$config_file" ]] || return 0

    awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        /^[[:space:]]*framework:[[:space:]]*/ {
            sub(/^[[:space:]]*framework:[[:space:]]*/, "")
            gsub(/^\"|\"$/, "")
            gsub(/^'\''|'\''$/, "")
            print
            exit
        }
    ' "$config_file"
}

main() {
    if [[ $# -ne 1 ]]; then
        usage
        exit 1
    fi

    local phase="$1"
    if [[ ! "$phase" =~ ^[a-z0-9-]+$ ]]; then
        echo "ERROR: Invalid phase '$phase'. Expected lowercase letters, numbers, or hyphens." >&2
        exit 1
    fi

    local repo_root
    repo_root="$(get_repo_root)"
    local config_file="$repo_root/.specify/extensions/conduct/conduct-config.yml"
    local framework=""
    framework="$(extract_framework "$config_file")"

    local -a ordered_agents=()
    local preferred_agent=""
    preferred_agent="$(normalize_agent "${ORCHESTRATION_AGENT:-${SPECIFY_AI:-}}")"
    if [[ -n "$preferred_agent" ]]; then
        ordered_agents+=("$preferred_agent")
    fi

    local agent
    for agent in copilot claude gemini cursor-agent qwen opencode codex windsurf kilocode auggie roo codebuddy amp shai kiro-cli bob qodercli tabnine kimi generic; do
        if [[ "$agent" != "$preferred_agent" ]]; then
            ordered_agents+=("$agent")
        fi
    done

    local resolved_agent=""
    local resolved_path=""
    local candidate_path=""

    for agent in "${ordered_agents[@]}"; do
        candidate_path="$(resolve_agent_path "$repo_root" "$agent" "$phase")"
        if [[ -n "$candidate_path" && -f "$candidate_path" ]]; then
            resolved_agent="$agent"
            resolved_path="$candidate_path"
            break
        fi
    done

    if [[ -z "$resolved_path" ]]; then
        echo "ERROR: Unable to resolve an installed command file for phase '$phase'." >&2
        echo "Checked repo root: $repo_root" >&2
        if [[ -n "$preferred_agent" ]]; then
            echo "Preferred agent override: $preferred_agent" >&2
        fi
        exit 1
    fi

    cat "$resolved_path"
}

main "$@"
