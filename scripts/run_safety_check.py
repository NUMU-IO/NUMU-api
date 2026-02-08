"""Safety dependency vulnerability scanner.

Checks installed packages against the Safety vulnerability database
and generates a report. Fails CI if vulnerabilities are found.

Usage:
    python scripts/run_safety_check.py
    python scripts/run_safety_check.py --output reports/safety.txt
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Safety dependency check")
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "bare"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    # Generate frozen requirements from current environment
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as tmp:
        freeze_result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
        )
        tmp.write(freeze_result.stdout)
        tmp_path = tmp.name

    print(f"Generated requirements freeze: {tmp_path}")
    print(f"Packages found: {len(freeze_result.stdout.strip().splitlines())}")
    print("=" * 70)

    cmd = [
        sys.executable,
        "-m",
        "safety",
        "check",
        "-r",
        tmp_path,
        "--output",
        args.format,
    ]

    print(f"Running: {' '.join(cmd)}")
    print("=" * 70)

    result = subprocess.run(cmd, capture_output=True, text=True)

    output = result.stdout + result.stderr
    # Filter out the deprecation warning noise
    lines = []
    in_deprecation = False
    for line in output.splitlines():
        if "DEPRECATED" in line:
            in_deprecation = True
            continue
        if in_deprecation and line.strip().startswith("+==="):
            in_deprecation = False
            continue
        if not in_deprecation:
            lines.append(line)

    clean_output = "\n".join(lines).strip()
    print(clean_output)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(clean_output, encoding="utf-8")
        print(f"\nReport written to {args.output}")

    # Clean up temp file
    Path(tmp_path).unlink(missing_ok=True)

    # Exit code: safety returns 64 for vulnerabilities found
    if result.returncode != 0:
        print("\n" + "=" * 70)
        print("SAFETY: Vulnerabilities detected. Review output above.")
        print(
            "Document accepted risks in docs/security/dependency_audit.md "
            "with rationale."
        )
        sys.exit(1)
    else:
        print("\n" + "=" * 70)
        print("SAFETY: No known vulnerabilities. PASS.")
        sys.exit(0)


if __name__ == "__main__":
    main()
