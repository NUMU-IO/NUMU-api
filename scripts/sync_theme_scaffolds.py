#!/usr/bin/env python3
"""Bundle V3 theme *source* trees into the API's theme_scaffold/ directory.

The code editor (Online Store -> Edit code) seeds a new store's workspace from
a bundled, buildable theme source. `v3_starter` is the generic fallback; this
script copies each real V3 theme's editable source (NOT node_modules/dist) so a
store whose active theme is e.g. Empire seeds Empire's actual code.

The bundled dir is named by the theme's canonical id (theme.json -> "id"),
which equals the slug the storefront/DB use for the active theme — so
ThemeCodeService can map active-theme-slug -> bundled source directly.

Run locally after pulling theme changes; the generated dirs are committed so
they ship in the API image (CI has no access to the V3-themes repos):

    python scripts/sync_theme_scaffolds.py            # all themes
    python scripts/sync_theme_scaffolds.py empire-v3  # one (by id)

Pass --themes-root to point at the V3-themes checkout (defaults to a sibling
of the NUMU-api repo).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Only these top-level entries are part of a buildable theme source. Anything
# else (node_modules, dist, lockfiles, .git, caches) is intentionally dropped.
INCLUDE = {
    "src",
    "schemas",
    "templates",
    "index.html",
    "package.json",
    "theme.json",
    "settings_schema.json",
    "styles.css",
    "tsconfig.json",
    "vite.config.ts",
    ".gitignore",
}
# Never copy these even if they appear inside an included dir.
IGNORE = shutil.ignore_patterns(
    "node_modules",
    "dist",
    ".git",
    ".turbo",
    "*.log",
    "package-lock.json",
    "bun.lockb",
    "pnpm-lock.yaml",
    ".DS_Store",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD_BASE = REPO_ROOT / "src" / "infrastructure" / "theme_scaffold"


def theme_id_of(repo: Path) -> str | None:
    tj = repo / "theme.json"
    if not tj.is_file():
        return None
    try:
        return (
            str(json.loads(tj.read_text(encoding="utf-8")).get("id") or "").strip()
            or None
        )
    except json.JSONDecodeError:
        return None


def sync_one(repo: Path) -> tuple[str, int] | None:
    theme_id = theme_id_of(repo)
    if not theme_id:
        print(f"  [skip] {repo.name}: no theme.json id")
        return None
    dest = SCAFFOLD_BASE / theme_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    count = 0
    for entry in sorted(repo.iterdir()):
        if entry.name not in INCLUDE:
            continue
        target = dest / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, ignore=IGNORE)
            count += sum(1 for p in target.rglob("*") if p.is_file())
        else:
            shutil.copy2(entry, target)
            count += 1
    print(f"  [ok] {repo.name:36} -> theme_scaffold/{theme_id}  ({count} files)")
    return theme_id, count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="*", help="Theme id(s) to sync (default: all)")
    ap.add_argument(
        "--themes-root",
        default=str(REPO_ROOT.parent / "V3-themes"),
        help="Path to the V3-themes checkout",
    )
    args = ap.parse_args()

    themes_root = Path(args.themes_root)
    if not themes_root.is_dir():
        print(f"V3-themes root not found: {themes_root}", file=sys.stderr)
        return 1

    repos = sorted(p for p in themes_root.glob("*-engine-V3") if p.is_dir())
    if args.only:
        wanted = set(args.only)
        repos = [r for r in repos if (theme_id_of(r) in wanted)]
    if not repos:
        print("No matching theme repos found.", file=sys.stderr)
        return 1

    print(f"Syncing {len(repos)} theme source(s) -> {SCAFFOLD_BASE}")
    synced = [s for r in repos if (s := sync_one(r))]
    print(f"\nDone: {len(synced)} bundled ({sum(c for _, c in synced)} files total).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
