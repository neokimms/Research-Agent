from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .citations import normalize_source_record, source_identity_key
from .collectors import collect_paper_sources
from .config import Settings
from .evidence import fallback_evidence
from .models import SourceRecord
from .obsidian import ObsidianWriter
from .render import render_source_note
from .textutil import slugify, yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


PAPER_CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>P\d{3})\b", re.MULTILINE)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)


@dataclass(frozen=True)
class PaperRefreshCandidate:
    proposal_id: str
    topic: str
    record: SourceRecord


@dataclass(frozen=True)
class PaperRefreshResult:
    vault_path: Path
    topics: list[str]
    candidates: list[PaperRefreshCandidate]
    warnings: list[str]


@dataclass(frozen=True)
class PaperRefreshWriteResult:
    result: PaperRefreshResult
    note_path: Path


@dataclass(frozen=True)
class PaperRefreshApplyItem:
    proposal_path: Path
    proposal_id: str
    topic: str
    record: SourceRecord
    source_id: str
    note_path: Path


@dataclass(frozen=True)
class PaperRefreshSkippedItem:
    proposal_path: Path
    proposal_id: str
    title: str
    reason: str


@dataclass(frozen=True)
class PaperRefreshApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[PaperRefreshApplyItem]
    created_paths: list[Path]
    skipped_items: list[PaperRefreshSkippedItem]


def build_paper_refresh(
    settings: Settings,
    *,
    topic: str = "",
    limit_each: int = 3,
    max_proposals: int = 20,
) -> PaperRefreshResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    topics = [topic.strip()] if topic.strip() else _vault_topics(vault)
    warnings: list[str] = []
    if not topics:
        return PaperRefreshResult(vault_path=vault, topics=[], candidates=[], warnings=["No generated topics found in the vault. Provide a topic."])

    existing_keys = _existing_paper_keys(vault)
    seen_keys = set(existing_keys)
    candidates: list[PaperRefreshCandidate] = []
    for topic_value in topics:
        collector_warnings = []
        records = collect_paper_sources(
            topic_value,
            settings.sources.paper_sources,
            limit_each=limit_each,
            warnings=collector_warnings,
        )
        warnings.extend(f"{warning.source}: {warning.detail}" for warning in collector_warnings)
        for record in records:
            normalized = normalize_source_record(record)
            key = source_identity_key(normalized)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            proposal_id = f"P{len(candidates) + 1:03d}"
            candidates.append(PaperRefreshCandidate(proposal_id=proposal_id, topic=topic_value, record=normalized))
            if len(candidates) >= max_proposals:
                break
        if len(candidates) >= max_proposals:
            break

    return PaperRefreshResult(vault_path=vault, topics=topics, candidates=candidates, warnings=warnings)


def render_paper_refresh(result: PaperRefreshResult, *, max_proposals: int = 50) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""Paper Refresh

Vault: {result.vault_path}
Topics: {len(result.topics)}
Paper candidates: {len(result.candidates)}
Warnings: {len(result.warnings)}

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden)}

Warnings:
{_warning_lines(result.warnings)}
"""


def write_paper_refresh_note(
    settings: Settings,
    *,
    topic: str = "",
    limit_each: int = 3,
    max_proposals: int = 20,
) -> PaperRefreshWriteResult:
    result = build_paper_refresh(settings, topic=topic, limit_each=limit_each, max_proposals=max_proposals)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_paper-refresh.md",
        render_paper_refresh_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return PaperRefreshWriteResult(result=result, note_path=path)


def apply_paper_refresh(
    settings: Settings,
    *,
    dry_run: bool = True,
    applied_at: str = "",
) -> PaperRefreshApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    existing_keys = _existing_paper_keys(vault)
    seen_keys = set(existing_keys)
    next_index = _next_source_index(vault)
    proposal_notes = 0
    approved_items: list[PaperRefreshApplyItem] = []
    created_paths: list[Path] = []
    skipped_items: list[PaperRefreshSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    if not dry_run:
        writer.ensure_structure()

    for proposal_path in _paper_refresh_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text)
        checked_ids = _checked_proposal_ids(text)
        for proposal_id in checked_ids:
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(PaperRefreshSkippedItem(proposal_path, proposal_id, "", "candidate record not found"))
                continue
            key = source_identity_key(candidate.record)
            if key and key in seen_keys:
                skipped_items.append(PaperRefreshSkippedItem(proposal_path, proposal_id, candidate.record.title, "paper source already exists"))
                continue
            seen_keys.add(key)
            source_id = f"S{next_index:03d}"
            note_path = _paper_note_path(settings, candidate, source_id=source_id)
            item = PaperRefreshApplyItem(
                proposal_path=proposal_path,
                proposal_id=proposal_id,
                topic=candidate.topic,
                record=candidate.record,
                source_id=source_id,
                note_path=note_path,
            )
            approved_items.append(item)
            created_paths.append(note_path)
            proposal_paths_to_mark.add(proposal_path)
            if not dry_run:
                writer.write_note(
                    settings.obsidian.source_dir + "/papers",
                    note_path.name,
                    _render_paper_source(candidate, source_id=source_id, checked_at=_checked_date(applied_at)),
                )
            next_index += 1

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if applied_at:
                values["applied_at"] = applied_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")

    return PaperRefreshApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        created_paths=sorted(set(created_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_paper_refresh_apply_result(result: PaperRefreshApplyResult) -> str:
    action = "Would create notes" if result.dry_run else "Created notes"
    title = "Paper Refresh Apply Dry Run" if result.dry_run else "Paper Refresh Apply"
    return f"""{title}

