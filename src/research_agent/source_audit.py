from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Settings
from .obsidian import ObsidianWriter
from .source_reference_sync import preview_source_reference_sync
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _split_frontmatter


CLAIM_ID_RE = re.compile(r"(?m)^-\s+(?:\*\*원본:\*\*\s+)?E\d{3,}\b")


@dataclass(frozen=True)
class SourceAuditIssue:
    severity: str
    relative_path: str
    check: str
    detail: str
    line: int | None = None


@dataclass(frozen=True)
class SourceAuditResult:
    vault_path: Path
    source_notes: int
    official_docs: int
    papers: int
    standards: int
    exact_official_docs: int
    seed_official_docs: int
    paper_identity_notes: int
    notes_with_claims: int
    evidence_ledgers: int
    service_blueprints: int
    stale_reference_count: int
    issues: list[SourceAuditIssue]

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
class SourceAuditWriteResult:
    result: SourceAuditResult
    note_path: Path
    checked_at: str


def run_source_audit(vault_path: Path, *, target_paths: list[Path | str] | None = None) -> SourceAuditResult:
    vault = vault_path.expanduser().resolve()
    source_notes = 0
    official_docs = 0
    papers = 0
    standards = 0
    exact_official_docs = 0
    seed_official_docs = 0
    paper_identity_notes = 0
    notes_with_claims = 0
    issues: list[SourceAuditIssue] = []

    for path in _audit_paths(vault, target_paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "source-note":
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue

        source_notes += 1
        relative_path = path.relative_to(vault).as_posix()
        source_id = _frontmatter_scalar(frontmatter, "source_id")
        source_type = _frontmatter_scalar(frontmatter, "source_type")
        source_url = _frontmatter_scalar(frontmatter, "source_url")
        canonical_url = _frontmatter_scalar(frontmatter, "canonical_url")
        doi = _frontmatter_scalar(frontmatter, "doi")
        arxiv_id = _frontmatter_scalar(frontmatter, "arxiv_id")
        source_provider = _frontmatter_scalar(frontmatter, "source_provider")
        source_score = _frontmatter_scalar(frontmatter, "source_score")
        checked_at = _frontmatter_scalar(frontmatter, "checked_at")
        has_claims = _has_claims(body)

        if source_type == "official-docs":
            official_docs += 1
            if _is_seed_url(source_url or canonical_url):
                seed_official_docs += 1
                issues.append(_issue("WARN", relative_path, "official_docs.exact_url", "Official docs source looks like a seed domain rather than an exact page", text, "source_url:"))
            else:
                exact_official_docs += 1
        elif source_type == "papers":
            papers += 1
            if doi or arxiv_id:
                paper_identity_notes += 1
            else:
                issues.append(_issue("WARN", relative_path, "paper.identity", "Paper source is missing DOI and arXiv metadata", text, "doi:"))
        elif source_type == "standards":
            standards += 1

        if has_claims:
            notes_with_claims += 1

        if not source_id:
            issues.append(_issue("FAIL", relative_path, "frontmatter.source_id", "Missing source_id frontmatter", text, "source_id:"))
        if not source_type:
            issues.append(_issue("FAIL", relative_path, "frontmatter.source_type", "Missing source_type frontmatter", text, "source_type:"))
        if not source_url and not canonical_url:
            issues.append(_issue("FAIL", relative_path, "frontmatter.source_url", "Missing source_url/canonical_url", text, "source_url:"))
        if not checked_at:
            issues.append(_issue("WARN", relative_path, "frontmatter.checked_at", "Missing checked_at frontmatter", text, "checked_at:"))
        if not source_provider and source_type in {"papers", "official-docs"}:
            issues.append(_issue("WARN", relative_path, "frontmatter.source_provider", "Missing source_provider frontmatter", text, "source_provider:"))
        if source_score and _score_value(source_score) < 0.6:
            issues.append(_issue("WARN", relative_path, "frontmatter.source_score", f"Low source_score: {source_score}", text, "source_score:"))
        if not has_claims:
            issues.append(_issue("WARN", relative_path, "body.claims", "No structured evidence claim is linked in Important Claims", text, "## Important Claims"))

    evidence_ledgers = 0
    service_blueprints = 0
    stale_reference_count = 0
    if target_paths is None:
        sync_result = preview_source_reference_sync(vault)
        evidence_ledgers = sync_result.evidence_ledgers
        service_blueprints = sync_result.service_blueprints
        stale_reference_count = len(sync_result.replacements)
        for replacement in sync_result.replacements:
            issues.append(
                SourceAuditIssue(
                    "WARN",
                    replacement.relative_path,
                    f"downstream.{replacement.change_type}",
                    f"Stale source reference: {replacement.detail}",
                )
            )

    issues.sort(key=lambda issue: (issue.severity != "FAIL", issue.relative_path, issue.line or 0, issue.check))
    return SourceAuditResult(
        vault_path=vault,
        source_notes=source_notes,
        official_docs=official_docs,
        papers=papers,
        standards=standards,
        exact_official_docs=exact_official_docs,
        seed_official_docs=seed_official_docs,
        paper_identity_notes=paper_identity_notes,
        notes_with_claims=notes_with_claims,
        evidence_ledgers=evidence_ledgers,
        service_blueprints=service_blueprints,
        stale_reference_count=stale_reference_count,
        issues=issues,
    )


def render_source_audit(result: SourceAuditResult, *, max_issues: int = 50) -> str:
    status = "PASS" if result.passed else "FAIL"
    shown = result.issues[:max_issues]
    hidden = len(result.issues) - len(shown)
    return f"""Source Audit

Vault: {result.vault_path}
Status: {status}
Source notes scanned: {result.source_notes}
Official docs: {result.official_docs}
Exact official docs: {result.exact_official_docs}
Seed official docs: {result.seed_official_docs}
Standards: {result.standards}
Papers: {result.papers}
Papers with DOI/arXiv identity: {result.paper_identity_notes}
Notes with structured claims: {result.notes_with_claims}
Evidence ledgers scanned: {result.evidence_ledgers}
Service blueprints scanned: {result.service_blueprints}
Stale downstream references: {result.stale_reference_count}
Failures: {result.failure_count}
Warnings: {result.warning_count}

Issues:
{_issue_lines(shown)}
{_hidden_line(hidden)}
"""


def write_source_audit_note(
    settings: Settings,
    *,
    max_issues: int = 50,
    checked_at: datetime | None = None,
) -> SourceAuditWriteResult:
    timestamp = checked_at or now_local(settings.app.timezone)
    checked_at_text = timestamp.isoformat(timespec="seconds")
    result = run_source_audit(settings.obsidian.vault_path)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_source-audit.md",
        render_source_audit_note(result, checked_at=checked_at_text, max_issues=max_issues),
    )
    return SourceAuditWriteResult(result=result, note_path=path, checked_at=checked_at_text)


