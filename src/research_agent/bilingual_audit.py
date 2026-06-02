from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .bilingual_upgrade import REPORT_TYPES, TRANSLATION_APPENDIX_RE, _upgrade_note_text
from .config import Settings
from .obsidian import ObsidianWriter
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _split_frontmatter


PROBLEM_MARKERS = [
    "한국어 번역 검토 필요",
    "Fetch or search this domain for exact 근거",
    "주장s",
]


@dataclass(frozen=True)
class BilingualAuditIssue:
    severity: str
    relative_path: str
    check: str
    detail: str
    line: int | None = None


@dataclass(frozen=True)
class BilingualAuditResult:
    vault_path: Path
    generated_reports: int
    bilingual_notes: int
    appendix_notes: int
    inline_translation_notes: int
    issues: list[BilingualAuditIssue]

    @property
    def failure_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "FAIL")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "WARN")

    @property
    def passed(self) -> bool:
        return self.failure_count == 0


@dataclass(frozen=True)
class BilingualAuditWriteResult:
    result: BilingualAuditResult
    note_path: Path
    checked_at: str


def run_bilingual_audit(
    vault_path: Path,
    *,
    refresh_check: bool = True,
    translator_mode: str = "dictionary",
    target_paths: list[Path | str] | None = None,
) -> BilingualAuditResult:
    vault = vault_path.expanduser().resolve()
    generated_reports = 0
    bilingual_notes = 0
    appendix_notes = 0
    inline_translation_notes = 0
    issues: list[BilingualAuditIssue] = []

    for path in _audit_paths(vault, target_paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        note_type = _frontmatter_scalar(frontmatter, "type")
        if note_type not in REPORT_TYPES:
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue

        generated_reports += 1
        relative_path = path.relative_to(vault).as_posix()
        language = _frontmatter_scalar(frontmatter, "language").lower()
        original_language = _frontmatter_scalar(frontmatter, "original_language").lower()
        translation_language = _frontmatter_scalar(frontmatter, "translation_language").lower()
        appendix_count = len(TRANSLATION_APPENDIX_RE.findall(body))
        has_inline_translation = "**한국어 번역**" in body

        if language == "bilingual" and translation_language == "ko":
            bilingual_notes += 1
        if appendix_count:
            appendix_notes += 1
        if has_inline_translation:
            inline_translation_notes += 1

        if language != "bilingual":
            issues.append(_issue("FAIL", relative_path, "frontmatter.language", "Expected language: bilingual", text, "language:"))
        if original_language and original_language != "en":
            issues.append(_issue("WARN", relative_path, "frontmatter.original_language", "Expected original_language: en", text, "original_language:"))
        if not original_language:
            issues.append(_issue("WARN", relative_path, "frontmatter.original_language", "Missing original_language frontmatter", text, "generated_by:"))
        if translation_language != "ko":
            issues.append(_issue("FAIL", relative_path, "frontmatter.translation_language", "Expected translation_language: ko", text, "translation_language:"))
        if not has_inline_translation:
            issues.append(_issue("FAIL", relative_path, "body.translation_block", "Missing **한국어 번역** block", text, "# "))
        if appendix_count > 1:
            issues.append(_issue("FAIL", relative_path, "body.translation_appendix", f"Duplicate Korean Translation Draft sections: {appendix_count}", text, "## Korean Translation Draft"))
        if appendix_count == 1:
            _audit_appendix(text, body, relative_path, issues)
            if refresh_check and _appendix_translation_mode(body) == translator_mode:
                refreshed = _upgrade_note_text(text, translator_mode=translator_mode, refresh_translation=True)
                if refreshed.rstrip() != text.rstrip():
                    issues.append(
                        _issue(
                            "WARN",
                            relative_path,
                            "body.refresh",
                            "Korean Translation Draft differs from the current translation dictionary",
                            text,
                            "## Korean Translation Draft",
                        )
                    )
        for marker in PROBLEM_MARKERS:
            if marker in body:
                issues.append(_issue("WARN", relative_path, "body.translation_quality", f"Problem marker remains: {marker}", text, marker))

    issues.sort(key=lambda issue: (issue.severity != "FAIL", issue.relative_path, issue.line or 0, issue.check))
    return BilingualAuditResult(
        vault_path=vault,
        generated_reports=generated_reports,
        bilingual_notes=bilingual_notes,
        appendix_notes=appendix_notes,
        inline_translation_notes=inline_translation_notes,
        issues=issues,
    )


def render_bilingual_audit(result: BilingualAuditResult, *, max_issues: int = 50) -> str:
    status = "PASS" if result.passed else "FAIL"
    shown = result.issues[:max_issues]
    hidden = len(result.issues) - len(shown)
    return f"""Bilingual Audit

Vault: {result.vault_path}
Status: {status}
Generated report notes scanned: {result.generated_reports}
Bilingual notes: {result.bilingual_notes}
Translation appendix notes: {result.appendix_notes}
Inline Korean block notes: {result.inline_translation_notes}
Failures: {result.failure_count}
Warnings: {result.warning_count}

Issues:
{_issue_lines(shown)}
{_hidden_line(hidden)}
"""


def render_bilingual_audit_run_summary(result: BilingualAuditResult, *, max_issues: int = 5) -> str:
    status = "PASS" if result.passed else "FAIL"
    shown = result.issues[:max_issues]
    hidden = len(result.issues) - len(shown)
    korean_status = "통과" if result.passed else "실패"
    return f"""**원본**

Bilingual audit status: {status}. Failures: {result.failure_count}. Warnings: {result.warning_count}.

**한국어 번역**

한글 병기 점검 상태: {korean_status}. 실패 {result.failure_count}건, 경고 {result.warning_count}건.

| metric | value |
|---|---:|
| generated report notes scanned | {result.generated_reports} |
| bilingual notes | {result.bilingual_notes} |
| translation appendix notes | {result.appendix_notes} |
| inline Korean block notes | {result.inline_translation_notes} |

Issues:
{_issue_lines(shown)}
{_hidden_line(hidden)}
"""


def write_bilingual_audit_note(
    settings: Settings,
    *,
    refresh_check: bool = True,
    translator_mode: str = "dictionary",
    max_issues: int = 50,
    checked_at: datetime | None = None,
) -> BilingualAuditWriteResult:
    timestamp = checked_at or now_local(settings.app.timezone)
    checked_at_text = timestamp.isoformat(timespec="seconds")
    result = run_bilingual_audit(
        settings.obsidian.vault_path,
        refresh_check=refresh_check,
        translator_mode=translator_mode,
    )
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_bilingual-audit.md",
        render_bilingual_audit_note(result, checked_at=checked_at_text, max_issues=max_issues),
    )
    return BilingualAuditWriteResult(result=result, note_path=path, checked_at=checked_at_text)


