from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .collectors import collect_standard_sources
from .config import Settings
from .models import SourceRecord
from .obsidian import ObsidianWriter
from .secrets import select_llm_provider
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


STANDARDS_PROPOSAL_RE = re.compile(
    r"^- \[(?P<state>[ xX])\] Replace (?P<link>\[\[[^\]]+\]\]) seed URL `(?P<old_url>[^`]+)` "
    r"with \[(?P<title>[^\]]+)\]\((?P<new_url>[^)]+)\) "
    r"\(provider: (?P<provider>[^,]+), score: (?P<score>[^)]+)\)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class StandardsSeedNote:
    path: Path
    relative_path: str
    topic: str
    title: str
    source_url: str
    domain: str
    source_provider: str


@dataclass(frozen=True)
class StandardsRefreshProposal:
    seed: StandardsSeedNote
    candidate: SourceRecord


@dataclass(frozen=True)
class StandardsRefreshResult:
    vault_path: Path
    provider: str
    provider_available: bool
    seed_notes: list[StandardsSeedNote]
    proposals: list[StandardsRefreshProposal]
    warnings: list[str]


@dataclass(frozen=True)
class StandardsRefreshWriteResult:
    result: StandardsRefreshResult
    note_path: Path


@dataclass(frozen=True)
class StandardsRefreshApplyItem:
    proposal_path: Path
    source_path: Path
    relative_source_path: str
    old_url: str
    new_url: str
    title: str
    provider: str
    score: str


@dataclass(frozen=True)
class StandardsRefreshSkippedItem:
    proposal_path: Path
    source_path: Path | None
    relative_source_path: str
    reason: str


@dataclass(frozen=True)
class StandardsRefreshApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[StandardsRefreshApplyItem]
    updated_paths: list[Path]
    skipped_items: list[StandardsRefreshSkippedItem]


def build_standards_refresh(settings: Settings, *, limit: int = 6) -> StandardsRefreshResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    provider = select_llm_provider(settings)
    seed_notes = _seed_standard_notes(vault)
    warnings: list[str] = []
    proposals: list[StandardsRefreshProposal] = []

    if not seed_notes:
        return StandardsRefreshResult(
            vault_path=vault,
            provider=provider.provider,
            provider_available=provider.available,
            seed_notes=[],
            proposals=[],
            warnings=[],
        )

    if not provider.available:
        warnings.append("No supported API key configured; exact standards URL collection was skipped.")
        return StandardsRefreshResult(
            vault_path=vault,
            provider=provider.provider,
            provider_available=False,
            seed_notes=seed_notes,
            proposals=[],
            warnings=warnings,
        )

    candidates_by_topic = _collect_candidates_by_topic(
        settings,
        seed_notes,
        provider_name=provider.provider,
        api_key=provider.api_key or "",
        limit=limit,
    )
    for seed in seed_notes:
        candidates = candidates_by_topic.get(seed.topic, [])
        match = _best_candidate_for_seed(seed, candidates)
        if match:
            proposals.append(StandardsRefreshProposal(seed=seed, candidate=match))
        else:
            warnings.append(f"No exact standards candidate found for {seed.relative_path}.")

    return StandardsRefreshResult(
        vault_path=vault,
        provider=provider.provider,
        provider_available=provider.available,
        seed_notes=seed_notes,
        proposals=proposals,
        warnings=warnings,
    )


def render_standards_refresh(result: StandardsRefreshResult, *, max_proposals: int = 50) -> str:
    shown = result.proposals[:max_proposals]
    hidden = len(result.proposals) - len(shown)
    return f"""Standards Refresh

Vault: {result.vault_path}
Provider: {result.provider}
Provider available: {result.provider_available}
Seed standards notes: {len(result.seed_notes)}
Exact URL proposals: {len(result.proposals)}
Warnings: {len(result.warnings)}

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden)}

Warnings:
{_warning_lines(result.warnings)}
"""


def write_standards_refresh_note(
    settings: Settings,
    *,
    limit: int = 6,
    max_proposals: int = 50,
) -> StandardsRefreshWriteResult:
    result = build_standards_refresh(settings, limit=limit)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_standards-refresh.md",
        render_standards_refresh_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return StandardsRefreshWriteResult(result=result, note_path=path)


def apply_standards_refresh(
    vault_path: Path,
    *,
    dry_run: bool = True,
    applied_at: str = "",
) -> StandardsRefreshApplyResult:
    vault = vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[StandardsRefreshApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[StandardsRefreshSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _standards_refresh_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        for match in STANDARDS_PROPOSAL_RE.finditer(text):
            if match.group("state").strip().lower() != "x":
                continue
            target = _link_target(match.group("link"))
            source_path = _resolve_source_path(vault, target)
            relative_source_path = target if target.endswith(".md") else f"{target}.md"
            if source_path is None or not source_path.exists():
                skipped_items.append(
                    StandardsRefreshSkippedItem(
                        proposal_path=proposal_path,
                        source_path=source_path,
                        relative_source_path=relative_source_path,
                        reason="source note not found",
                    )
                )
                continue
            item = StandardsRefreshApplyItem(
                proposal_path=proposal_path,
                source_path=source_path,
                relative_source_path=source_path.relative_to(vault).as_posix(),
                old_url=match.group("old_url").strip(),
                new_url=match.group("new_url").strip(),
                title=match.group("title").strip(),
                provider=match.group("provider").strip(),
                score=match.group("score").strip(),
            )
            current_text = source_path.read_text(encoding="utf-8", errors="replace")
            frontmatter, _ = _split_frontmatter(current_text)
            current_url = _frontmatter_scalar(frontmatter, "source_url") or _frontmatter_scalar(frontmatter, "canonical_url")
            if current_url == item.new_url:
                skipped_items.append(
                    StandardsRefreshSkippedItem(
                        proposal_path=proposal_path,
                        source_path=source_path,
                        relative_source_path=item.relative_source_path,
                        reason="source note already uses proposed URL",
                    )
                )
                continue
            if current_url and current_url != item.old_url and not _is_seed_url(current_url):
                skipped_items.append(
                    StandardsRefreshSkippedItem(
                        proposal_path=proposal_path,
                        source_path=source_path,
                        relative_source_path=item.relative_source_path,
                        reason=f"source note URL is no longer the proposed seed URL: {current_url}",
                    )
                )
                continue
            approved_items.append(item)
            updated_paths.append(source_path)
            proposal_paths_to_mark.add(proposal_path)
            if not dry_run:
                source_path.write_text(_apply_item_to_source_text(current_text, item), encoding="utf-8")

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if applied_at:
                values["applied_at"] = applied_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")

    return StandardsRefreshApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda path: path.as_posix()),
        skipped_items=skipped_items,
    )


def render_standards_refresh_apply_result(result: StandardsRefreshApplyResult) -> str:
    action = "Would update notes" if result.dry_run else "Updated notes"
    title = "Standards Refresh Apply Dry Run" if result.dry_run else "Standards Refresh Apply"
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


def render_standards_refresh_note(
    result: StandardsRefreshResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.proposals[:max_proposals]
    hidden = len(result.proposals) - len(shown)
    return f"""---
type: {yaml_scalar("standards-refresh")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
provider: {yaml_scalar(result.provider)}
seed_notes: {len(result.seed_notes)}
proposal_count: {len(result.proposals)}
warning_count: {len(result.warnings)}
---
# Standards Refresh

## Summary

| metric | value |
|---|---:|
| seed standards notes | {len(result.seed_notes)} |
| exact URL proposals | {len(result.proposals)} |
| warnings | {len(result.warnings)} |

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden)}

## Warnings

{_warning_lines(result.warnings)}

## Review Checklist

- [ ] Confirm each proposed URL is an official standards, governance, risk, compliance, or security framework page.
- [ ] Apply accepted URL updates to source note frontmatter and citable evidence.
- [ ] Rerun `source-audit`.
"""


def _seed_standard_notes(vault: Path) -> list[StandardsSeedNote]:
    notes: list[StandardsSeedNote] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _ = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "source-note":
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        if _frontmatter_scalar(frontmatter, "source_type") != "standards":
            continue
        source_url = _frontmatter_scalar(frontmatter, "source_url") or _frontmatter_scalar(frontmatter, "canonical_url")
        if not _is_seed_url(source_url):
            continue
        notes.append(
            StandardsSeedNote(
                path=path,
                relative_path=path.relative_to(vault).as_posix(),
                topic=_frontmatter_scalar(frontmatter, "topic"),
                title=_frontmatter_scalar(frontmatter, "title") or path.stem,
                source_url=source_url,
                domain=_domain(source_url),
                source_provider=_frontmatter_scalar(frontmatter, "source_provider"),
            )
        )
    notes.sort(key=lambda note: note.relative_path)
    return notes


def _standards_refresh_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _ = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "standards-refresh":
            paths.append(path)
    return sorted(paths)


def _apply_item_to_source_text(text: str, item: StandardsRefreshApplyItem) -> str:
    text = text.replace(item.old_url, item.new_url)
    updated = _set_frontmatter_scalars(
        text,
        {
            "source_url": item.new_url,
            "canonical_url": item.new_url,
            "source_provider": item.provider,
            "source_score": item.score,
        },
    )
    updated = _replace_metadata_line(updated, "Provider", item.provider)
    updated = _replace_metadata_line(updated, "Source Score", item.score)
    return updated


def _replace_metadata_line(text: str, label: str, value: str) -> str:
    return re.sub(rf"(?m)^- {re.escape(label)}: .*$", f"- {label}: {value}", text)


def _collect_candidates_by_topic(
    settings: Settings,
    seed_notes: list[StandardsSeedNote],
    *,
    provider_name: str,
    api_key: str,
    limit: int,
) -> dict[str, list[SourceRecord]]:
    topics = sorted({note.topic for note in seed_notes if note.topic})
    results: dict[str, list[SourceRecord]] = {}
    for topic in topics:
        records = collect_standard_sources(
            topic,
            settings.sources,
            api_key=api_key,
            model=_planner_model(settings, provider_name),
            provider=provider_name,
            limit=limit,
        )
        results[topic] = [record for record in records if record.source_type == "standards" and not _is_seed_url(record.url or record.canonical_url)]
    return results


def _best_candidate_for_seed(seed: StandardsSeedNote, candidates: list[SourceRecord]) -> SourceRecord | None:
    domain_matches = [
        candidate
        for candidate in candidates
        if _domain_matches(seed.domain, candidate.url or candidate.canonical_url)
    ]
    if not domain_matches:
        return None
    return sorted(domain_matches, key=lambda record: (record.source_score, len(record.summary), record.title), reverse=True)[0]


def _planner_model(settings: Settings, provider_name: str) -> str:
    if provider_name == "gemini":
        return settings.gemini.models.planner
    return settings.openai.models.planner


def _is_seed_url(url: str) -> bool:
    text = str(url or "").strip()
    if not text:
        return False
    parsed = urllib.parse.urlparse(text)
    return bool(parsed.netloc) and parsed.path in {"", "/"} and not parsed.query


def _domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


def _domain_matches(seed_domain: str, url: str) -> bool:
    domain = _domain(url)
    return domain == seed_domain or domain.endswith(f".{seed_domain}")


def _link_target(link: str) -> str:
    inner = link.strip()[2:-2]
    return inner.split("|", 1)[0].split("#", 1)[0].strip()


def _resolve_source_path(vault: Path, target: str) -> Path | None:
    relative = target if target.endswith(".md") else f"{target}.md"
    candidate = (vault / relative).resolve()
    if candidate == vault or vault not in candidate.parents:
        return None
    return candidate


def _proposal_lines(proposals: list[StandardsRefreshProposal]) -> str:
    if not proposals:
        return "- None."
    return "\n".join(_proposal_line(proposal) for proposal in proposals)


def _proposal_line(proposal: StandardsRefreshProposal) -> str:
    seed = proposal.seed
    candidate = proposal.candidate
    link = _wikilink(seed.relative_path)
    score = f"{candidate.source_score:.2f}" if candidate.source_score else "not scored"
    return (
        f"- [ ] Replace {link} seed URL `{seed.source_url}` with "
        f"[{candidate.title}]({candidate.url or candidate.canonical_url}) "
        f"(provider: {candidate.source_provider or 'unknown'}, score: {score})"
    )


def _wikilink(relative_path: str) -> str:
    target = relative_path[:-3] if relative_path.endswith(".md") else relative_path
    label = Path(target).stem
    return f"[[{target}|{label}]]"


def _warning_lines(warnings: list[str]) -> str:
    if not warnings:
        return "- None."
    return "\n".join(f"- {warning}" for warning in warnings)


def _apply_item_lines(items: list[StandardsRefreshApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.relative_source_path}: `{item.old_url}` -> `{item.new_url}` ({item.provider}, score {item.score})"
        for item in items
    )


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _skipped_item_lines(items: list[StandardsRefreshSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.relative_source_path}: {item.reason}"
        for item in items
    )


def _hidden_line(hidden: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals."
