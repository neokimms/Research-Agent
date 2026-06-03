from __future__ import annotations

import json
import logging
import re
from typing import Any

from .gemini_client import GeminiError, GeminiGenerateClient, gemini_output_text
from .models import EvidenceBundle, EvidenceClaim, SourceRecord
from .openai_client import OpenAIError, OpenAIResponsesClient, output_text


logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_id": {"type": "string"},
                    "source_id": {"type": "string"},
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                    "source_title": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_type": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "category": {"type": "string"},
                },
                "required": [
                    "claim_id",
                    "source_id",
                    "claim",
                    "evidence",
                    "source_title",
                    "source_url",
                    "source_type",
                    "confidence",
                    "category",
                ],
            },
        },
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "needs_verification": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["claims", "conflicts", "needs_verification"],
}


def extract_evidence(
    topic: str,
    sources: list[SourceRecord],
    *,
    api_key: str | None,
    model: str,
    provider: str = "openai",
    offline: bool,
) -> EvidenceBundle:
    if offline or not api_key or not sources:
        reason = "offline" if offline else "missing api key" if not api_key else "no sources"
        logger.info(
            "using fallback evidence extraction",
            extra={"stage": "extract_evidence", "provider": provider, "topic": topic, "reason": reason},
        )
        return fallback_evidence(topic, sources)

    if provider == "gemini":
        return _extract_evidence_with_gemini(topic, sources, api_key=api_key, model=model)
    return _extract_evidence_with_openai(topic, sources, api_key=api_key, model=model)


def _extract_evidence_with_openai(
    topic: str,
    sources: list[SourceRecord],
    *,
    api_key: str,
    model: str,
) -> EvidenceBundle:
    client = OpenAIResponsesClient(api_key=api_key, default_model=model, timeout_seconds=90)
    try:
        response = client.create(
            input_text=_evidence_prompt(topic, sources),
            instructions=(
                "Extract structured evidence as JSON. "
                "Use only the provided source records. Do not invent URLs, titles, or claims. "
                "Prefer claims that help classify structure or choose a practical service baseline."
            ),
            model=model,
            text_format={
                "type": "json_schema",
                "name": "research_evidence_bundle",
                "description": "Structured evidence claims extracted from collected research sources.",
                "schema": EVIDENCE_SCHEMA,
                "strict": True,
            },
        )
    except OpenAIError as exc:
        logger.warning(
            "structured evidence extraction failed; using fallback evidence",
            extra={"stage": "extract_evidence", "provider": "openai", "topic": topic, "error": str(exc)},
        )
        return fallback_evidence(topic, sources)

    bundle = parse_evidence_output(output_text(response), sources=sources)
    return _structured_or_fallback(topic, sources, bundle)


def _extract_evidence_with_gemini(
    topic: str,
    sources: list[SourceRecord],
    *,
    api_key: str,
    model: str,
) -> EvidenceBundle:
    client = GeminiGenerateClient(api_key=api_key, default_model=model, timeout_seconds=90)
    try:
        response = client.generate(
            input_text=_evidence_prompt(topic, sources),
            instructions=(
                "Extract structured evidence as JSON. "
                "Use only the provided source records. Do not invent URLs, titles, or claims. "
                "Prefer claims that help classify structure or choose a practical service baseline."
            ),
            model=model,
            response_schema=EVIDENCE_SCHEMA,
        )
    except GeminiError as exc:
        logger.warning(
            "structured evidence extraction failed; using fallback evidence",
            extra={"stage": "extract_evidence", "provider": "gemini", "topic": topic, "error": str(exc)},
        )
        return fallback_evidence(topic, sources)

    bundle = parse_evidence_output(gemini_output_text(response), sources=sources)
    return _structured_or_fallback(topic, sources, bundle)


def _structured_or_fallback(topic: str, sources: list[SourceRecord], bundle: EvidenceBundle) -> EvidenceBundle:
    if bundle.claims:
        return EvidenceBundle(
            claims=bundle.claims,
            conflicts=bundle.conflicts,
            needs_verification=bundle.needs_verification,
            extraction_mode="structured-json",
        )
    logger.warning(
        "structured evidence output had no valid claims; using fallback evidence",
        extra={"stage": "extract_evidence", "topic": topic, "extraction_mode": bundle.extraction_mode},
    )
    return fallback_evidence(topic, sources)


def fallback_evidence(topic: str, sources: list[SourceRecord]) -> EvidenceBundle:
    claims: list[EvidenceClaim] = []
    for index, source in enumerate(sources, start=1):
        source_id = f"S{index:03d}"
        summary = source.summary.replace("\n", " ").strip()
        claim = summary or f"Review {source.title} for topic relevance."
        evidence = source.summary.replace("\n", " ").strip() or source.title
        source_url = source.url or source.canonical_url
        claims.append(
            EvidenceClaim(
                claim_id=f"E{index:03d}",
                source_id=source_id,
                claim=claim[:240],
                evidence=evidence[:240],
                source_title=source.title,
                source_url=source_url,
                source_type=source.source_type,
                confidence="medium" if source_url else "low",
                category=source.source_type,
            )
        )

    needs = [
        "Evidence extraction used fallback source summaries; review each claim before treating it as verified.",
        "Confirm exact official documentation pages instead of relying only on seed domains.",
        "Confirm paper metadata and DOI/arXiv IDs for all paper claims.",
    ]
    if not sources:
        needs = [f"Add sources for {topic}."]

    return EvidenceBundle(
        claims=claims,
        conflicts=[],
        needs_verification=needs,
        extraction_mode="fallback",
    )


