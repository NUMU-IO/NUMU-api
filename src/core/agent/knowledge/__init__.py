"""Knowledge-base domain types (spec 002) — no infrastructure imports.

These describe the shape of the shared corpus and the coverage report independent
of storage. Layer A holds NO merchant data; Layer B is tenant-scoped (RLS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ArticleStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class SourceKind(StrEnum):
    AUTHORED = "authored"  # in-repo authored how-to (Layer A)
    DOCS = "docs"  # ingested from docs.numueg.app (Layer A)
    PLAYBOOK = "playbook"  # growth playbook (Layer A)
    CATALOG = "catalog"  # auto-derived from the store catalog (Layer B)
    POLICY = "policy"  # auto-derived from store policies (Layer B)
    NOTE = "note"  # merchant-authored note/FAQ (Layer B)


@dataclass(frozen=True)
class KnowledgeArea:
    key: str
    label_en: str
    label_ar: str
    description: str = ""


@dataclass
class KnowledgeDoc:
    """A parsed shared-corpus document ready to embed + upsert."""

    source: str
    title: str
    area: str
    locale: str
    chunks: list[str]
    section: str | None = None
    source_kind: SourceKind = SourceKind.AUTHORED
    status: ArticleStatus = ArticleStatus.PUBLISHED
    # playbook-only fields (present when source_kind == PLAYBOOK)
    signal: str | None = None
    feature: str | None = None
    detected_by: str | None = None
    howto: str | None = None


@dataclass
class GrowthPlaybook:
    """A store-signal → NUMU-feature recommendation, grounded in an authored doc."""

    source: str
    title: str
    signal: str
    feature: str
    detected_by: str
    howto: str
    locale: str = "en"


@dataclass
class CoverageAreaRow:
    area: str
    published_count: int
    newest_updated_at: datetime | None
    is_gap: bool
    is_stale: bool


@dataclass
class CoverageReport:
    generated_at: datetime | None
    staleness_days: int
    areas: list[CoverageAreaRow] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        total = len(self.areas)
        with_content = sum(1 for a in self.areas if a.published_count > 0)
        return {
            "areas_total": total,
            "areas_with_gap": sum(1 for a in self.areas if a.is_gap),
            "areas_stale": sum(1 for a in self.areas if a.is_stale),
            "coverage_pct": round(100.0 * with_content / total, 1) if total else 0.0,
        }
