"""Subdomains a store can never take.

Every store lives on ``<subdomain>.numueg.app``, the same parent as NUMU's
own hosts, so a store named after one of them (``trust``, ``partners``) could
impersonate the platform to merchants and shoppers. One list, used by every
path that hands out a subdomain.
"""

RESERVED_SUBDOMAINS = frozenset({
    # Platform hosts, live and planned
    "www",
    "api",
    "app",
    "admin",
    "auth",
    "merchant",
    "partners",
    "trust",
    "wa",
    "status",
    "mail",
    "email",
    "docs",
    "help",
    "support",
    "blog",
    "cdn",
    "static",
    "assets",
    "dashboard",
    # Infrastructure
    "ftp",
    "ssh",
    "sftp",
    "cpanel",
    "webmail",
    "ns1",
    "ns2",
    "localhost",
    # Environments
    "test",
    "staging",
    "dev",
    "demo",
    "beta",
    "alpha",
    # Commerce words that read as the platform's own
    "shop",
    "store",
    "checkout",
    "pay",
    "payment",
    "billing",
    # Brand
    "numu",
    "numo",
    "numa",
    "numueg",
})

# Per-environment control-plane hosts: api-test, merchant-staging, admin-test, …
RESERVED_PREFIXES = ("api-", "merchant-", "admin-", "partners-")


def is_reserved_subdomain(subdomain: str) -> bool:
    s = subdomain.lower().strip()
    return s in RESERVED_SUBDOMAINS or s.startswith(RESERVED_PREFIXES)
