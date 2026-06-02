from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .obsidian import REVIEWED_STATUSES
from .render import _translate_to_korean
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


REPORT_TYPES = {
    "source-note",
    "evidence-ledger",
    "service-blueprint",
    "topic-map",
    "run-log",
}
SKIP_TRANSLATION_HEADINGS = {
    "Artifacts",
    "Backlink Proposals",
    "Citation Metadata",
    "Core Notes",
    "Quality Gates",
    "Related Notes",
    "Source Notes",
    "Warnings",
}
H2_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
TRANSLATION_APPENDIX_RE = re.compile(r"(?m)^## Korean Translation Draft\s*$")


@dataclass(frozen=True)
class BilingualUpgradeCandidate:
    path: Path
    relative_path: str
    note_type: str
    status: str
    reason: str


@dataclass(frozen=True)
class BilingualUpgradeResult:
    dry_run: bool
    translator_mode: str
    candidates: list[BilingualUpgradeCandidate]
    upgraded_paths: list[Path]
    skipped_reviewed: list[BilingualUpgradeCandidate]
    skipped_existing: int
    total_scanned: int


def upgrade_bilingual_notes(
    vault_path: Path,
    *,
    dry_run: bool = True,
    include_reviewed: bool = False,
    max_notes: int | None = None,
    translator_mode: str = "dictionary",
    refresh_translation: bool = False,
) -> BilingualUpgradeResult:
    vault = vault_path.expanduser().resolve()
    candidates, skipped_reviewed, skipped_existing, total_scanned = _bilingual_candidates(
        vault,
        include_reviewed=include_reviewed,
        refresh_translation=refresh_translation,
    )
    selected = candidates[:max_notes] if max_notes is not None else candidates
    upgraded_paths: list[Path] = []

    for candidate in selected:
        text = candidate.path.read_text(encoding="utf-8", errors="replace")
        updated = _upgrade_note_text(
            text,
            translator_mode=translator_mode,
            refresh_translation=refresh_translation,
        )
        if updated == text:
            continue
        upgraded_paths.append(candidate.path)
        if not dry_run:
            candidate.path.write_text(updated, encoding="utf-8")

    return BilingualUpgradeResult(
        dry_run=dry_run,
        translator_mode=translator_mode,
        candidates=selected,
        upgraded_paths=upgraded_paths,
        skipped_reviewed=skipped_reviewed,
        skipped_existing=skipped_existing,
        total_scanned=total_scanned,
    )


def render_bilingual_upgrade_result(result: BilingualUpgradeResult) -> str:
    title = "Bilingual Upgrade Dry Run" if result.dry_run else "Bilingual Upgrade"
    action = "Would update notes" if result.dry_run else "Updated notes"
    return f"""{title}

Translator mode: {result.translator_mode}
Generated report notes scanned: {result.total_scanned}
Candidates: {len(result.candidates)}
{action}: {len(result.upgraded_paths)}
Skipped reviewed/evergreen notes: {len(result.skipped_reviewed)}
Skipped already bilingual notes: {result.skipped_existing}

Candidate notes:
{_candidate_lines(result.candidates)}

Skipped protected notes:
{_candidate_lines(result.skipped_reviewed)}
"""


def _bilingual_candidates(
    vault: Path,
    *,
    include_reviewed: bool,
    refresh_translation: bool,
) -> tuple[list[BilingualUpgradeCandidate], list[BilingualUpgradeCandidate], int, int]:
    candidates: list[BilingualUpgradeCandidate] = []
    skipped_reviewed: list[BilingualUpgradeCandidate] = []
    skipped_existing = 0
    total_scanned = 0
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        note_type = _frontmatter_scalar(frontmatter, "type")
        if note_type not in REPORT_TYPES:
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        total_scanned += 1
        already_bilingual = _already_bilingual(frontmatter, body)
        if already_bilingual and not _refreshable_translation(body, refresh_translation=refresh_translation):
            skipped_existing += 1
            continue
        if not already_bilingual and refresh_translation:
            continue
        status = _frontmatter_scalar(frontmatter, "status")
        reason = (
            "refresh existing Korean translation draft"
            if already_bilingual
            else "missing bilingual frontmatter and translation draft"
        )
        candidate = BilingualUpgradeCandidate(
            path=path,
            relative_path=path.relative_to(vault).as_posix(),
            note_type=note_type,
            status=status,
            reason=reason,
        )
        if status.lower() in REVIEWED_STATUSES and not include_reviewed:
            skipped_reviewed.append(candidate)
        else:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item.relative_path)
    skipped_reviewed.sort(key=lambda item: item.relative_path)
    return candidates, skipped_reviewed, skipped_existing, total_scanned


