"""Load the authored in-repo corpus (Layer A) → KnowledgeDoc list.

Parses `corpus/areas.json`, `corpus/howto/<area>/*.md`, and `corpus/playbooks/*.md`.
Frontmatter is simple flat `key: value` YAML — parsed without a YAML dependency so
the loader is robust in any environment. Bodies are chunked on H2/H3 headings.

The loader validates that every doc's `area` exists in the taxonomy and that
required frontmatter is present, so a malformed file fails fast rather than
silently corrupting coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.core.agent.knowledge import (
    ArticleStatus,
    KnowledgeArea,
    KnowledgeDoc,
    SourceKind,
)

# corpus dir lives in infrastructure (the content), the loader in application.
_CORPUS_DIR = (
    Path(__file__).resolve().parents[3]
    / "infrastructure"
    / "agent"
    / "knowledge"
    / "corpus"
)


def corpus_dir() -> Path:
    return _CORPUS_DIR


def load_areas(corpus: Path | None = None) -> list[KnowledgeArea]:
    base = corpus or _CORPUS_DIR
    data = json.loads((base / "areas.json").read_text(encoding="utf-8"))
    return [
        KnowledgeArea(
            key=a["key"],
            label_en=a.get("label_en", a["key"]),
            label_ar=a.get("label_ar", a["key"]),
            description=a.get("description", ""),
        )
        for a in data.get("areas", [])
    ]


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Frontmatter is a leading `---` block."""
    if not raw.lstrip().startswith("---"):
        return {}, raw
    text = raw.lstrip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    fm_block, body = parts[1], parts[2]
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip().strip('"').strip("'")
    return fm, body.lstrip("\n")


def _chunk_body(title: str, body: str) -> list[str]:
    """Chunk markdown on H2/H3 headings; prepend the title for retrieval context."""
    lines = body.splitlines()
    chunks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            chunks.append(f"{title}\n\n{text}")

    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            flush()
            current = [line]
        else:
            current.append(line)
    flush()
    if not chunks:
        body_text = body.strip()
        chunks = [f"{title}\n\n{body_text}" if body_text else title]
    return chunks


def _doc_from_file(path: Path, *, default_kind: SourceKind) -> KnowledgeDoc:
    fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    required = ["source", "title", "area", "locale"]
    missing = [k for k in required if not fm.get(k)]
    if missing:
        raise ValueError(f"{path.name}: missing frontmatter {missing}")
    kind = SourceKind(fm["source_kind"]) if fm.get("source_kind") else default_kind
    status = ArticleStatus(fm.get("status", "published"))
    return KnowledgeDoc(
        source=fm["source"],
        title=fm["title"],
        area=fm["area"],
        locale=fm["locale"],
        chunks=_chunk_body(fm["title"], body),
        section=fm.get("section"),
        source_kind=kind,
        status=status,
        signal=fm.get("signal"),
        feature=fm.get("feature"),
        detected_by=fm.get("detected_by"),
        howto=fm.get("howto"),
    )


def load_howto(corpus: Path | None = None) -> list[KnowledgeDoc]:
    base = (corpus or _CORPUS_DIR) / "howto"
    if not base.exists():
        return []
    return [
        _doc_from_file(p, default_kind=SourceKind.AUTHORED)
        for p in sorted(base.rglob("*.md"))
    ]


def load_playbooks(corpus: Path | None = None) -> list[KnowledgeDoc]:
    base = (corpus or _CORPUS_DIR) / "playbooks"
    if not base.exists():
        return []
    return [
        _doc_from_file(p, default_kind=SourceKind.PLAYBOOK)
        for p in sorted(base.rglob("*.md"))
    ]


def load_authored_corpus(corpus: Path | None = None) -> list[KnowledgeDoc]:
    """All authored Layer-A docs (how-to + playbooks), validated against the taxonomy."""
    areas = {a.key for a in load_areas(corpus)}
    docs = load_howto(corpus) + load_playbooks(corpus)
    for d in docs:
        if d.area not in areas:
            raise ValueError(f"{d.source}: area '{d.area}' is not in areas.json")
        if d.source_kind == SourceKind.PLAYBOOK and not all([
            d.signal,
            d.feature,
            d.detected_by,
            d.howto,
        ]):
            raise ValueError(
                f"{d.source}: playbook missing signal/feature/detected_by/howto"
            )
    return docs