def parse_evidence_output(text: str, *, sources: list[SourceRecord]) -> EvidenceBundle:
    data = _json_object(text)
    if not isinstance(data, dict):
        logger.warning(
            "evidence output was not valid JSON",
            extra={"stage": "parse_evidence", "text_length": len(text)},
        )
        return EvidenceBundle(claims=[], extraction_mode="parse-failed")

    errors = _validate_evidence_payload(data)
    if errors:
        logger.warning(
            "evidence output failed schema validation",
            extra={"stage": "parse_evidence", "errors": errors[:5]},
        )
        return EvidenceBundle(claims=[], needs_verification=errors, extraction_mode="schema-invalid")

    source_by_id = {f"S{index:03d}": source for index, source in enumerate(sources, start=1)}
    claims: list[EvidenceClaim] = []
    for index, item in enumerate(data.get("claims", []), start=1):
        if not isinstance(item, dict):
            continue
        claim = _claim_from_mapping(item, source_by_id=source_by_id, fallback_index=index)
        if claim:
            claims.append(claim)

    return EvidenceBundle(
        claims=claims,
        conflicts=_string_list(data.get("conflicts")),
        needs_verification=_string_list(data.get("needs_verification")),
        extraction_mode="structured-json",
    )


def _validate_evidence_payload(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_top_level = {"claims", "conflicts", "needs_verification"}
    for key in sorted(required_top_level):
        if key not in data:
            errors.append(f"Missing required evidence field: {key}")

    claims = data.get("claims")
    if not isinstance(claims, list):
        errors.append("Evidence field must be a list: claims")
    else:
        required_claim_fields = {
            "claim_id",
            "source_id",
            "claim",
            "evidence",
            "source_title",
            "source_url",
            "source_type",
            "confidence",
            "category",
        }
        for index, item in enumerate(claims, start=1):
            if not isinstance(item, dict):
                errors.append(f"Claim {index} must be an object.")
                continue
            for key in sorted(required_claim_fields):
                if key not in item:
                    errors.append(f"Claim {index} missing required field: {key}")
                elif not isinstance(item.get(key), str):
                    errors.append(f"Claim {index} field must be a string: {key}")
            confidence = item.get("confidence")
            if isinstance(confidence, str) and confidence not in {"low", "medium", "high"}:
                errors.append(f"Claim {index} has invalid confidence: {confidence}")

    for key in ("conflicts", "needs_verification"):
        value = data.get(key)
        if not isinstance(value, list):
            errors.append(f"Evidence field must be a list: {key}")
        elif any(not isinstance(item, str) for item in value):
            errors.append(f"Evidence field must contain only strings: {key}")

    return errors


def _evidence_prompt(topic: str, sources: list[SourceRecord]) -> str:
    source_payload = []
    for index, source in enumerate(sources, start=1):
        source_payload.append(
            {
                "source_id": f"S{index:03d}",
                "title": source.title,
                "url": source.url or source.canonical_url,
                "canonical_url": source.canonical_url,
                "doi": source.doi,
                "arxiv_id": source.arxiv_id,
                "source_provider": source.source_provider,
                "source_score": source.source_score,
                "source_type": source.source_type,
                "summary": source.summary,
                "authors": source.authors,
                "published_at": source.published_at,
                "updated_at": source.updated_at,
            }
        )
    return json.dumps(
        {
            "topic": topic,
            "sources": source_payload,
            "task": (
                "Extract evidence claims for structure classification and service blueprint synthesis. "
                "Each claim must be grounded in one source_id from the provided list."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def _json_object(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    candidates = [text.strip()]
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _claim_from_mapping(
    item: dict[str, Any],
    *,
    source_by_id: dict[str, SourceRecord],
    fallback_index: int,
) -> EvidenceClaim | None:
    claim_text = str(item.get("claim", "")).strip()
    if not claim_text:
        return None

    source_id = str(item.get("source_id") or "").strip()
    source = source_by_id.get(source_id)
    if source is None and source_by_id:
        source_id = next(iter(source_by_id))
        source = source_by_id[source_id]

    confidence = str(item.get("confidence") or "medium").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"

    return EvidenceClaim(
        claim_id=str(item.get("claim_id") or f"E{fallback_index:03d}").strip(),
        source_id=source_id or f"S{fallback_index:03d}",
        claim=claim_text,
        evidence=str(item.get("evidence") or "").strip(),
        source_title=str(item.get("source_title") or (source.title if source else "")).strip(),
        source_url=str(item.get("source_url") or ((source.url or source.canonical_url) if source else "")).strip(),
        source_type=str(item.get("source_type") or (source.source_type if source else "")).strip(),
        confidence=confidence,
        category=str(item.get("category") or "general").strip(),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