def _upgrade_note_text(text: str, *, translator_mode: str, refresh_translation: bool = False) -> str:
    updated = _set_frontmatter_scalars(
        text,
        {
            "language": "bilingual",
            "original_language": "en",
            "translation_language": "ko",
            "translation_mode": translator_mode,
        },
    )
    _, upgraded_body = _split_frontmatter(updated)
    if refresh_translation:
        original_body, stripped = _strip_translation_appendix(upgraded_body)
        if not stripped:
            return updated
        appendix = _translation_appendix(original_body, translator_mode=translator_mode)
        return _replace_body(updated, original_body.rstrip() + "\n\n" + appendix + "\n")
    if "## Korean Translation Draft" in upgraded_body:
        return updated
    appendix = _translation_appendix(upgraded_body, translator_mode=translator_mode)
    return updated.rstrip() + "\n\n" + appendix + "\n"


def _translation_appendix(body: str, *, translator_mode: str) -> str:
    blocks = _translatable_sections(body)
    if not blocks:
        original = _first_nonempty_body(body)
        blocks = [("Original Note", original)] if original else []

    if not blocks:
        return f"""## Korean Translation Draft

- No translatable text found.
"""

    rendered_blocks = []
    for heading, content in blocks:
        original, korean = _translation_pair(content)
        rendered_blocks.append(
            f"""### {heading}

**원본**

{original}

**한국어 번역**

{korean}
"""
        )
    return f"""## Korean Translation Draft

Translation mode: {translator_mode}

{chr(10).join(rendered_blocks).rstrip()}
"""


def _translatable_sections(body: str) -> list[tuple[str, str]]:
    matches = list(H2_RE.finditer(body))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        if heading in SKIP_TRANSLATION_HEADINGS:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        if not content or _looks_like_link_list_only(content):
            continue
        blocks.append((heading, content))
    return blocks


def _already_bilingual(frontmatter: dict[str, str | list[str]], body: str) -> bool:
    language = _frontmatter_scalar(frontmatter, "language").lower()
    translation_language = _frontmatter_scalar(frontmatter, "translation_language").lower()
    has_korean_block = "**한국어 번역**" in body
    has_upgrade_appendix = "## Korean Translation Draft" in body
    return has_upgrade_appendix or (
        language == "bilingual" and (translation_language == "ko" or has_korean_block)
    )


def _refreshable_translation(body: str, *, refresh_translation: bool) -> bool:
    return refresh_translation and bool(TRANSLATION_APPENDIX_RE.search(body))


def _strip_translation_appendix(body: str) -> tuple[str, bool]:
    match = TRANSLATION_APPENDIX_RE.search(body)
    if not match:
        return body, False
    next_h2 = H2_RE.search(body, match.end())
    if next_h2:
        stripped = body[: match.start()].rstrip() + "\n\n" + body[next_h2.start() :].lstrip()
        return stripped.strip("\n"), True
    return body[: match.start()].strip("\n"), True


def _replace_body(text: str, body: str) -> str:
    if not text.startswith("---\n"):
        return body.rstrip() + "\n"
    end = text.find("\n---", 4)
    if end == -1:
        return body.rstrip() + "\n"
    return text[: end + 4] + "\n" + body.strip("\n") + "\n"


MAX_TRANSLATION_SECTION_CHARS = 4000
TRUNCATION_NOTE_ORIGINAL = "[Translation draft truncated for safety. Review the original section for the remaining content.]"
TRUNCATION_NOTE_KOREAN = "[번역 초안은 안전을 위해 일부만 포함했습니다. 남은 내용은 원문 섹션을 검토하세요.]"


def _translation_pair(content: str) -> tuple[str, str]:
    original = content.strip()
    truncated = False
    if len(original) > MAX_TRANSLATION_SECTION_CHARS:
        original = original[:MAX_TRANSLATION_SECTION_CHARS].rstrip()
        truncated = True
    korean = _translate_to_korean(original)
    if truncated:
        original = f"{original}\n\n{TRUNCATION_NOTE_ORIGINAL}"
        korean = f"{korean}\n\n{TRUNCATION_NOTE_KOREAN}"
    return original, korean


def _first_nonempty_body(body: str) -> str:
    lines = []
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        if line.strip():
            lines.append(line)
    return "\n".join(lines).strip()


def _looks_like_link_list_only(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return bool(lines) and all(line.startswith("- [[") or line.startswith("[[") for line in lines)


def _candidate_lines(candidates: list[BilingualUpgradeCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(
        f"- {candidate.relative_path} ({candidate.note_type}, {candidate.status or 'no status'}): {candidate.reason}"
        for candidate in candidates
    )
