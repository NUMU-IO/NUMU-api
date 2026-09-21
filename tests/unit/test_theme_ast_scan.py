"""The theme bundle security scan (apps plan, Phase 8 item 8.3).

Two properties, both of which the previous implementation got wrong:

  * It scanned `dist/theme.js` alone. `dist/theme.server.js` is imported by
    the storefront's SSR worker, so anything hidden there executes inside
    NUMU's own Node process, and a code-split build could park it in a
    sibling chunk instead.
  * When the scanner could not run it silently degraded to a five-substring
    regex pass that read as a clean scan. Worse, the scanner was written to
    a temp directory, where `require('acorn')` can never resolve — so that
    degraded pass was in practice the only scan that ever ran.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.infrastructure.messaging.tasks import theme_upload_tasks as t

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="the scanner needs Node on PATH"
)


def _acorn_root() -> str | None:
    """A directory acorn resolves from, or None (then the test is skipped).

    Only the sandboxed builder image is guaranteed to carry acorn; on a dev
    machine the theme CLI's node_modules is the usual copy.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "numu-theme-cli" / "node_modules" / "acorn"
        if candidate.is_dir():
            return str(parent / "numu-theme-cli")
    return None


def _dist(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path]:
    theme_dir = tmp_path / "theme"
    dist = theme_dir / "dist"
    dist.mkdir(parents=True)
    for name, source in files.items():
        (dist / name).write_text(source, encoding="utf-8")
    return dist, theme_dir


def test_a_scanner_that_cannot_run_fails_the_build_instead_of_passing_it(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(t, "USE_DOCKER", False)
    monkeypatch.setenv("NUMU_ACORN_ROOT", str(tmp_path / "nowhere"))
    dist, theme_dir = _dist(tmp_path, {"theme.js": "export const a = 1;"})

    violations = t._ast_security_scan(dist, theme_dir)

    assert violations, "an unrunnable scanner must not read as a clean scan"
    assert violations[0].startswith("scan_error:")


def test_a_build_that_emitted_no_javascript_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "USE_DOCKER", False)
    dist, theme_dir = _dist(tmp_path, {"theme.css": "body{color:red}"})

    assert t._ast_security_scan(dist, theme_dir) == [
        "scan_error: build emitted no JavaScript to scan"
    ]


def test_every_emitted_chunk_is_scanned_not_just_the_entry(tmp_path, monkeypatch):
    root = _acorn_root()
    if root is None:
        pytest.skip("no acorn checkout to resolve from")
    monkeypatch.setattr(t, "USE_DOCKER", False)
    monkeypatch.setenv("NUMU_ACORN_ROOT", root)
    dist, theme_dir = _dist(
        tmp_path,
        {
            "theme.js": "export const mount = () => {};",
            # The SSR bundle runs server-side, inside NUMU's worker.
            "theme.server.js": "require('child_process');",
            "chunk-abc123.mjs": "export const x = eval('1+1');",
        },
    )

    violations = t._ast_security_scan(dist, theme_dir)

    assert any("theme.server.js" in v and "child_process" in v for v in violations)
    assert any("chunk-abc123.mjs" in v and v.startswith("eval:") for v in violations)
    assert not any("theme.js:" in v for v in violations)


def test_a_clean_bundle_passes(tmp_path, monkeypatch):
    root = _acorn_root()
    if root is None:
        pytest.skip("no acorn checkout to resolve from")
    monkeypatch.setattr(t, "USE_DOCKER", False)
    monkeypatch.setenv("NUMU_ACORN_ROOT", root)
    dist, theme_dir = _dist(
        tmp_path,
        {
            "theme.js": "export const mount = (el) => { el.textContent = 'hi'; };",
            "theme.server.js": "export const render = () => '<div>hi</div>';",
        },
    )

    assert t._ast_security_scan(dist, theme_dir) == []


def test_the_regex_fallback_is_gone(tmp_path):
    # It was the only scan that ever ran, and it passed on anything its five
    # substrings missed. Keep it deleted.
    assert not hasattr(t, "_fallback_regex_scan")


def test_unparseable_javascript_is_a_violation_not_a_pass(tmp_path, monkeypatch):
    root = _acorn_root()
    if root is None:
        pytest.skip("no acorn checkout to resolve from")
    monkeypatch.setattr(t, "USE_DOCKER", False)
    monkeypatch.setenv("NUMU_ACORN_ROOT", root)
    dist, theme_dir = _dist(tmp_path, {"theme.js": "export const = ;;;"})

    violations = t._ast_security_scan(dist, theme_dir)

    assert any(v.startswith("parse_error:") for v in violations)


def test_the_scanner_runs_in_the_builder_image_when_docker_is_on(monkeypatch, tmp_path):
    # In production the worker host has no Node at all; the build already
    # happens inside the builder image, and that image is where acorn lives.
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, '{"violations": []}', "")

    monkeypatch.setattr(t, "USE_DOCKER", True)
    monkeypatch.setattr(t.subprocess, "run", fake_run)
    dist, theme_dir = _dist(tmp_path, {"theme.js": "export const a = 1;"})

    assert t._ast_security_scan(dist, theme_dir) == []
    cmd = seen["cmd"]
    assert cmd[0] == "docker"
    assert "--network=none" in cmd
    assert "NODE_PATH=/usr/local/lib/node_modules" in cmd
    assert cmd[-1] == "/theme-dist/theme.js"
