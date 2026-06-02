from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .vault_index import _frontmatter_scalar, _markdown_files, _split_frontmatter


CLAIM_LINE_RE = re.compile(r"(?m)^-\s+(?:\*\*원본:\*\*\s*)?(?P<claim_id>E\d{3,})\s+\((?P<confidence>[^,]+),\s*(?P<category>[^)]+)\):\s*(?P<claim>.+?)\s*$")
CITABLE_LINE_RE = re.compile(r"(?m)^-\s+(?P<claim_id>E\d{3,}):\s*(?P<evidence>.+?)\s*$")
LEDGER_ROW_RE = re.compile(r"^\|\s*(?P<claim_id>E\d{3,})\s*\|")


@dataclass(frozen=True)
class SourceClaimSnapshot:
    claim_id: str
    claim: str
    evidence: str
    confidence: str
    category: str


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    relative_path: str
    topic: str
    source_id: str
    source_type: str
    title: str
    source_url: str
    canonical_url: str
    checked_at: str
    claims: list[SourceClaimSnapshot] = field(default_factory=list)

    @property
    def preferred_url(self) -> str:
        return self.source_url or self.canonical_url


@dataclass(frozen=True)
class SourceReferenceReplacement:
    path: Path
    relative_path: str
    change_type: str
    detail: str


@dataclass(frozen=True)
class SourceReferenceSyncResult:
    dry_run: bool
    vault_path: Path
    source_notes: int
    evidence_ledgers: int
    service_blueprints: int
    updated_paths: list[Path]
    replacements: list[SourceReferenceReplacement]


def sync_source_references(settings: Settings, *, dry_run: bool = True) -> SourceReferenceSyncResult:
    return _sync_source_references(settings.obsidian.vault_path, dry_run=dry_run)


def preview_source_reference_sync(vault_path: Path) -> SourceReferenceSyncResult:
    return _sync_source_references(vault_path, dry_run=True)


def _sync_source_references(vault_path: Path, *, dry_run: bool) -> SourceReferenceSyncResult:
    vault = vault_path.expanduser().resolve()
    sources = _source_snapshots(vault)
    sources_by_topic = _sources_by_topic(sources)
    claim_to_source = _claim_to_source(sources)

    evidence_ledgers = 0
    service_blueprints = 0
    updated_paths: list[Path] = []
    replacements: list[SourceReferenceReplacement] = []

    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        note_type = _frontmatter_scalar(frontmatter, "type")
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue

        if note_type == "evidence-ledger":
            evidence_ledgers += 1
            updated, path_replacements = _sync_evidence_ledger(path, text, vault, claim_to_source)
        elif note_type == "service-blueprint":
            service_blueprints += 1
            topic = _frontmatter_scalar(frontmatter, "topic")
            updated, path_replacements = _sync_service_blueprint(path, text, vault, sources_by_topic.get(topic, []))
        else:
            continue

        if path_replacements:
            replacements.extend(path_replacements)
            updated_paths.append(path)
            if not dry_run:
                path.write_text(updated, encoding="utf-8")

    return SourceReferenceSyncResult(
        dry_run=dry_run,
        vault_path=vault,
        source_notes=len(sources),
        evidence_ledgers=evidence_ledgers,
        service_blueprints=service_blueprints,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        replacements=replacements,
    )


def render_source_reference_sync_result(result: SourceReferenceSyncResult, *, max_replacements: int = 50) -> str:
    title = "Source Reference Sync Dry Run" if result.dry_run else "Source Reference Sync"
    action = "Would update notes" if result.dry_run else "Updated notes"
    shown = result.replacements[:max_replacements]
    hidden = len(result.replacements) - len(shown)
    return f"""{title}

Vault: {result.vault_path}
Source notes scanned: {result.source_notes}
Evidence ledgers scanned: {result.evidence_ledgers}
Service blueprints scanned: {result.service_blueprints}
{action}: {len(result.updated_paths)}
Replacements: {len(result.replacements)}

Updated notes:
{_path_lines(result.updated_paths)}

Replacements:
{_replacement_lines(shown)}
{_hidden_line(hidden, max_replacements)}
"""


def _source_snapshots(vault: Path) -> list[SourceSnapshot]:
    snapshots: list[SourceSnapshot] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "source-note":
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        source_id = _frontmatter_scalar(frontmatter, "source_id")
        source_url = _frontmatter_scalar(frontmatter, "source_url")
        canonical_url = _frontmatter_scalar(frontmatter, "canonical_url")
        if not source_id or not (source_url or canonical_url):
            continue
        snapshots.append(
            SourceSnapshot(
                path=path,
                relative_path=path.relative_to(vault).as_posix(),
                topic=_frontmatter_scalar(frontmatter, "topic"),
                source_id=source_id,
                source_type=_frontmatter_scalar(frontmatter, "source_type"),
                title=_frontmatter_scalar(frontmatter, "title") or _heading_title(body) or path.stem,
                source_url=source_url,
                canonical_url=canonical_url,
                checked_at=_frontmatter_scalar(frontmatter, "checked_at"),
                claims=_source_claims(body, source_id=source_id),
            )
        )
    return sorted(snapshots, key=lambda item: item.relative_path)


