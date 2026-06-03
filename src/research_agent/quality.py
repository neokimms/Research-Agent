from __future__ import annotations

import re

from .config import QualityGateSettings
from .models import EvidenceBundle, QualityGateResult, SourceRecord


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


def evaluate_quality_gates(
    settings: QualityGateSettings,
    *,
    sources: list[SourceRecord],
    evidence: EvidenceBundle,
    blueprint_markdown: str,
    checked_at: str,
    evidence_path: str,
) -> list[QualityGateResult]:
    gates = [
        _official_sources_gate(settings, sources),
        _source_urls_gate(settings, sources),
        _checked_at_gate(settings, checked_at),
        _evidence_ledger_gate(settings, evidence, evidence_path),
        _uncertainty_section_gate(settings, blueprint_markdown),
    ]
    return [gate for gate in gates if gate is not None]


def _official_sources_gate(
    settings: QualityGateSettings,
    sources: list[SourceRecord],
) -> QualityGateResult | None:
    required = max(0, settings.min_official_sources)
    if required == 0:
        return QualityGateResult(PASS, "min official sources", "disabled; no minimum official source count configured")

    count = sum(1 for source in sources if source.source_type == "official-docs" and _traceable_url(source))
    if count >= required:
        return QualityGateResult(PASS, "min official sources", f"{count} official documentation sources found; required {required}")
    return QualityGateResult(FAIL, "min official sources", f"{count} official documentation sources found; required {required}")


def _source_urls_gate(
    settings: QualityGateSettings,
    sources: list[SourceRecord],
) -> QualityGateResult | None:
    if not settings.require_source_urls:
        return QualityGateResult(PASS, "source urls", "disabled by configuration")

    missing = [source.title for source in sources if not _traceable_url(source)]
    if not missing:
        return QualityGateResult(PASS, "source urls", f"all {len(sources)} source records include URLs")

    preview = ", ".join(missing[:3])
    suffix = f"; plus {len(missing) - 3} more" if len(missing) > 3 else ""
    return QualityGateResult(FAIL, "source urls", f"{len(missing)} source records are missing URLs: {preview}{suffix}")


def _checked_at_gate(settings: QualityGateSettings, checked_at: str) -> QualityGateResult | None:
    if not settings.require_checked_at:
        return QualityGateResult(PASS, "checked_at", "disabled by configuration")
    if checked_at.strip():
        return QualityGateResult(PASS, "checked_at", f"run checked_at is {checked_at}")
    return QualityGateResult(FAIL, "checked_at", "run checked_at is missing")


def _evidence_ledger_gate(
    settings: QualityGateSettings,
    evidence: EvidenceBundle,
    evidence_path: str,
) -> QualityGateResult | None:
    if not settings.require_evidence_ledger:
        return QualityGateResult(PASS, "evidence ledger", "disabled by configuration")
    if evidence.claims and evidence_path.strip():
        return QualityGateResult(PASS, "evidence ledger", f"{len(evidence.claims)} evidence claims will be written")
    return QualityGateResult(FAIL, "evidence ledger", "no evidence claims are available for the ledger")


def _uncertainty_section_gate(
    settings: QualityGateSettings,
    blueprint_markdown: str,
) -> QualityGateResult | None:
    if not settings.require_uncertainty_section:
        return QualityGateResult(PASS, "uncertainty section", "disabled by configuration")
    if _section_has_content(blueprint_markdown, "Still Uncertain"):
        return QualityGateResult(PASS, "uncertainty section", "service blueprint includes a non-empty Still Uncertain section")
    return QualityGateResult(FAIL, "uncertainty section", "service blueprint is missing a non-empty Still Uncertain section")


def _section_has_content(markdown: str, heading: str) -> bool:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return False
    content = match.group(1).strip()
    return bool(content and content not in {"-", "- TBD"})


def _traceable_url(source: SourceRecord) -> bool:
    return bool((source.url or source.canonical_url or "").strip())
