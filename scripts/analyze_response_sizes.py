#!/usr/bin/env python3
"""API Response Size Analyzer for 3G Network Optimization.

This script analyzes API endpoint response sizes to identify:
- Large responses that need optimization
- Endpoints missing pagination
- Opportunities for sparse fieldsets
- Compression effectiveness

Usage:
    python scripts/analyze_response_sizes.py --base-url http://localhost:8000
    python scripts/analyze_response_sizes.py --output report.json
    python scripts/analyze_response_sizes.py --format markdown

3G Optimization Targets:
- Target response size: <10KB for list endpoints
- Maximum acceptable: 50KB with compression
- Pagination required for lists >20 items
"""

import argparse
import gzip
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# 3G optimization thresholds (in bytes)
THRESHOLDS = {
    "excellent": 10 * 1024,  # 10KB - ideal for 3G
    "acceptable": 50 * 1024,  # 50KB - acceptable with compression
    "warning": 100 * 1024,  # 100KB - needs optimization
    "critical": 500 * 1024,  # 500KB - critical, will cause issues on 3G
}

# Endpoints that typically need pagination
LIST_ENDPOINT_PATTERNS = [
    "/products",
    "/orders",
    "/customers",
    "/categories",
    "/addresses",
    "/coupons",
]


@dataclass
class EndpointAnalysis:
    """Analysis result for a single endpoint."""

    path: str
    method: str
    status_code: int
    response_size_bytes: int
    compressed_size_bytes: int | None
    compression_ratio: float | None
    response_time_ms: float
    item_count: int | None = None
    has_pagination: bool = False
    severity: str = "unknown"
    recommendations: list[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """Complete analysis report."""

    generated_at: str
    base_url: str
    total_endpoints: int
    endpoints_analyzed: int
    critical_count: int
    warning_count: int
    results: list[EndpointAnalysis]
    summary: dict[str, Any] = field(default_factory=dict)


def calculate_severity(size_bytes: int) -> str:
    """Determine severity level based on response size."""
    if size_bytes <= THRESHOLDS["excellent"]:
        return "excellent"
    elif size_bytes <= THRESHOLDS["acceptable"]:
        return "acceptable"
    elif size_bytes <= THRESHOLDS["warning"]:
        return "warning"
    else:
        return "critical"


def get_compression_size(content: bytes) -> int:
    """Calculate gzip compressed size."""
    return len(gzip.compress(content))


def count_items(data: Any) -> int | None:
    """Try to count items in a list response."""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        # Common pagination response patterns
        for key in ["items", "data", "results", "products", "orders", "customers"]:
            if key in data and isinstance(data[key], list):
                return len(data[key])
    return None


def check_pagination(data: Any, path: str) -> bool:
    """Check if response appears to be paginated."""
    if not isinstance(data, dict):
        return False

    # Look for common pagination fields
    pagination_fields = [
        "page",
        "page_size",
        "total",
        "total_pages",
        "next_cursor",
        "next",
        "previous",
        "cursor",
        "offset",
        "limit",
    ]

    return any(field in data for field in pagination_fields)


def generate_recommendations(
    analysis: EndpointAnalysis, is_list_endpoint: bool
) -> list[str]:
    """Generate optimization recommendations based on analysis."""
    recommendations = []

    # Size-based recommendations
    if analysis.severity == "critical":
        recommendations.append(
            f"CRITICAL: Response size ({analysis.response_size_bytes:,} bytes) "
            "is too large for 3G networks. Immediate optimization required."
        )
    elif analysis.severity == "warning":
        recommendations.append(
            f"WARNING: Response size ({analysis.response_size_bytes:,} bytes) "
            "may cause slow loading on 3G networks."
        )

    # Pagination recommendations
    if is_list_endpoint and not analysis.has_pagination:
        recommendations.append(
            "Add pagination support. List endpoints should use cursor-based pagination "
            "with a default page size of 15-20 items for 3G optimization."
        )

    if analysis.item_count and analysis.item_count > 20 and not analysis.has_pagination:
        recommendations.append(
            f"Response contains {analysis.item_count} items without pagination. "
            "Implement cursor-based pagination to improve 3G performance."
        )

    # Compression recommendations
    if analysis.compression_ratio and analysis.compression_ratio < 0.5:
        recommendations.append(
            f"Good compression ratio ({analysis.compression_ratio:.1%}). "
            "Ensure gzip/brotli compression is enabled in production."
        )
    elif analysis.compression_ratio and analysis.compression_ratio > 0.7:
        recommendations.append(
            "Response has low compressibility. Consider reducing response payload "
            "by implementing sparse fieldsets (?fields=id,name,price)."
        )

    # Sparse fieldsets recommendation for large responses
    if analysis.response_size_bytes > THRESHOLDS["acceptable"]:
        recommendations.append(
            "Implement sparse fieldsets to allow clients to request only needed fields. "
            "Example: ?fields=id,name,price,images"
        )

    return recommendations


async def analyze_endpoint(
    client: httpx.AsyncClient,
    path: str,
    method: str = "GET",
    params: dict | None = None,
) -> EndpointAnalysis | None:
    """Analyze a single endpoint's response."""
    try:
        start_time = datetime.now()

        response = await client.request(method, path, params=params, timeout=30.0)

        response_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        content = response.content
        response_size = len(content)

        # Calculate compressed size
        compressed_size = get_compression_size(content)
        compression_ratio = compressed_size / response_size if response_size > 0 else 0

        # Parse response for analysis
        item_count = None
        has_pagination = False

        if response.status_code == 200:
            try:
                data = response.json()
                item_count = count_items(data)
                has_pagination = check_pagination(data, path)
            except json.JSONDecodeError:
                pass

        # Determine severity
        severity = calculate_severity(response_size)

        # Check if this is a list endpoint
        is_list_endpoint = any(
            pattern in path.lower() for pattern in LIST_ENDPOINT_PATTERNS
        )

        analysis = EndpointAnalysis(
            path=path,
            method=method,
            status_code=response.status_code,
            response_size_bytes=response_size,
            compressed_size_bytes=compressed_size,
            compression_ratio=compression_ratio,
            response_time_ms=round(response_time_ms, 2),
            item_count=item_count,
            has_pagination=has_pagination,
            severity=severity,
        )

        # Generate recommendations
        analysis.recommendations = generate_recommendations(analysis, is_list_endpoint)

        return analysis

    except httpx.RequestError as e:
        print(f"  Error analyzing {path}: {e}", file=sys.stderr)
        return None


async def get_openapi_endpoints(
    client: httpx.AsyncClient,
) -> list[tuple[str, str]]:
    """Extract endpoints from OpenAPI specification."""
    try:
        response = await client.get("/openapi.json")
        if response.status_code != 200:
            print("Warning: Could not fetch OpenAPI spec", file=sys.stderr)
            return []

        spec = response.json()
        endpoints = []

        for path, methods in spec.get("paths", {}).items():
            for method in methods:
                if method.upper() in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
                    endpoints.append((path, method.upper()))

        return endpoints

    except Exception as e:
        print(f"Error fetching OpenAPI spec: {e}", file=sys.stderr)
        return []


# Default endpoints to analyze if OpenAPI is not available
DEFAULT_ENDPOINTS = [
    ("/api/v1/public/health", "GET"),
    ("/api/v1/public/ping", "GET"),
    # Storefront endpoints (most critical for 3G)
    ("/api/v1/storefront/store/1/products", "GET"),
    ("/api/v1/storefront/store/1/products?limit=20", "GET"),
    ("/api/v1/storefront/store/1/products?limit=50", "GET"),
    ("/api/v1/storefront/store/1/categories", "GET"),
]


async def run_analysis(
    base_url: str,
    auth_token: str | None = None,
    endpoints: list[tuple[str, str]] | None = None,
) -> AnalysisReport:
    """Run complete response size analysis."""
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(base_url=base_url, headers=headers) as client:
        # Get endpoints from OpenAPI or use defaults
        if endpoints is None:
            endpoints = await get_openapi_endpoints(client)
            if not endpoints:
                print("Using default endpoint list", file=sys.stderr)
                endpoints = DEFAULT_ENDPOINTS

        print(f"Analyzing {len(endpoints)} endpoints...")

        results = []
        critical_count = 0
        warning_count = 0

        for i, (path, method) in enumerate(endpoints):
            # Skip non-GET endpoints for now (they require request bodies)
            if method != "GET":
                continue

            # Skip paths with path parameters that we can't fill
            if "{" in path and path not in [
                p for p, _ in DEFAULT_ENDPOINTS if "{" in p
            ]:
                continue

            print(f"  [{i + 1}/{len(endpoints)}] {method} {path}...")

            analysis = await analyze_endpoint(client, path, method)
            if analysis:
                results.append(analysis)
                if analysis.severity == "critical":
                    critical_count += 1
                elif analysis.severity == "warning":
                    warning_count += 1

        # Generate summary
        total_size = sum(r.response_size_bytes for r in results)
        avg_size = total_size / len(results) if results else 0
        avg_response_time = (
            sum(r.response_time_ms for r in results) / len(results) if results else 0
        )

        summary = {
            "total_response_size_bytes": total_size,
            "average_response_size_bytes": round(avg_size),
            "average_response_time_ms": round(avg_response_time, 2),
            "endpoints_needing_pagination": sum(
                1
                for r in results
                if not r.has_pagination and r.item_count and r.item_count > 20
            ),
            "severity_distribution": {
                "excellent": sum(1 for r in results if r.severity == "excellent"),
                "acceptable": sum(1 for r in results if r.severity == "acceptable"),
                "warning": sum(1 for r in results if r.severity == "warning"),
                "critical": sum(1 for r in results if r.severity == "critical"),
            },
        }

        return AnalysisReport(
            generated_at=datetime.now().isoformat(),
            base_url=base_url,
            total_endpoints=len(endpoints),
            endpoints_analyzed=len(results),
            critical_count=critical_count,
            warning_count=warning_count,
            results=results,
            summary=summary,
        )


def format_markdown(report: AnalysisReport) -> str:
    """Format report as Markdown."""
    lines = [
        "# API Response Size Analysis Report",
        "",
        f"**Generated:** {report.generated_at}",
        f"**Base URL:** {report.base_url}",
        "",
        "## Summary",
        "",
        f"- **Total Endpoints:** {report.total_endpoints}",
        f"- **Endpoints Analyzed:** {report.endpoints_analyzed}",
        f"- **Critical Issues:** {report.critical_count}",
        f"- **Warnings:** {report.warning_count}",
        "",
        "### Severity Distribution",
        "",
    ]

    dist = report.summary.get("severity_distribution", {})
    for severity, count in dist.items():
        emoji = {
            "excellent": "✅",
            "acceptable": "👍",
            "warning": "⚠️",
            "critical": "🚨",
        }.get(severity, "❓")
        lines.append(f"- {emoji} **{severity.capitalize()}:** {count}")

    lines.extend([
        "",
        "## Detailed Results",
        "",
    ])

    # Sort by severity (critical first)
    severity_order = {"critical": 0, "warning": 1, "acceptable": 2, "excellent": 3}
    sorted_results = sorted(
        report.results, key=lambda r: severity_order.get(r.severity, 4)
    )

    for result in sorted_results:
        emoji = {
            "excellent": "✅",
            "acceptable": "👍",
            "warning": "⚠️",
            "critical": "🚨",
        }.get(result.severity, "❓")

        lines.extend([
            f"### {emoji} `{result.method} {result.path}`",
            "",
            f"- **Status:** {result.status_code}",
            f"- **Size:** {result.response_size_bytes:,} bytes "
            f"({result.response_size_bytes / 1024:.1f} KB)",
            f"- **Compressed:** {result.compressed_size_bytes:,} bytes "
            f"(ratio: {result.compression_ratio:.1%})"
            if result.compressed_size_bytes
            else "",
            f"- **Response Time:** {result.response_time_ms:.2f}ms",
            f"- **Items Count:** {result.item_count}" if result.item_count else "",
            f"- **Has Pagination:** {'Yes' if result.has_pagination else 'No'}",
            "",
        ])

        if result.recommendations:
            lines.append("**Recommendations:**")
            for rec in result.recommendations:
                lines.append(f"- {rec}")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## 3G Optimization Targets",
        "",
        "| Severity | Threshold |",
        "|----------|-----------|",
        "| Excellent | < 10 KB |",
        "| Acceptable | < 50 KB |",
        "| Warning | < 100 KB |",
        "| Critical | > 100 KB |",
        "",
    ])

    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze API response sizes for 3G optimization"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the API (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--token",
        help="Bearer token for authentication",
    )

    args = parser.parse_args()

    import asyncio

    report = asyncio.run(run_analysis(args.base_url, args.token))

    if args.format == "json":
        output = json.dumps(
            {
                "generated_at": report.generated_at,
                "base_url": report.base_url,
                "total_endpoints": report.total_endpoints,
                "endpoints_analyzed": report.endpoints_analyzed,
                "critical_count": report.critical_count,
                "warning_count": report.warning_count,
                "summary": report.summary,
                "results": [asdict(r) for r in report.results],
            },
            indent=2,
        )
    else:
        output = format_markdown(report)

    if args.output:
        args.output.write_text(output)
        print(f"Report saved to {args.output}")
    else:
        print(output)

    # Exit with non-zero if critical issues found
    if report.critical_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
