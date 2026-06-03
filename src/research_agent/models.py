from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceRecord:
    title: str
    url: str
    source_type: str
    summary: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: str = ""
    updated_at: str = ""
    raw_text: str = ""
    doi: str = ""
    arxiv_id: str = ""
    source_provider: str = ""
    canonical_url: str = ""
    source_score: float = 0.0


@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    source_id: str
    claim: str
    evidence: str
    source_title: str
    source_url: str
    source_type: str
    confidence: str
    category: str

    def __post_init__(self) -> None:
        if self.confidence not in {"low", "medium", "high"}:
            raise ValueError("EvidenceClaim.confidence must be one of: low, medium, high")


@dataclass(frozen=True)
class EvidenceBundle:
    claims: list[EvidenceClaim]
    conflicts: list[str] = field(default_factory=list)
    needs_verification: list[str] = field(default_factory=list)
    extraction_mode: str = "fallback"


@dataclass(frozen=True)
class QualityGateResult:
    status: str
    name: str
    detail: str


@dataclass(frozen=True)
class RunWarning:
    category: str
    source: str
    detail: str


@dataclass(frozen=True)
class RunArtifacts:
    run_note: str
    source_notes: list[str]
    evidence_ledger: str
    service_blueprint: str
    topic_map: str


@dataclass(frozen=True)
class PlannedArtifact:
    path: str
    kind: str
    status: str = "planned"
    note: str = ""


@dataclass(frozen=True)
class SafetyCheck:
    status: str
    name: str
    detail: str


@dataclass(frozen=True)
class DryRunPlan:
    topic: str
    vault_path: str
    mode: str
    artifacts: list[PlannedArtifact]
    safety: list[SafetyCheck]
