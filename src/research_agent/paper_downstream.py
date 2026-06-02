from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .obsidian import ObsidianWriter
from .source_reference_sync import SourceClaimSnapshot, SourceSnapshot, _source_snapshots
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


DOWNSTREAM_CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>D\d{3})\b", re.MULTILINE)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)
LEDGER_ROW_RE = re.compile(r"^\|\s*(?P<claim_id>E\d{3,})\s*\|")


@dataclass(frozen=True)
class DownstreamNoteTargets:
    evidence_ledger: Path | None
    service_blueprint: Path | None
    topic_map: Path | None


@dataclass(frozen=True)
class PaperDownstreamCandidate:
    proposal_id: str
    source: SourceSnapshot
    claim: SourceClaimSnapshot
    targets: DownstreamNoteTargets
    pending_targets: list[str]


@dataclass(frozen=True)
class PaperDownstreamResult:
    vault_path: Path
    paper_sources: int
    candidates: list[PaperDownstreamCandidate]
    warnings: list[str]


@dataclass(frozen=True)
class PaperDownstreamWriteResult:
    result: PaperDownstreamResult
    note_path: Path


@dataclass(frozen=True)
class PaperDownstreamApplyItem:
    proposal_path: Path
    proposal_id: str
    source_id: str
    claim_id: str
    title: str
    updated_targets: list[str]


@dataclass(frozen=True)
class PaperDownstreamSkippedItem:
    proposal_path: Path
    proposal_id: str
    title: str
    reason: str


@dataclass(frozen=True)
class PaperDownstreamApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[PaperDownstreamApplyItem]
    updated_paths: list[Path]
    skipped_items: list[PaperDownstreamSkippedItem]


def build_paper_downstream_proposals(settings: Settings, *, max_proposals: int = 50) -> PaperDownstreamResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    sources = [source for source in _source_snapshots(vault) if source.source_type == "papers"]
    ledgers = _notes_by_topic(vault, "evidence-ledger")
    blueprints = _notes_by_topic(vault, "service-blueprint")
    topic_maps = _notes_by_topic(vault, "topic-map")

    candidates: list[PaperDownstreamCandidate] = []
    warnings: list[str] = []
    warned_topics: set[tuple[str, str]] = set()

    for source in sources:
        targets = DownstreamNoteTargets(
            evidence_ledger=ledgers.get(source.topic),
            service_blueprint=blueprints.get(source.topic),
            topic_map=topic_maps.get(source.topic),
        )
        for note_type, path in (
            ("evidence-ledger", targets.evidence_ledger),
            ("service-blueprint", targets.service_blueprint),
            ("topic-map", targets.topic_map),
        ):
            warning_key = (source.topic, note_type)
            if path is None and warning_key not in warned_topics:
                warnings.append(f"No {note_type} note found for topic: {source.topic}")
                warned_topics.add(warning_key)

        for claim in source.claims:
            pending_targets = _pending_targets(source, claim, targets)
            if not pending_targets:
                continue
            proposal_id = f"D{len(candidates) + 1:03d}"
            candidates.append(
                PaperDownstreamCandidate(
                    proposal_id=proposal_id,
                    source=source,
                    claim=claim,
                    targets=targets,
                    pending_targets=pending_targets,
                )
            )
            if len(candidates) >= max_proposals:
                return PaperDownstreamResult(
                    vault_path=vault,
                    paper_sources=len(sources),
                    candidates=candidates,
                    warnings=warnings,
                )

    return PaperDownstreamResult(
        vault_path=vault,
        paper_sources=len(sources),
        candidates=candidates,
        warnings=warnings,
    )


def render_paper_downstream_proposals(result: PaperDownstreamResult, *, max_proposals: int = 50) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""Paper Downstream Proposals

Vault: {result.vault_path}
Paper sources scanned: {result.paper_sources}
Downstream candidates: {len(result.candidates)}
Warnings: {len(result.warnings)}

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