Proposal notes scanned: {result.proposal_notes}
Approved checklist items: {len(result.approved_items)}
{action}: {len(result.created_paths)}
Skipped items: {len(result.skipped_items)}

Approved items:
{_apply_item_lines(result.approved_items)}

Created notes:
{_path_lines(result.created_paths)}

Skipped items:
{_skipped_item_lines(result.skipped_items)}
"""


def render_paper_refresh_note(
    result: PaperRefreshResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("paper-refresh")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
topic_count: {len(result.topics)}
proposal_count: {len(result.candidates)}
warning_count: {len(result.warnings)}
---
# Paper Refresh

## Summary

| metric | value |
|---|---:|
| topics | {len(result.topics)} |
| paper candidates | {len(result.candidates)} |
| warnings | {len(result.warnings)} |

## Topics

{_topic_lines(result.topics)}

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden)}

## Warnings

{_warning_lines(result.warnings)}

## Review Checklist

- [ ] Confirm each paper is relevant and has DOI or arXiv identity.
- [ ] Apply accepted paper sources with `apply-paper-refresh`.
- [ ] Rerun `source-audit`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def _render_paper_source(candidate: PaperRefreshCandidate, *, source_id: str, checked_at: str) -> str:
    evidence = fallback_evidence(candidate.topic, [candidate.record])
    claim_index = _source_index(source_id)
    claims = []
    for claim in evidence.claims:
        claims.append(
            claim.__class__(
                claim_id=f"E{claim_index:03d}",
                source_id=source_id,
                claim=claim.claim,
                evidence=claim.evidence,
                source_title=claim.source_title,
                source_url=claim.source_url,
                source_type=claim.source_type,
                confidence=claim.confidence,
                category=claim.category,
            )
        )
    return render_source_note(candidate.record, topic=candidate.topic, checked_at=checked_at, source_id=source_id, claims=claims)


def _candidate_json(candidates: list[PaperRefreshCandidate]) -> str:
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2)


def _candidate_to_json(candidate: PaperRefreshCandidate) -> dict:
    record = candidate.record
    return {
        "proposal_id": candidate.proposal_id,
        "topic": candidate.topic,
        "title": record.title,
        "url": record.url,
        "canonical_url": record.canonical_url,
        "source_type": record.source_type,
        "summary": record.summary,
        "authors": record.authors,
        "published_at": record.published_at,
        "updated_at": record.updated_at,
        "doi": record.doi,
        "arxiv_id": record.arxiv_id,
        "source_provider": record.source_provider,
        "source_score": record.source_score,
    }


def _candidate_from_json(item: dict) -> PaperRefreshCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    topic = str(item.get("topic") or "").strip()
    if not proposal_id or not topic:
        return None
    authors = item.get("authors")
    record = normalize_source_record(
        SourceRecord(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("url") or "").strip(),
            canonical_url=str(item.get("canonical_url") or "").strip(),
            source_type="papers",
            summary=str(item.get("summary") or "").strip(),
            authors=[str(author) for author in authors] if isinstance(authors, list) else [],
            published_at=str(item.get("published_at") or "").strip(),
            updated_at=str(item.get("updated_at") or "").strip(),
            doi=str(item.get("doi") or "").strip(),
            arxiv_id=str(item.get("arxiv_id") or "").strip(),
            source_provider=str(item.get("source_provider") or "").strip(),
            source_score=float(item.get("source_score") or 0.0),
        )
    )
    if not record.title or not (record.url or record.canonical_url):
        return None
    return PaperRefreshCandidate(proposal_id=proposal_id, topic=topic, record=record)


def _candidate_records(text: str) -> dict[str, PaperRefreshCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, PaperRefreshCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _checked_proposal_ids(text: str) -> list[str]:
    return [match.group("proposal_id") for match in PAPER_CHECK_RE.finditer(text) if match.group("state").strip().lower() == "x"]


def _vault_topics(vault: Path) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        topic = _frontmatter_scalar(frontmatter, "topic")
        if topic and topic not in seen:
            seen.add(topic)
            topics.append(topic)
    return topics


def _existing_paper_keys(vault: Path) -> set[str]:
    keys: set[str] = set()
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "source-note":
            continue
        if _frontmatter_scalar(frontmatter, "source_type") != "papers":
            continue
        record = SourceRecord(
            title=_frontmatter_scalar(frontmatter, "title") or path.stem,
            url=_frontmatter_scalar(frontmatter, "source_url"),
            canonical_url=_frontmatter_scalar(frontmatter, "canonical_url"),
            source_type="papers",
            doi=_frontmatter_scalar(frontmatter, "doi"),
            arxiv_id=_frontmatter_scalar(frontmatter, "arxiv_id"),
        )
        key = source_identity_key(record)
        if key:
            keys.add(key)
    return keys


def _next_source_index(vault: Path) -> int:
    max_index = 0
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        match = re.search(r"(\d+)", _frontmatter_scalar(frontmatter, "source_id"))
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index + 1


def _paper_refresh_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "paper-refresh":
            paths.append(path)
    return sorted(paths)


def _paper_note_path(settings: Settings, candidate: PaperRefreshCandidate, *, source_id: str) -> Path:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    date_prefix = now_local(settings.app.timezone).strftime("%Y-%m-%d")
    source_index = _source_index(source_id)
    filename = f"{date_prefix}_{slugify(candidate.topic)}_paper-{source_index:02d}_{slugify(candidate.record.title, fallback='paper')}.md"
    return (vault / settings.obsidian.source_dir / "papers" / filename).resolve()


def _source_index(source_id: str) -> int:
    match = re.search(r"(\d+)", source_id)
    return int(match.group(1)) if match else 1


def _checked_date(applied_at: str) -> str:
    return (applied_at.split("T", 1)[0] if applied_at else now_local("Asia/Seoul").date().isoformat())


def _proposal_lines(candidates: list[PaperRefreshCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(_proposal_line(candidate) for candidate in candidates)


def _proposal_line(candidate: PaperRefreshCandidate) -> str:
    record = candidate.record
    score = f"{record.source_score:.2f}" if record.source_score else "not scored"
    identity = _identity_text(record)
    return (
        f"- [ ] {candidate.proposal_id} Add paper [{record.title}]({record.url or record.canonical_url}) "
        f"(provider: {record.source_provider or 'unknown'}, score: {score}{identity})"
    )


def _identity_text(record: SourceRecord) -> str:
    parts = []
    if record.doi:
        parts.append(f"doi: `{record.doi}`")
    if record.arxiv_id:
        parts.append(f"arxiv: `{record.arxiv_id}`")
    return ", " + ", ".join(parts) if parts else ""


def _topic_lines(topics: list[str]) -> str:
    if not topics:
        return "- None."
    return "\n".join(f"- {topic}" for topic in topics)


def _warning_lines(warnings: list[str]) -> str:
    if not warnings:
        return "- None."
    return "\n".join(f"- {warning}" for warning in warnings)


def _apply_item_lines(items: list[PaperRefreshApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.proposal_id} {item.source_id}: {item.record.title} -> {item.note_path}"
        for item in items
    )


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _skipped_item_lines(items: list[PaperRefreshSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.proposal_id} {item.title}: {item.reason}"
        for item in items
    )


def _hidden_line(hidden: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals."