def render_source_audit_note(
    result: SourceAuditResult,
    *,
    checked_at: str,
    max_issues: int = 50,
) -> str:
    status = "PASS" if result.passed else "FAIL"
    shown = result.issues[:max_issues]
    hidden = len(result.issues) - len(shown)
    return f"""---
type: {yaml_scalar("source-audit")}
status: {yaml_scalar("draft")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
audit_status: {yaml_scalar(status)}
source_notes: {result.source_notes}
official_docs: {result.official_docs}
exact_official_docs: {result.exact_official_docs}
seed_official_docs: {result.seed_official_docs}
standards: {result.standards}
papers: {result.papers}
paper_identity_notes: {result.paper_identity_notes}
notes_with_claims: {result.notes_with_claims}
evidence_ledgers: {result.evidence_ledgers}
service_blueprints: {result.service_blueprints}
stale_reference_count: {result.stale_reference_count}
failure_count: {result.failure_count}
warning_count: {result.warning_count}
---
# Source Audit

## Summary

| metric | value |
|---|---:|
| status | {status} |
| source notes scanned | {result.source_notes} |
| official docs | {result.official_docs} |
| exact official docs | {result.exact_official_docs} |
| seed official docs | {result.seed_official_docs} |
| standards | {result.standards} |
| papers | {result.papers} |
| papers with DOI/arXiv identity | {result.paper_identity_notes} |
| notes with structured claims | {result.notes_with_claims} |
| evidence ledgers scanned | {result.evidence_ledgers} |
| service blueprints scanned | {result.service_blueprints} |
| stale downstream references | {result.stale_reference_count} |
| failures | {result.failure_count} |
| warnings | {result.warning_count} |

## Issues

{_issue_lines(shown)}
{_hidden_line(hidden)}

## Next Actions

{_next_actions(result)}
"""


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


def _has_claims(body: str) -> bool:
    if "No structured claims extracted yet." in body:
        return False
    return bool(CLAIM_ID_RE.search(body))


def _is_seed_url(url: str) -> bool:
    text = str(url or "").strip()
    if not text:
        return False
    parsed = urllib.parse.urlparse(text)
    return bool(parsed.netloc) and parsed.path in {"", "/"} and not parsed.query


def _score_value(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def _issue(
    severity: str,
    relative_path: str,
    check: str,
    detail: str,
    text: str,
    needle: str,
) -> SourceAuditIssue:
    return SourceAuditIssue(
        severity=severity,
        relative_path=relative_path,
        check=check,
        detail=detail,
        line=_line_number(text, needle),
    )


def _line_number(text: str, needle: str) -> int | None:
    index = text.find(needle)
    if index == -1:
        return None
    return text.count("\n", 0, index) + 1


def _issue_lines(issues: list[SourceAuditIssue]) -> str:
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


def _next_actions(result: SourceAuditResult) -> str:
    if result.failure_count:
        return "- Fix failed source note contract checks, then rerun `source-audit`."
    if result.stale_reference_count:
        return "- Run `sync-source-references --apply`, then rerun `source-audit`."
    if result.seed_official_docs:
        return "- Run `official-docs-refresh --write-note`, approve exact URL candidates, apply them, then rerun `source-audit`."
    if result.warning_count:
        return "- Review warnings and rerun `source-audit` after source metadata is cleaned up."
    return "- No source quality follow-up required."
