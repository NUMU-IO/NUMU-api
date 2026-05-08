# spec-kit-conduct-ext

Conduct wrapper for [Spec Kit](https://github.com/github/spec-kit) that executes each spec-kit phase with sub-agents to stabilize behavior and reduce context pollution in the main agent.

## Commands

| Command | What it does |
|---------|-------------|
| `speckit.conduct.run` | Run one phase (`specify`, `plan`, `tasks`, or `implement`) through step-by-step sub-agent orchestration |

## Installation

Install from the latest release (requires a [tagged release](https://github.com/twbrandon7/spec-kit-conduct-ext/releases) to exist):

```sh
specify extension add --from https://github.com/twbrandon7/spec-kit-conduct-ext/archive/refs/tags/v1.0.0.zip
```

Or for local development:

```sh
specify extension add --dev /path/to/spec-kit-conduct-ext
```

## Usage

### Phase-by-Phase Execution

Run one phase at a time. Each invocation delegates that phase's steps to sub-agents and keeps the main agent context clean.

```
/speckit.conduct.run <phase> <feature name or requirements>
```

Supported phase values:

```
specify
plan
tasks
implement
```

Run each phase separately:

```
/speckit.conduct.run specify Build a pomodoro app
/speckit.conduct.run plan
/speckit.conduct.run tasks
/speckit.conduct.run implement
```

The command does not run all phases in one invocation. You should review outputs between phases and then trigger the next phase explicitly.

## Framework Configuration

If you want to explicitly specify the AI coding tool for orchestration, set `framework` in `.specify/extensions/conduct/conduct-config.yml`:

```yaml
framework: "copilot"
```

This file is created automatically when the extension is installed (using `conduct-config.template.yml` as the template). `load.sh` returns that value in its JSON output as `framework`. Supported values match the agent identifiers used by the loader: `copilot`, `claude`, `gemini`, `cursor-agent`, `qwen`, `opencode`, `codex`, `windsurf`, `kilocode`, `auggie`, `roo`, `codebuddy`, `amp`, `shai`, `kiro-cli`, `bob`, `qodercli`, `tabnine`, `kimi`, `generic`.

## Requirements

- Spec Kit `>=0.3.1`

## License

MIT