def _source_claims(body: str, *, source_id: str) -> list[SourceClaimSnapshot]:
    evidence_by_id = {match.group("claim_id"): match.group("evidence").strip() for match in CITABLE_LINE_RE.finditer(body)}
    claims = [
        SourceClaimSnapshot(
            claim_id=match.group("claim_id"),
            claim=match.group("claim").strip(),
            evidence=evidence_by_id.get(match.group("claim_id"), match.group("claim").strip()),
            confidence=match.group("confidence").strip(),
            category=match.group("category").strip(),
        )
        for match in CLAIM_LINE_RE.finditer(body)
    ]
    if claims:
        return claims
    fallback_id = _claim_id_from_source_id(source_id)
    return [SourceClaimSnapshot(claim_id=fallback_id, claim="", evidence="", confidence="medium", category="")] if fallback_id else []


def _claim_id_from_source_id(source_id: str) -> str:
    match = re.search(r"(\d{3,})", source_id)
    return f"E{match.group(1)}" if match else ""


def _sources_by_topic(sources: list[SourceSnapshot]) -> dict[str, list[SourceSnapshot]]:
    grouped: dict[str, list[SourceSnapshot]] = {}
    for source in sources:
        grouped.setdefault(source.topic, []).append(source)
    return grouped


def _claim_to_source(sources: list[SourceSnapshot]) -> dict[str, tuple[SourceSnapshot, SourceClaimSnapshot]]:
    mapping: dict[str, tuple[SourceSnapshot, SourceClaimSnapshot]] = {}
    for source in sources:
        for claim in source.claims:
            if claim.claim_id and source.preferred_url:
                mapping[claim.claim_id] = (source, claim)
    return mapping


def _sync_evidence_ledger(
    path: Path,
    text: str,
    vault: Path,
    claim_to_source: dict[str, tuple[SourceSnapshot, SourceClaimSnapshot]],
) -> tuple[str, list[SourceReferenceReplacement]]:
    replacements: list[SourceReferenceReplacement] = []
    lines = text.splitlines()
    updated_lines: list[str] = []
    relative_path = path.relative_to(vault).as_posix()
    for line in lines:
        match = LEDGER_ROW_RE.match(line)
        if not match:
            updated_lines.append(line)
            continue
        claim_id = match.group("claim_id")
        source_pair = claim_to_source.get(claim_id)
        if not source_pair:
            updated_lines.append(line)
            continue
        source, claim = source_pair
        new_line = _ledger_row(source, claim)
        if new_line != line:
            replacements.append(
                SourceReferenceReplacement(
                    path=path,
                    relative_path=relative_path,
                    change_type="evidence-ledger",
                    detail=f"{claim_id}: sync source URL/title/claim from {source.relative_path}",
                )
            )
        updated_lines.append(new_line)
    return "\n".join(updated_lines).rstrip() + "\n", replacements


def _ledger_row(source: SourceSnapshot, claim: SourceClaimSnapshot) -> str:
    checked_at = source.checked_at or "Not captured."
    confidence = claim.confidence or "medium"
    category = claim.category or source.source_type
    claim_text = claim.claim or f"Claim from {source.title}."
    evidence = claim.evidence or claim_text
    return (
        f"| {claim.claim_id} | {_table_cell(claim_text)} | {_table_cell(source.preferred_url)} | "
        f"{_table_cell(source.source_type)} | {_table_cell(checked_at)} | {_table_cell(confidence)} | "
        f"{_table_cell(category)}: {_table_cell(evidence)} |"
    )


def _sync_service_blueprint(
    path: Path,
    text: str,
    vault: Path,
    sources: list[SourceSnapshot],
) -> tuple[str, list[SourceReferenceReplacement]]:
    updated = text
    replacements: list[SourceReferenceReplacement] = []
    relative_path = path.relative_to(vault).as_posix()

    for source in sources:
        new_url = source.preferred_url
        if not new_url:
            continue
        candidate_urls = {source.source_url, source.canonical_url, _root_url(new_url)}
        candidate_urls = {url for url in candidate_urls if url}
        for old_url in sorted(candidate_urls, key=len, reverse=True):
            updated, count = _replace_markdown_links(updated, old_url=old_url, new_title=source.title, new_url=new_url)
            if count:
                replacements.append(
                    SourceReferenceReplacement(
                        path=path,
                        relative_path=relative_path,
                        change_type="service-blueprint",
                        detail=f"{source.relative_path}: {old_url} -> {new_url}",
                    )
                )
    return updated, replacements


def _replace_markdown_links(text: str, *, old_url: str, new_title: str, new_url: str) -> tuple[str, int]:
    pattern = re.compile(rf"\[([^\]]+)\]\({re.escape(old_url)}\)")
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        new_link = f"[{_escape_link_text(new_title)}]({new_url})"
        if match.group(0) == new_link:
            return match.group(0)
        count += 1
        return new_link

    return pattern.sub(replace, text), count


def _root_url(url: str) -> str:
    parsed = re.match(r"^(https?://[^/]+)", url.strip())
    return f"{parsed.group(1)}/" if parsed else ""


def _heading_title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _table_cell(value: str) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()


def _escape_link_text(value: str) -> str:
    return str(value or "").replace("[", "\\[").replace("]", "\\]")


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _replacement_lines(replacements: list[SourceReferenceReplacement]) -> str:
    if not replacements:
        return "- None."
    return "\n".join(f"- [{item.change_type}] {item.relative_path}: {item.detail}" for item in replacements)


def _hidden_line(hidden: int, max_replacements: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more replacement(s) hidden by --max-replacements={max_replacements}."