Warnings:
{_warning_lines(result.warnings)}
"""


def write_paper_downstream_proposals(
    settings: Settings,
    *,
    max_proposals: int = 50,
) -> PaperDownstreamWriteResult:
    result = build_paper_downstream_proposals(settings, max_proposals=max_proposals)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_paper-downstream-proposals.md",
        render_paper_downstream_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return PaperDownstreamWriteResult(result=result, note_path=path)


def apply_paper_downstream_proposals(
    settings: Settings,
    *,
    dry_run: bool = True,
    applied_at: str = "",
) -> PaperDownstreamApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[PaperDownstreamApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[PaperDownstreamSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _paper_downstream_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text, vault=vault)
        checked_ids = _checked_proposal_ids(text)
        for proposal_id in checked_ids:
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(PaperDownstreamSkippedItem(proposal_path, proposal_id, "", "candidate record not found"))
                continue
            updated, changed_paths, reason = _apply_candidate(candidate, vault=vault, dry_run=dry_run)
            if not updated:
                skipped_items.append(
                    PaperDownstreamSkippedItem(
                        proposal_path=proposal_path,
                        proposal_id=proposal_id,
                        title=candidate.source.title,
                        reason=reason or "downstream references already exist",
                    )
                )
                continue
            approved_items.append(
                PaperDownstreamApplyItem(
                    proposal_path=proposal_path,
                    proposal_id=proposal_id,
                    source_id=candidate.source.source_id,
                    claim_id=candidate.claim.claim_id,
                    title=candidate.source.title,
                    updated_targets=updated,
                )
            )
            updated_paths.extend(changed_paths)
            proposal_paths_to_mark.add(proposal_path)

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if applied_at:
                values["applied_at"] = applied_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")

    return PaperDownstreamApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_paper_downstream_apply_result(result: PaperDownstreamApplyResult) -> str:
    title = "Paper Downstream Apply Dry Run" if result.dry_run else "Paper Downstream Apply"
    action = "Would update notes" if result.dry_run else "Updated notes"
    return f"""{title}

Proposal notes scanned: {result.proposal_notes}
Approved checklist items: {len(result.approved_items)}
{action}: {len(result.updated_paths)}
Skipped items: {len(result.skipped_items)}

Approved items:
{_apply_item_lines(result.approved_items)}

Updated notes:
{_path_lines(result.updated_paths)}

