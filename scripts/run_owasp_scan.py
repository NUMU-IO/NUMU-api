"""OWASP ZAP automated scan runner.

Runs OWASP ZAP against the staging API and generates a Markdown report.
Requires ZAP to be running in daemon mode or as a Docker container.

Usage:
    # Start ZAP daemon first:
    docker run -d --name zap -p 8080:8080 ghcr.io/zaproxy/zaproxy:stable \
        zap.sh -daemon -host 0.0.0.0 -port 8080 \
        -config api.disablekey=true

    # Then run this script:
    python scripts/run_owasp_scan.py --target https://staging-api.numu.com

    # Or with custom ZAP host:
    python scripts/run_owasp_scan.py \
        --target https://staging-api.numu.com \
        --zap-host http://localhost:8080
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_ZAP_HOST = "http://localhost:8080"
REPORT_PATH = Path("docs/security/owasp_scan_report.md")

# Paths to actively scan (API surface)
API_PATHS = [
    "/",
    "/api/v1/public/health",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/storefront/store-by-subdomain/test",
]


def wait_for_zap(zap_host: str, timeout: int = 60) -> None:
    """Wait for ZAP to be ready."""
    logger.info("Waiting for ZAP to be ready at %s ...", zap_host)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{zap_host}/JSON/core/view/version/", timeout=5)
            if resp.status_code == 200:
                version = resp.json().get("version", "unknown")
                logger.info("ZAP is ready (version %s)", version)
                return
        except httpx.ConnectError:
            time.sleep(2)
    logger.error("ZAP did not become ready within %ds", timeout)
    sys.exit(1)


def get_zap_version(zap_host: str) -> str:
    """Get ZAP version string."""
    try:
        resp = httpx.get(f"{zap_host}/JSON/core/view/version/", timeout=10)
        return resp.json().get("version", "unknown")
    except Exception:
        return "unknown"


def spider_target(zap_host: str, target: str) -> None:
    """Run ZAP spider against the target."""
    logger.info("Starting spider scan against %s ...", target)
    resp = httpx.get(
        f"{zap_host}/JSON/spider/action/scan/",
        params={"url": target, "maxChildren": "10", "recurse": "true"},
        timeout=30,
    )
    scan_id = resp.json().get("scan", "0")

    while True:
        status_resp = httpx.get(
            f"{zap_host}/JSON/spider/view/status/",
            params={"scanId": scan_id},
            timeout=10,
        )
        progress = status_resp.json().get("status", "100")
        if int(progress) >= 100:
            break
        logger.info("  Spider progress: %s%%", progress)
        time.sleep(3)

    logger.info("Spider scan complete.")


def active_scan(zap_host: str, target: str) -> None:
    """Run ZAP active scan against the target."""
    logger.info("Starting active scan against %s ...", target)
    resp = httpx.get(
        f"{zap_host}/JSON/ascan/action/scan/",
        params={"url": target, "recurse": "true"},
        timeout=30,
    )
    scan_id = resp.json().get("scan", "0")

    while True:
        status_resp = httpx.get(
            f"{zap_host}/JSON/ascan/view/status/",
            params={"scanId": scan_id},
            timeout=10,
        )
        progress = status_resp.json().get("status", "100")
        if int(progress) >= 100:
            break
        logger.info("  Active scan progress: %s%%", progress)
        time.sleep(5)

    logger.info("Active scan complete.")


def seed_urls(zap_host: str, target: str) -> None:
    """Seed ZAP with known API endpoints."""
    for path in API_PATHS:
        url = urljoin(target, path)
        try:
            httpx.get(
                f"{zap_host}/JSON/core/action/accessUrl/",
                params={"url": url, "followRedirects": "true"},
                timeout=15,
            )
            logger.info("  Seeded: %s", url)
        except Exception as e:
            logger.warning("  Failed to seed %s: %s", url, e)


def get_alerts(zap_host: str, target: str) -> list[dict]:
    """Get all alerts from ZAP."""
    resp = httpx.get(
        f"{zap_host}/JSON/alert/view/alerts/",
        params={"baseurl": target, "start": "0", "count": "500"},
        timeout=30,
    )
    return resp.json().get("alerts", [])


def risk_label(risk: str) -> str:
    """Convert risk code to label."""
    return {"0": "Informational", "1": "Low", "2": "Medium", "3": "High"}.get(
        str(risk), "Unknown"
    )


def generate_report(
    alerts: list[dict],
    target: str,
    zap_version: str,
    scan_profile: str,
) -> str:
    """Generate Markdown report from ZAP alerts."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Deduplicate alerts by (name, risk, url)
    seen = set()
    unique_alerts = []
    for alert in alerts:
        key = (alert.get("name"), alert.get("risk"), alert.get("url"))
        if key not in seen:
            seen.add(key)
            unique_alerts.append(alert)

    # Group by risk level
    by_risk: dict[str, list[dict]] = {"3": [], "2": [], "1": [], "0": []}
    for alert in unique_alerts:
        risk = str(alert.get("risk", "0"))
        by_risk.setdefault(risk, []).append(alert)

    counts = {k: len(v) for k, v in by_risk.items()}

    lines = [
        "# OWASP ZAP Scan Report",
        "",
        "## Scan Metadata",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Scan Date** | {now} |",
        f"| **Target URL** | `{target}` |",
        f"| **ZAP Version** | {zap_version} |",
        f"| **Scan Profile** | {scan_profile} |",
        f"| **Scanner** | OWASP ZAP (via `scripts/run_owasp_scan.py`) |",
        "",
        "## Summary",
        "",
        f"| Risk Level | Count |",
        f"|------------|-------|",
        f"| High | {counts.get('3', 0)} |",
        f"| Medium | {counts.get('2', 0)} |",
        f"| Low | {counts.get('1', 0)} |",
        f"| Informational | {counts.get('0', 0)} |",
        f"| **Total** | **{sum(counts.values())}** |",
        "",
    ]

    if not unique_alerts:
        lines.append("**No alerts found.** The scan completed with zero findings.")
        lines.append("")
    else:
        lines.append("## Findings")
        lines.append("")

        for risk_code in ["3", "2", "1", "0"]:
            alert_group = by_risk.get(risk_code, [])
            if not alert_group:
                continue

            lines.append(f"### {risk_label(risk_code)} Risk")
            lines.append("")

            for alert in alert_group:
                name = alert.get("name", "Unknown")
                desc = alert.get("description", "No description")
                solution = alert.get("solution", "No solution provided")
                url = alert.get("url", "N/A")
                cwe = alert.get("cweid", "N/A")
                wasc = alert.get("wascid", "N/A")

                lines.append(f"#### {name}")
                lines.append("")
                lines.append(f"- **URL:** `{url}`")
                lines.append(f"- **CWE:** {cwe}")
                lines.append(f"- **WASC:** {wasc}")
                lines.append(f"- **Description:** {desc}")
                lines.append(f"- **Solution:** {solution}")
                lines.append("")

    lines.append("## Remediation Status")
    lines.append("")
    lines.append(
        "See commit history on `security/owasp-bandit-safety-hardening` branch "
        "for fixes applied to HIGH and MEDIUM findings."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OWASP ZAP scan against API")
    parser.add_argument(
        "--target",
        required=True,
        help="Target URL (e.g. https://staging-api.numu.com)",
    )
    parser.add_argument(
        "--zap-host",
        default=DEFAULT_ZAP_HOST,
        help=f"ZAP API host (default: {DEFAULT_ZAP_HOST})",
    )
    parser.add_argument(
        "--scan-profile",
        default="Baseline + Active",
        help="Scan profile name for report metadata",
    )
    parser.add_argument(
        "--spider-only",
        action="store_true",
        help="Run spider only (no active scan)",
    )
    parser.add_argument(
        "--output",
        default=str(REPORT_PATH),
        help=f"Output report path (default: {REPORT_PATH})",
    )
    args = parser.parse_args()

    # Connect to ZAP
    wait_for_zap(args.zap_host)
    zap_version = get_zap_version(args.zap_host)

    # Seed known API endpoints
    logger.info("Seeding known API endpoints...")
    seed_urls(args.zap_host, args.target)

    # Run spider
    spider_target(args.zap_host, args.target)

    # Run active scan unless spider-only
    if not args.spider_only:
        active_scan(args.zap_host, args.target)

    # Collect alerts
    alerts = get_alerts(args.zap_host, args.target)
    logger.info("Found %d alert(s).", len(alerts))

    # Generate report
    report = generate_report(
        alerts=alerts,
        target=args.target,
        zap_version=zap_version,
        scan_profile=args.scan_profile,
    )

    # Write report
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    logger.info("Report written to %s", output_path)

    # Exit with non-zero if HIGH or MEDIUM findings
    high_medium = [a for a in alerts if str(a.get("risk", "0")) in ("2", "3")]
    if high_medium:
        logger.warning(
            "%d HIGH/MEDIUM finding(s) detected. Review report.", len(high_medium)
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
