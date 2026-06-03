from __future__ import annotations

import re


REQUIRED_BLUEPRINT_SECTIONS = [
    "One-Line Conclusion",
    "When To Use",
    "Structure Classification",
    "Recommended Baseline",
    "Implementation Order",
    "Operational Risks",
    "Verification",
    "Evidence",
    "Still Uncertain",
    "Related Notes",
]


DEFAULT_SECTION_TEXT = {
    "One-Line Conclusion": "TBD after reviewing the evidence ledger.",
    "When To Use": "- TBD after reviewing the evidence ledger.",
    "Structure Classification": "- TBD after reviewing the evidence ledger.",
    "Recommended Baseline": "```text\nTBD\n```",
    "Implementation Order": "1. Review the evidence ledger.\n2. Confirm source quality.\n3. Promote the note after human review.",
    "Operational Risks": "- Stale or weakly verified sources.",
    "Verification": "- Check every important claim against the evidence ledger.",
    "Evidence": "- See the generated evidence ledger.",
    "Still Uncertain": "- Add unresolved questions during review.",
    "Related Notes": "- TBD",
}


def stabilize_service_blueprint(markdown: str, *, topic: str, bilingual: bool = True) -> str:
    frontmatter, body = _split_frontmatter(markdown.strip())
    if not body:
        body = f"# {topic} Service Blueprint\n"

    if not _has_h1(body):
        body = f"# {topic} Service Blueprint\n\n{body.lstrip()}"

    present_sections = [section for section in REQUIRED_BLUEPRINT_SECTIONS if _has_heading(body, section)]
    default_filled_sections: list[str] = []
    for section in REQUIRED_BLUEPRINT_SECTIONS:
        if not _has_heading(body, section):
            default_filled_sections.append(section)
            body = body.rstrip() + f"\n\n## {section}\n\n{DEFAULT_SECTION_TEXT[section]}\n"

    if not _has_heading(body, "Synthesis Coverage"):
        body = _insert_before_heading(
            body,
            "Related Notes",
            _synthesis_coverage_section(present_sections, default_filled_sections, bilingual=bilingual),
        )

    return (frontmatter + "\n" if frontmatter else "") + body.rstrip() + "\n"


def _split_frontmatter(markdown: str) -> tuple[str, str]:
    if not markdown.startswith("---\n"):
        return "", markdown
    end = markdown.find("\n---", 4)
    if end == -1:
        return "", markdown
    closing_end = end + len("\n---")
    return markdown[:closing_end].strip(), markdown[closing_end:].strip()


def _has_h1(markdown: str) -> bool:
    return re.search(r"(?m)^#\s+\S+", markdown) is not None


def _has_heading(markdown: str, heading: str) -> bool:
    pattern = rf"(?m)^##\s+{re.escape(heading)}\s*$"
    return re.search(pattern, markdown) is not None


def _insert_before_heading(markdown: str, heading: str, section_markdown: str) -> str:
    pattern = re.compile(rf"(?m)^##\s+{re.escape(heading)}\s*$")
    match = pattern.search(markdown)
    if not match:
        return markdown.rstrip() + "\n\n" + section_markdown.rstrip() + "\n"
    return markdown[: match.start()].rstrip() + "\n\n" + section_markdown.rstrip() + "\n\n" + markdown[match.start() :].lstrip()


def _synthesis_coverage_section(
    present_sections: list[str],
    default_filled_sections: list[str],
    *,
    bilingual: bool,
) -> str:
    generated = ", ".join(f"`{section}`" for section in present_sections) or "None detected."
    filled = ", ".join(f"`{section}`" for section in default_filled_sections) or "None."
    if not bilingual:
        return f"""## Synthesis Coverage

- Provider output sections detected: {generated}
- Stabilization default-filled sections requiring review: {filled}
"""
    return f"""## Synthesis Coverage

**원본**

- Provider output sections detected: {generated}
- Stabilization default-filled sections requiring review: {filled}

**한국어 번역**

- 프로바이더 출력에서 감지된 섹션: {generated}
- 안정화 단계에서 기본값으로 채워 검토가 필요한 섹션: {filled}
"""
