"""Cross-platform Locust load test runner.

Works on Windows, macOS, and Linux — no `make` required.

Usage:
    python scripts/load_test.py smoke          # 10 users,  1 min
    python scripts/load_test.py load           # 100 users, 5 min  (default)
    python scripts/load_test.py stress         # 500 users, 10 min
    python scripts/load_test.py ui             # interactive web UI
    python scripts/load_test.py --help
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Resolve paths relative to repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
LOCUST_FILE = REPO_ROOT / "tests" / "load" / "locustfile.py"
RESULTS_DIR = REPO_ROOT / "tests" / "load" / "results"

PROFILES = {
    "smoke": {"users": 10, "spawn_rate": 2, "run_time": "1m"},
    "load": {"users": 100, "spawn_rate": 10, "run_time": "5m"},
    "stress": {"users": 500, "spawn_rate": 25, "run_time": "10m"},
}


def ensure_results_dir():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def run_profile(profile: str, host: str):
    cfg = PROFILES[profile]
    ensure_results_dir()
    csv_prefix = RESULTS_DIR / profile
    html_report = RESULTS_DIR / f"{profile}.html"

    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(LOCUST_FILE),
        "--host",
        host,
        "--users",
        str(cfg["users"]),
        "--spawn-rate",
        str(cfg["spawn_rate"]),
        "--run-time",
        cfg["run_time"],
        "--headless",
        "--csv",
        str(csv_prefix),
        "--html",
        str(html_report),
    ]

    print(f"\n  Profile : {profile}")
    print(f"  Users   : {cfg['users']}")
    print(f"  Rate    : {cfg['spawn_rate']}/s")
    print(f"  Duration: {cfg['run_time']}")
    print(f"  Host    : {host}")
    print(f"  Results : {RESULTS_DIR}")
    print()

    return subprocess.call(cmd)


def run_ui(host: str, port: int):
    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(LOCUST_FILE),
        "--host",
        host,
        "--web-port",
        str(port),
    ]

    print(f"\n  Locust web UI starting on http://localhost:{port}")
    print(f"  Target host: {host}")
    print()

    return subprocess.call(cmd)


def main():
    parser = argparse.ArgumentParser(
        description="NUMU API load test runner (cross-platform)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Profiles:
  smoke    10 users,  2/s spawn,  1 min   — CI sanity check
  load     100 users, 10/s spawn, 5 min   — standard load test
  stress   500 users, 25/s spawn, 10 min  — capacity planning
  ui       opens Locust interactive web UI
""",
    )
    parser.add_argument(
        "profile",
        nargs="?",
        default="load",
        choices=["smoke", "load", "stress", "ui"],
        help="Test profile to run (default: load)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("LOCUST_HOST", "http://localhost:8021"),
        help="Target host (default: http://localhost:8021 or LOCUST_HOST env var)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8089,
        help="Web UI port for 'ui' profile (default: 8089)",
    )

    args = parser.parse_args()

    if not LOCUST_FILE.exists():
        print(f"Error: locustfile not found at {LOCUST_FILE}", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("LOAD_TEST_SUBDOMAIN"):
        print(
            "WARNING: LOAD_TEST_SUBDOMAIN not set. "
            "Tenant middleware won't resolve a tenant, so RLS will block most queries.\n"
            "  Set it to the subdomain of an existing tenant, e.g.:\n"
            "    set LOAD_TEST_SUBDOMAIN=mystore\n",
            file=sys.stderr,
        )

    if args.profile == "ui":
        sys.exit(run_ui(args.host, args.port))
    else:
        sys.exit(run_profile(args.profile, args.host))


if __name__ == "__main__":
    main()
