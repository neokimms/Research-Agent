def synthesis_instructions(*, bilingual: bool = True) -> str:
    bilingual_rule = (
        "- In every prose section, show the original text and Korean translation together using labels `**원본**` and `**한국어 번역**`."
        if bilingual
        else "- Write concise prose in the topic's primary language. Do not add Korean translation blocks unless the evidence requires them."
    )
    return f"""You are a careful IT research editor.

Write an Obsidian-ready Markdown service blueprint from the provided topic and evidence.

Rules:
- Prefer official documentation, standards, and papers over general web material.
- Do not invent citations.
- Every important claim must point to a source URL from the provided evidence.
- Clearly separate what is verified from what remains uncertain.
{bilingual_rule}
- Keep the final Markdown practical for building a service.
- Return Markdown only.
"""


SYNTHESIS_INSTRUCTIONS = synthesis_instructions()


def synthesis_prompt(topic: str, evidence_markdown: str, *, bilingual: bool = True) -> str:
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
    return f"""Topic:
{topic}

Evidence:
{evidence_markdown}

Required sections:
- One-Line Conclusion
- When To Use
- Structure Classification
- Recommended Baseline
- Implementation Order
- Operational Risks
- Verification
- Evidence
- Still Uncertain
- Related Notes

{bilingual_block}
"""
