from __future__ import annotations

from .report_profiles import get_report_profile, render_focus_rules_for_prompt, render_required_sections_for_prompt


def synthesis_instructions(
    *,
    bilingual: bool = True,
    domain_focus: str = "",
    research_type: str | None = None,
) -> str:
    profile = get_report_profile(research_type)
    bilingual_rule = (
        "- In every prose section, show the original text and Korean translation together using labels `**원본**` and `**한국어 번역**`."
        if bilingual
        else "- Write concise prose in the topic's primary language. Do not add Korean translation blocks unless the evidence requires them."
    )
    domain_rule = (
        f"- Prioritize evidence, framing, and examples relevant to the domain: {domain_focus.strip()}."
        if domain_focus.strip()
        else ""
    )
    return f"""You are a careful IT research editor.

Write an Obsidian-ready Markdown report from the provided topic and evidence.

Rules:
- Prefer official documentation, standards, and papers over general web material.
- Do not invent citations.
- Every important claim must point to a source URL from the provided evidence.
- Clearly separate what is verified from what remains uncertain.
- Use the `{profile.label}` report profile.
- Use the required headings exactly as provided in the prompt.
{bilingual_rule}
{domain_rule + chr(10) if domain_rule else ""}- Keep the final Markdown practical for the selected decision context.
- Profile-specific rules:
{render_focus_rules_for_prompt(profile)}
- Return Markdown only.
"""


SYNTHESIS_INSTRUCTIONS = synthesis_instructions()


def synthesis_prompt(
    topic: str,
    evidence_markdown: str,
    *,
    bilingual: bool = True,
    domain_focus: str = "",
    research_type: str | None = None,
) -> str:
    profile = get_report_profile(research_type)
    bilingual_block = (
        """For each prose section, include:
**원본**
<original text>

**한국어 번역**
<Korean translation>
"""
        if bilingual
        else "Write each prose section once, without duplicated translation blocks.\n"
    )
    domain_block = f"Domain focus: {domain_focus.strip()}\n" if domain_focus.strip() else ""
    return f"""Topic:
{topic}
Report profile: {profile.label}
Report title: {profile.report_title}
Profile summary: {profile.summary}
{domain_block}
Evidence:
{evidence_markdown}

Required sections:
{render_required_sections_for_prompt(profile)}

{bilingual_block}
"""
