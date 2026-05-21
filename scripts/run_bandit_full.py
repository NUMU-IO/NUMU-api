"""Bandit security scanner runner with full plugin coverage.

Runs Bandit against src/ with all plugins enabled and generates
a structured report. Fails CI if any HIGH or MEDIUM findings exist.

Usage:
    python scripts/run_bandit_full.py
    python scripts/run_bandit_full.py --format json
    python scripts/run_bandit_full.py --output reports/bandit.txt
"""

import argparse
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Bandit deep security scan")
    parser.add_argument(
        "--format",
        choices=["txt", "json", "csv", "html"],
        default="txt",
        help="Output format (default: txt)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--severity",
        default="low",
        choices=["low", "medium", "high"],
        help="Minimum severity to report (default: low)",
    )
    args = parser.parse_args()

    severity_flag = {
        "low": "-l",
        "medium": "-ll",
        "high": "-lll",
    }[args.severity]

    cmd = [
        sys.executable,
        "-m",
        "bandit",
        "-r",
        "src/",
        severity_flag,  # severity filter
        "-ii",  # high confidence only for noise reduction
        "-f",
        args.format,
        "--exclude",
        "tests,alembic/versions",
    ]

    if args.output:
        cmd.extend(["-o", args.output])

    print(f"Running: {' '.join(cmd)}")
    print("=" * 70)

    result = subprocess.run(cmd, capture_output=False)

    # Bandit exits 1 if findings exist, 0 if clean
    if result.returncode == 1:
        print("\n" + "=" * 70)
        print("BANDIT: Findings detected. Review output above.")
        print(
            "If findings are false positives, document justification in "
            "docs/security/dependency_audit.md"
        )
    elif result.returncode == 0:
        print("\n" + "=" * 70)
        print("BANDIT: No findings. Scan clean.")
    else:
        print(f"\nBANDIT: Unexpected exit code {result.returncode}")

    # For CI: fail on HIGH/MEDIUM findings only
    # Re-run with -ll (medium+) to check
    ci_check = subprocess.run(
        [
            sys.executable,
            "-m",
            "bandit",
            "-r",
            "src/",
            "-ll",  # medium and above
            "-ii",  # high confidence
            "-f",
            "txt",
            "--exclude",
            "tests,alembic/versions",
        ],
        capture_output=True,
        text=True,
    )

    if "No issues identified" in ci_check.stdout or ci_check.returncode == 0:
        print("\nCI CHECK: No HIGH/MEDIUM findings. PASS.")
        sys.exit(0)
    else:
        print("\nCI CHECK: HIGH/MEDIUM findings detected. FAIL.")
        print(ci_check.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()