Skipped items:
{_skipped_item_lines(result.skipped_items)}
"""


def render_paper_downstream_note(
    result: PaperDownstreamResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("paper-downstream-proposals")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
paper_source_count: {result.paper_sources}
proposal_count: {len(result.candidates)}
warning_count: {len(result.warnings)}
---
# Paper Downstream Proposals

## Summary

| metric | value |
|---|---:|
| paper sources scanned | {result.paper_sources} |
| downstream candidates | {len(result.candidates)} |
| warnings | {len(result.warnings)} |

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

## Warnings

{_warning_lines(result.warnings)}

## Review Checklist

- [ ] Confirm each paper claim belongs in the topic evidence ledger and blueprint.
- [ ] Apply accepted downstream updates with `apply-paper-downstream`.
- [ ] Rerun `source-audit` and `bilingual-audit`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def _pending_targets(source: SourceSnapshot, claim: SourceClaimSnapshot, targets: DownstreamNoteTargets) -> list[str]:
    pending: list[str] = []
    if targets.evidence_ledger and not _path_has_claim(targets.evidence_ledger, claim.claim_id):
        pending.append("evidence-ledger")
    if targets.service_blueprint and not _path_has_claim_or_source(targets.service_blueprint, source, claim):
        pending.append("service-blueprint")
    if targets.topic_map and not _topic_map_has_source_and_claim(targets.topic_map, source, claim):
        pending.append("topic-map")
    return pending


def _notes_by_topic(vault: Path, note_type: str) -> dict[str, Path]:
    grouped: dict[str, list[Path]] = {}
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != note_type:
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        topic = _frontmatter_scalar(frontmatter, "topic")
        if topic:
            grouped.setdefault(topic, []).append(path)
    return {topic: sorted(paths)[-1] for topic, paths in grouped.items()}


def _path_has_claim(path: Path, claim_id: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return bool(re.search(rf"(?m)^\|\s*{re.escape(claim_id)}\s*\|", text)) or claim_id in text


def _path_has_claim_or_source(path: Path, source: SourceSnapshot, claim: SourceClaimSnapshot) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return claim.claim_id in text or bool(source.preferred_url and source.preferred_url in text)


def _topic_map_has_source_and_claim(path: Path, source: SourceSnapshot, claim: SourceClaimSnapshot) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    source_target = _wikilink_target(source.relative_path)
    return source_target in text and claim.claim_id in text


def _apply_candidate(candidate: PaperDownstreamCandidate, *, vault: Path, dry_run: bool) -> tuple[list[str], list[Path], str]:
    updated_targets: list[str] = []
    updated_paths: list[Path] = []
    missing_targets: list[str] = []

    ledger = candidate.targets.evidence_ledger
    if ledger:
        text = ledger.read_text(encoding="utf-8", errors="replace")
        if not _ledger_has_claim(text, candidate.claim.claim_id):
            updated_text = _append_ledger_row(text, _ledger_row(candidate.source, candidate.claim))
            if not dry_run:
                ledger.write_text(updated_text, encoding="utf-8")
            updated_targets.append("evidence-ledger")
            updated_paths.append(ledger)
    else:
        missing_targets.append("evidence-ledger")

    blueprint = candidate.targets.service_blueprint
    if blueprint:
        text = blueprint.read_text(encoding="utf-8", errors="replace")
        if not _path_text_has_claim_or_source(text, candidate.source, candidate.claim):
            updated_text = _append_to_section(text, "Evidence", [_blueprint_evidence_line(candidate.source, candidate.claim)])
            if not dry_run:
                blueprint.write_text(updated_text, encoding="utf-8")
            updated_targets.append("service-blueprint")
            updated_paths.append(blueprint)
    else:
        missing_targets.append("service-blueprint")

    topic_map = candidate.targets.topic_map
    if topic_map:
        text = topic_map.read_text(encoding="utf-8", errors="replace")
        changed = False
        source_line = _topic_map_source_line(candidate.source)
        claim_line = _topic_map_claim_line(candidate.source, candidate.claim, ledger)
        if _wikilink_target(candidate.source.relative_path) not in text:
            text = _append_to_section(text, "Source Notes", [source_line])
            changed = True
        if candidate.claim.claim_id not in text:
            text = _append_to_section(text, "Claim Index", [claim_line])
            changed = True
        if changed:
            if not dry_run:
                topic_map.write_text(text, encoding="utf-8")
            updated_targets.append("topic-map")
            updated_paths.append(topic_map)
    else:
        missing_targets.append("topic-map")

    reason = "missing downstream target notes: " + ", ".join(missing_targets) if missing_targets else ""
    return updated_targets, updated_paths, reason


def _ledger_has_claim(text: str, claim_id: str) -> bool:
    return bool(re.search(rf"(?m)^\|\s*{re.escape(claim_id)}\s*\|", text))


def _path_text_has_claim_or_source(text: str, source: SourceSnapshot, claim: SourceClaimSnapshot) -> bool:
    return claim.claim_id in text or bool(source.preferred_url and source.preferred_url in text)


def _append_ledger_row(text: str, row: str) -> str:
    lines = text.splitlines()
    insert_at = -1
    for index, line in enumerate(lines):
        if LEDGER_ROW_RE.match(line):
            insert_at = index + 1
    if insert_at == -1:
        table = "\n".join(
            [
                "| claim_id | claim | source | source_type | checked_at | confidence | note |",
                "|---|---|---|---|---|---|---|",
                row,
            ]
        )
        return text.rstrip() + "\n\n" + table + "\n"
    lines.insert(insert_at, row)
    return "\n".join(lines).rstrip() + "\n"


def _append_to_section(text: str, heading: str, lines: list[str]) -> str:
    missing_lines = [line for line in lines if line and line not in text]
    if not missing_lines:
        return text
    heading_pattern = re.compile(rf"(?m)^## {re.escape(heading)}\s*$")
    match = heading_pattern.search(text)
    block = "\n".join(missing_lines)
    if not match:
        return text.rstrip() + f"\n\n## {heading}\n\n{block}\n"

    section_start = text.find("\n", match.end())
    section_start = len(text) if section_start == -1 else section_start + 1
    next_match = re.search(r"(?m)^##\s+", text[section_start:])
    section_end = section_start + next_match.start() if next_match else len(text)

    before = text[:section_end].rstrip()
    after = text[section_end:].lstrip("\n")
    updated = before + "\n" + block + "\n"
    if after:
        updated += "\n" + after
    return updated.rstrip() + "\n"


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


def _blueprint_evidence_line(source: SourceSnapshot, claim: SourceClaimSnapshot) -> str:
    return f"- {claim.claim_id}: [{_escape_link_text(source.title)}]({source.preferred_url}) ({source.source_type}; {_wikilink(source.relative_path)})"


def _topic_map_source_line(source: SourceSnapshot) -> str:
    return f"- {_wikilink(source.relative_path)}"


def _topic_map_claim_line(source: SourceSnapshot, claim: SourceClaimSnapshot, ledger: Path | None) -> str:
    evidence = claim.evidence or claim.claim or f"Claim from {source.title}."
    links = [_wikilink(source.relative_path)]
    if ledger:
        try:
            links.insert(0, _wikilink(ledger.relative_to(source.path.parents[2]).as_posix()))
        except ValueError:
            pass
    return f"- {claim.claim_id}: {evidence} ({', '.join(links)})"


def _candidate_json(candidates: list[PaperDownstreamCandidate]) -> str:
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2)


def _candidate_to_json(candidate: PaperDownstreamCandidate) -> dict:
    return {
        "proposal_id": candidate.proposal_id,
        "pending_targets": candidate.pending_targets,
        "source": {
            "path": candidate.source.relative_path,
            "topic": candidate.source.topic,
            "source_id": candidate.source.source_id,
            "source_type": candidate.source.source_type,
            "title": candidate.source.title,
            "source_url": candidate.source.source_url,
            "canonical_url": candidate.source.canonical_url,
            "checked_at": candidate.source.checked_at,
        },
        "claim": {
            "claim_id": candidate.claim.claim_id,
            "claim": candidate.claim.claim,
            "evidence": candidate.claim.evidence,
            "confidence": candidate.claim.confidence,
            "category": candidate.claim.category,
        },
        "targets": {
            "evidence_ledger": _relative_path(candidate.targets.evidence_ledger, candidate.source.path),
            "service_blueprint": _relative_path(candidate.targets.service_blueprint, candidate.source.path),
            "topic_map": _relative_path(candidate.targets.topic_map, candidate.source.path),
        },
    }


def _candidate_records(text: str, *, vault: Path) -> dict[str, PaperDownstreamCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, PaperDownstreamCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item, vault=vault)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _candidate_from_json(item: dict, *, vault: Path) -> PaperDownstreamCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    source_item = item.get("source")
    claim_item = item.get("claim")
    target_item = item.get("targets")
    if not proposal_id or not isinstance(source_item, dict) or not isinstance(claim_item, dict) or not isinstance(target_item, dict):
        return None
    source_path = _vault_path(vault, str(source_item.get("path") or ""))
    if source_path is None:
        return None
    source = SourceSnapshot(
        path=source_path,
        relative_path=str(source_item.get("path") or "").strip(),
        topic=str(source_item.get("topic") or "").strip(),
        source_id=str(source_item.get("source_id") or "").strip(),
        source_type=str(source_item.get("source_type") or "").strip(),
        title=str(source_item.get("title") or "").strip(),
        source_url=str(source_item.get("source_url") or "").strip(),
        canonical_url=str(source_item.get("canonical_url") or "").strip(),
        checked_at=str(source_item.get("checked_at") or "").strip(),
        claims=[],
    )
    claim = SourceClaimSnapshot(
        claim_id=str(claim_item.get("claim_id") or "").strip(),
        claim=str(claim_item.get("claim") or "").strip(),
        evidence=str(claim_item.get("evidence") or "").strip(),
        confidence=str(claim_item.get("confidence") or "").strip(),
        category=str(claim_item.get("category") or "").strip(),
    )
    if not source.source_id or not source.title or not source.preferred_url or not claim.claim_id:
        return None
    targets = DownstreamNoteTargets(
        evidence_ledger=_vault_path(vault, str(target_item.get("evidence_ledger") or "")),
        service_blueprint=_vault_path(vault, str(target_item.get("service_blueprint") or "")),
        topic_map=_vault_path(vault, str(target_item.get("topic_map") or "")),
    )
    pending_targets = [str(target) for target in item.get("pending_targets") or [] if str(target).strip()]
    return PaperDownstreamCandidate(
        proposal_id=proposal_id,
        source=source,
        claim=claim,
        targets=targets,
        pending_targets=pending_targets,
    )


def _checked_proposal_ids(text: str) -> list[str]:
    return [match.group("proposal_id") for match in DOWNSTREAM_CHECK_RE.finditer(text) if match.group("state").strip().lower() == "x"]


def _paper_downstream_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "paper-downstream-proposals":
            paths.append(path)
    return sorted(paths)


def _proposal_lines(candidates: list[PaperDownstreamCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(_proposal_line(candidate) for candidate in candidates)


def _proposal_line(candidate: PaperDownstreamCandidate) -> str:
    source = candidate.source
    claim = candidate.claim
    targets = ", ".join(candidate.pending_targets)
    return (
        f"- [ ] {candidate.proposal_id} Add {claim.claim_id} from {source.source_id} "
        f"[{source.title}]({source.preferred_url}) to {targets}"
    )


def _apply_item_lines(items: list[PaperDownstreamApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.proposal_id} {item.source_id}/{item.claim_id}: {item.title} -> {', '.join(item.updated_targets)}"
        for item in items
    )


def _skipped_item_lines(items: list[PaperDownstreamSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_id} {item.title}: {item.reason}" for item in items)


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _warning_lines(warnings: list[str]) -> str:
    if not warnings:
        return "- None."
    return "\n".join(f"- {warning}" for warning in warnings)


def _hidden_line(hidden: int, max_proposals: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals={max_proposals}."


def _relative_path(path: Path | None, anchor_path: Path) -> str:
    if path is None:
        return ""
    vault = anchor_path.parents[2]
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return path.as_posix()


def _vault_path(vault: Path, relative: str) -> Path | None:
    clean = relative.strip().strip("/")
    if not clean:
        return None
    candidate = (vault / clean).resolve()
    if candidate != vault and vault not in candidate.parents:
        return None
    return candidate


def _wikilink(path: str) -> str:
    target = _wikilink_target(path)
    label = Path(target).stem
    return f"[[{target}|{label}]]"


def _wikilink_target(path: str) -> str:
    return path[:-3] if path.endswith(".md") else path


def _table_cell(value: str) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()


def _escape_link_text(value: str) -> str:
    return str(value or "").replace("[", "\\[").replace("]", "\\]")
