# Changelog

## [1.2.0] - 2026-04-22

### Fixed
- All-current path: `check.md` self-instruction referenced Migration References URLs that did not exist when all packages were current — the template wrote "None required" while the instruction said to consult those URLs. This internal contradiction is now resolved.

### Changed
- `check.md` Step 6 (all-current template): adds `### Current-Version References` subsection under `## Migration References` with documentation URLs for each current package
- `check.md` Step 6 (standard template): adds `### Current-Version References` subsection for ✅ Current packages in mixed reports (behind-package URLs remain above as before)
- `check.md` Step 6 (all-current template): adds hint in empty Compatibility Rules block pointing to Migration References
- `check.md` Step 7.6, `load.md` Step 3: self-instructions explicitly address training-data cutoff risk for packages at current versions
- `load.md` Step 3 summary template: reports count of current-version documentation references when available

### Unchanged
- Behind-package entries in Migration References (no regression — existing URLs for ⚠️ Behind packages remain above the new subsection in mixed reports)
- `validate.md` (no changes — validation scope remains Compatibility Rules only)
- Check-time network calls (no new HTTP fetches; URL strings are statically embedded)

## [1.1.0] - 2026-04-22

### Added
- **Constraints artifact**: `speckit.version-guard.check` now writes `<feature_dir>/version-guard-report.md` (where `<feature_dir>` is read from `.specify/feature.json`, falling back to `.specify/` if unavailable) with a two-channel constraint model that subsequent plan, task, and implement phases reference
- **Compatibility Rules (mandatory)**: Paired DON'T / DO tables ensuring generated code works with the *locked* version — checked by the validate command
- **Upgrade Guidance (informational)**: Summarizes what's new in the latest version — informational only, never checked by validation
- **Post-implementation validation**: New `speckit.version-guard.validate` command scans generated code for version-incompatible API patterns from the Compatibility Rules (advisory, non-blocking)
- **Lightweight load command**: New `speckit.version-guard.load` command re-reads the constraints artifact into context before task generation and implementation without re-fetching from registries; falls back to full check if artifact is missing
- **`after_implement` hook**: Automatically runs validation after `/speckit.implement`
- **`before_tasks` hook**: Loads version guard constraints before `/speckit.tasks` so task breakdowns respect version boundaries
- **Known issue checks**: All locked versions are checked for known CVEs and critical bugs via GitHub security advisories and npm audit data; critical/high CVEs trigger a pause-and-confirm in interactive contexts or a prominent warning in non-interactive contexts

### Changed
- Version status table uses three statuses: ✅ Current (same major version), ⚠️ Behind (different major version), ❓ Unverified (couldn't check — network failure, rate limiting, etc.). For `0.x` packages, minor version is also compared per SemVer (breaking changes are expected in minor bumps).
- Check command resolves actual locked versions from lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`) first, falling back to `package.json` only when no lockfile exists (using the minimum version a range satisfies as a conservative proxy)
- Check command loads the Compatibility Rules into agent context with explicit instructions to treat them as mandatory guidance
- `docs/reference/tech-stack-decision-record.md` is treated as supplemental context when lockfiles or package.json are present. When NO lockfile or package.json exists (greenfield projects), the decision record is promoted to a last-resort version source — npm-relevant packages with exact versions are extracted and marked "(from decision record)"
- All constraints target the locked version (not the latest) — the resolved version from the lockfile (or `package.json` fallback) is the baseline for all constraint generation

## [1.0.0] - 2026-04-20

### Added
- Initial release
- `speckit.version-guard.check` command to verify locked versions against live npm registry
- Fetches official migration guides and changelogs for flagged packages
- `before_plan` and `before_implement` hooks
- Graceful degradation for network failures, missing `package.json` or tech stack record, and rate limiting
