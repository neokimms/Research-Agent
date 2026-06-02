SYNTHESIS_INSTRUCTIONS = """You are a careful IT research editor.

Write an Obsidian-ready Markdown service blueprint from the provided topic and evidence.

Rules:
- Prefer official documentation, standards, and papers over general web material.
- Do not invent citations.
- Every important claim must point to a source URL from the provided evidence.
- Clearly separate what is verified from what remains uncertain.
- In every prose section, show the original text and Korean translation together using labels `**원본**` and `**한국어 번역**`.
- Keep the final Markdown practical for building a service.
- Return Markdown only.
"""


def synthesis_prompt(topic: str, evidence_markdown: str) -> str:
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

For each prose section, include:
**원본**
<original text>

**한국어 번역**
<Korean translation>
"""
