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


def stabilize_service_blueprint(markdown: str, *, topic: str) -> str:
    frontmatter, body = _split_frontmatter(markdown.strip())
    if not body:
        body = f"# {topic} Service Blueprint\n"

    if not _has_h1(body):
        body = f"# {topic} Service Blueprint\n\n{body.lstrip()}"

    for section in REQUIRED_BLUEPRINT_SECTIONS:
        if not _has_heading(body, section):
            body = body.rstrip() + f"\n\n## {section}\n\n{DEFAULT_SECTION_TEXT[section]}\n"

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