def render_bilingual_audit_note(
    result: BilingualAuditResult,
    *,
    checked_at: str,
    max_issues: int = 50,
) -> str:
    status = "PASS" if result.passed else "FAIL"
    shown = result.issues[:max_issues]
    hidden = len(result.issues) - len(shown)
    return f"""---
type: {yaml_scalar("bilingual-audit")}
status: {yaml_scalar("draft")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
audit_status: {yaml_scalar(status)}
generated_reports: {result.generated_reports}
bilingual_notes: {result.bilingual_notes}
translation_appendix_notes: {result.appendix_notes}
inline_translation_notes: {result.inline_translation_notes}
failure_count: {result.failure_count}
warning_count: {result.warning_count}
---
# Bilingual Audit

## Summary

| metric | value |
|---|---:|
| status | {status} |
| generated report notes scanned | {result.generated_reports} |
| bilingual notes | {result.bilingual_notes} |
| translation appendix notes | {result.appendix_notes} |
| inline Korean block notes | {result.inline_translation_notes} |
| failures | {result.failure_count} |
| warnings | {result.warning_count} |

## Issues

{_issue_lines(shown)}
{_hidden_line(hidden)}

## Next Actions

{_next_actions(result)}
"""


def _audit_appendix(text: str, body: str, relative_path: str, issues: list[BilingualAuditIssue]) -> None:
    appendix_start = TRANSLATION_APPENDIX_RE.search(body)
    if not appendix_start:
        return
    appendix = body[appendix_start.start() :]
    if "Translation mode:" not in appendix:
        issues.append(_issue("WARN", relative_path, "appendix.translation_mode", "Missing Translation mode line", text, "## Korean Translation Draft"))
    if "**원본**" not in appendix:
        issues.append(_issue("FAIL", relative_path, "appendix.original_block", "Missing **원본** block in appendix", text, "## Korean Translation Draft"))
    if "**한국어 번역**" not in appendix:
        issues.append(_issue("FAIL", relative_path, "appendix.korean_block", "Missing **한국어 번역** block in appendix", text, "## Korean Translation Draft"))


def _appendix_translation_mode(body: str) -> str:
    appendix_start = TRANSLATION_APPENDIX_RE.search(body)
    if not appendix_start:
        return ""
    appendix = body[appendix_start.start() :]
    for line in appendix.splitlines():
        if line.startswith("Translation mode:"):
            return line.split(":", 1)[1].strip()
    return ""


def _issue(
    severity: str,
    relative_path: str,
    check: str,
    detail: str,
    text: str,
    needle: str,
) -> BilingualAuditIssue:
    return BilingualAuditIssue(
        severity=severity,
        relative_path=relative_path,
        check=check,
        detail=detail,
        line=_line_number(text, needle),
    )


def _audit_paths(vault: Path, target_paths: list[Path | str] | None) -> list[Path]:
    if target_paths is None:
        return _markdown_files(vault)
    paths: list[Path] = []
    seen: set[Path] = set()
    for item in target_paths:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = vault / candidate
        resolved = candidate.resolve()
        if resolved in seen or resolved.suffix != ".md" or not resolved.exists():
            continue
        if resolved != vault and vault not in resolved.parents:
            continue
        seen.add(resolved)
        paths.append(resolved)
    return paths


def _line_number(text: str, needle: str) -> int | None:
    index = text.find(needle)
    if index == -1:
        return None
    return text.count("\n", 0, index) + 1


def _issue_lines(issues: list[BilingualAuditIssue]) -> str:
    if not issues:
        return "- None."
    return "\n".join(
        f"- [{issue.severity}] {issue.relative_path}{_line_suffix(issue.line)} {issue.check}: {issue.detail}"
        for issue in issues
    )


def _line_suffix(line: int | None) -> str:
    return f":{line}" if line else ""


def _hidden_line(hidden: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more issue(s) hidden by --max-issues."


def _next_actions(result: BilingualAuditResult) -> str:
    if result.failure_count:
        return "- Fix failed bilingual contract checks, then rerun `bilingual-audit`."
    if result.warning_count:
        return "- Review warnings, run `upgrade-bilingual --refresh-translation --apply` if needed, then rerun `bilingual-audit`."
    return "- No bilingual follow-up required."
