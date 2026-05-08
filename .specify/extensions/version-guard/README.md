# spec-kit-version-guard

A [Spec Kit](https://github.com/github/spec-kit) extension that verifies locked tech stack versions against live registries before planning, writes version-correct API constraints, and validates generated code — ensuring LLM agents use up-to-date API patterns instead of stale training data.

## Problem

LLM agents generate code based on training data that may be months behind the latest library versions. When your project locks a dependency version newer than the model's training cutoff:

- Deprecated APIs get used instead of their replacements
- New features and patterns are missed
- Generated code may not compile or may use anti-patterns
- Silent regressions when API signatures have changed

## Solution

The Version Guard extension fires before `/speckit.plan`, fetching the latest stable versions from npm and comparing them against your locked versions. For any flagged packages, it fetches official migration guides and changelogs so the agent uses real documentation — not training data — for code generation. For packages at current versions, the report includes documentation URLs in the Current-Version References section — giving the agent a starting point to verify locked-version APIs rather than relying solely on training data. Before `/speckit.tasks` and `/speckit.implement`, a lightweight load command re-reads the cached constraints without hitting registries again.

## Installation

```bash
# From release
specify extension add version-guard --from https://github.com/KevinBrown5280/spec-kit-version-guard/archive/refs/tags/v1.2.0.zip

# From main branch
specify extension add version-guard --from https://github.com/KevinBrown5280/spec-kit-version-guard/archive/refs/heads/main.zip

# Development mode (local clone)
specify extension add --dev /path/to/spec-kit-version-guard
```

## Commands

| Command | Description | Modifies Files? |
|---------|-------------|-----------------|
| `speckit.version-guard.check` | Check locked tech stack versions against live npm registry, write constraints artifact | Yes — writes `<feature_dir>/version-guard-report.md` |
| `speckit.version-guard.load` | Load existing constraints artifact into agent context (falls back to full check if artifact is missing) | Typically no — may write `<feature_dir>/version-guard-report.md` on fallback |
| `speckit.version-guard.validate` | Scan generated code for version-incompatible API patterns flagged by version guard | Usually read-only; may re-run check if a prior skip is stale |

## How It Works

1. **Locate dependency versions**: Resolves actual locked versions using this precedence (first match wins per location): `package-lock.json` → `pnpm-lock.yaml` → `yarn.lock` → `package.json` (conservative proxy — minimum version the range satisfies). Evaluates each location independently in order: repo root → `frontend/` → `backend/`. Aggregates all packages; if the same package appears at multiple locations, the first location in that order wins. Reads `docs/reference/tech-stack-decision-record.md` as supplemental context when lockfiles or package.json are present, or as a **last-resort version source** for greenfield projects with no lockfile or package.json (extracts npm-relevant packages with exact versions, marked "(from decision record)").

2. **Fetch latest versions**: For each locked dependency, fetches `https://registry.npmjs.org/{package}/latest` and compares:

   | Package | Locked | Latest Stable | Status |
   |---------|--------|---------------|--------|
   | react   | 18.3.1 | 19.x.x       | ⚠️ Behind |
   | vite    | 6.0.0  | 6.2.x        | ✅ Current |

3. **Check for known issues**: For all locked versions (including current), checks GitHub security advisories and npm audit data for known CVEs or critical bugs affecting the locked version.

4. **Fetch migration documentation**: For packages that are ⚠️ Behind or have known issues, fetches official changelogs:
   - React: `https://react.dev/blog`
   - Vite: `https://vite.dev/blog`
   - Azure SDKs: Microsoft docs MCP tools
   - Others: GitHub releases page or CHANGELOG.md

5. **Write constraints artifact**: Creates `<feature_dir>/version-guard-report.md` (where `<feature_dir>` is read from `.specify/feature.json`, falling back to `.specify/`) with a two-channel constraint model:
   - **Compatibility Rules (mandatory)** — paired DON'T / DO tables ensuring generated code works with the *locked* version. For example, if you're on React 18, don't use React 19 APIs. These rules are checked by the validate command.
   - **Upgrade Guidance (informational)** — summarizes what's new in the latest version. Informational only — helps developers decide whether/when to upgrade. Not checked by validation.
   - **Known Issues** — CVEs and critical bugs affecting locked versions, with severity and links.
   - **Migration References** — URLs for deeper reading.

6. **CVE pause-and-confirm**: If any critical or high severity CVEs are found in locked versions, the command pauses in interactive contexts (conversational sessions) and requires the developer to explicitly acknowledge before continuing. In non-interactive contexts (automated pipelines, CI), the command emits a prominent warning and continues without hanging. This is the only scenario that may pause the workflow — it ensures developers are aware of serious security issues before code is generated against a vulnerable version.

7. **Load constraints into context**: The agent reads the constraints file and treats the Compatibility Rules as mandatory guidance for all subsequent steps (plan, tasks, and implement). Before task generation and implementation, the lightweight `load` command re-reads the artifact into context without re-fetching from registries. If the artifact is missing (e.g., `/speckit.implement` was run without `/speckit.plan`), the full check runs as a fallback.

8. **Post-implementation validation** (`after_implement`): The validate command reads the Compatibility Rules from the artifact, scans generated code for incompatible patterns from the DON'T column, and outputs an advisory report with the paired DO replacement for each match. It also surfaces any critical/high CVEs as a reminder.

9. **Non-blocking workflow**: Apart from critical/high CVE acknowledgment, the workflow is never blocked. The Compatibility Rules are mandatory guidance that the agent must follow when generating code for listed packages. The command always completes — it never permanently blocks.

## Hooks

| Hook | Command | Description |
|------|---------|-------------|
| `before_plan` | `speckit.version-guard.check` | Verify versions and write constraints before planning |
| `before_tasks` | `speckit.version-guard.load` | Load constraints into context before task generation |
| `before_implement` | `speckit.version-guard.load` | Load constraints into context before code generation (falls back to full check if artifact is missing) |
| `after_implement` | `speckit.version-guard.validate` | Scan generated code for version-incompatible API patterns |

## Graceful Degradation

- Network failures or rate limiting: classifies the package as ❓ Unverified, skips constraint generation, and notes the failure in the report
- No lockfile or `package.json`: falls back to `docs/reference/tech-stack-decision-record.md` as a last-resort version source for greenfield projects (extracts npm packages with exact versions, marked "(from decision record)")
- No lockfile, `package.json`, or tech stack decision record: outputs "ℹ️ No dependency sources found (no lockfile, package.json, or tech stack decision record) — skipping version check"
- All packages current and no known issues: writes a minimal report and outputs "✅ All locked versions are current"
- Known-issue lookups fail: warns and skips — does not fail the entire check
- Critical/high CVEs: pauses for developer acknowledgment in interactive contexts; emits prominent warning and continues in non-interactive contexts
- Output directory missing: creates it automatically
- Validation with no constraints artifact and no in-memory report: outputs "ℹ️ No version guard report found — skipping validation"
- False positives in validation: report is advisory — developer decides whether to act

## Requirements

- Spec Kit >= 0.2.0

## License

MIT
