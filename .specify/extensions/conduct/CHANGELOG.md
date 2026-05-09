# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-04-04

### Added
- `.extensionignore` to selectively ignore specific files and directories during the extension installation process.
- Integration test coverage for extension installation, validating script loading and command registration.

### Changed
- Integrated and configured `uv` as the default package and dependency manager.

### Fixed
- Refined the `conduct` agent prompt to strictly enforce one-step delegation per sub-agent, preventing single sub-agents from erroneously executing entire multi-step tasks.
- Corrected the required Spec Kit version constraint.

## [1.0.0] - 2026-03-04

### Added

- Initial release of the Conduct Extension
- `speckit.conduct.run` command — orchestrates a single spec-kit phase (specify, plan, tasks, or implement) through step-by-step sub-agent delegation
