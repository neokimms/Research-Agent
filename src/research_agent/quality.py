from __future__ import annotations

import re
import unicodedata

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
    topic: str = "",
) -> list[QualityGateResult]:
    gates = [
        _official_sources_gate(settings, sources),
        _source_urls_gate(settings, sources),
        _source_relevance_gate(settings, sources, topic),
        _checked_at_gate(settings, checked_at),
        _evidence_ledger_gate(settings, evidence, evidence_path),
        _fallback_evidence_gate(settings, evidence),
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


def _fallback_evidence_gate(settings: QualityGateSettings, evidence: EvidenceBundle) -> QualityGateResult | None:
    if not settings.fail_on_fallback_evidence:
        return None
    if evidence.extraction_mode != "fallback":
        return QualityGateResult(PASS, "structured evidence extraction", f"evidence extraction mode is {evidence.extraction_mode}")
    return QualityGateResult(FAIL, "structured evidence extraction", "evidence extraction used fallback source summaries")


def _source_relevance_gate(
    settings: QualityGateSettings,
    sources: list[SourceRecord],
    topic: str,
) -> QualityGateResult | None:
    required_count = max(0, settings.min_relevant_sources)
    required_ratio = max(0.0, min(1.0, settings.min_relevant_source_ratio))
    if required_count == 0 and required_ratio == 0.0:
        return None
    if not topic.strip():
        return QualityGateResult(FAIL, "source relevance", "topic is missing; source relevance cannot be evaluated")
    if not sources:
        return QualityGateResult(FAIL, "source relevance", "no sources collected; required topic-relevant sources")

    scored = [(source, source_relevance_score(topic, source)) for source in sources]
    relevant = [(source, score) for source, score in scored if score >= 0.22]
    ratio = len(relevant) / len(sources)
    required_by_ratio = int(len(sources) * required_ratio + 0.999999) if required_ratio else 0
    required = max(required_count, required_by_ratio)
    if len(relevant) >= required:
        return QualityGateResult(
            PASS,
            "source relevance",
            f"{len(relevant)}/{len(sources)} sources appear topic-relevant; required {required}",
        )

    weak = [f"{source.title} ({score:.2f})" for source, score in scored if score < 0.22]
    preview = ", ".join(weak[:3])
    suffix = f"; plus {len(weak) - 3} more" if len(weak) > 3 else ""
    return QualityGateResult(
        FAIL,
        "source relevance",
        f"{len(relevant)}/{len(sources)} sources appear topic-relevant; required {required}. Weak matches: {preview}{suffix}",
    )


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


def source_relevance_score(topic: str, source: SourceRecord) -> float:
    if source.source_provider == "seed":
        return 0.0
    concepts = _topic_concepts(topic)
    if not concepts:
        return 0.0
    haystack = _normalized_text(" ".join([source.title, source.summary, source.url, source.canonical_url]))
    matched = 0
    for aliases in concepts:
        if any(_term_in_text(alias, haystack) for alias in aliases):
            matched += 1
    score = matched / len(concepts)
    anchors = _anchor_concepts(concepts)
    if anchors and not any(any(_term_in_text(alias, haystack) for alias in aliases) for aliases in anchors):
        score = min(score, 0.12)
    if _normalized_text(topic) and _normalized_text(topic) in haystack:
        score = max(score, 0.5)
    return score


def _topic_concepts(topic: str) -> list[set[str]]:
    concepts: list[set[str]] = []
    for token in _tokens(topic):
        aliases = {token}
        aliases.update(_ALIASES.get(token, set()))
        if token == "x" and any(item in _normalized_text(topic) for item in {"스페이스 x", "space x"}):
            aliases.add("spacex")
        if token == "스페이스":
            aliases.update({"spacex", "space"})
        concepts.append(aliases)
    if "스페이스 x" in _normalized_text(topic) or "space x" in _normalized_text(topic):
        concepts.insert(0, {"spacex", "space x", "space exploration technologies"})
    unique: list[set[str]] = []
    seen: set[tuple[str, ...]] = set()
    for aliases in concepts:
        key = tuple(sorted(aliases))
        if key not in seen:
            seen.add(key)
            unique.append(aliases)
    return unique[:12]


def _anchor_concepts(concepts: list[set[str]]) -> list[set[str]]:
    anchors: list[set[str]] = []
    for aliases in concepts:
        if aliases & _GENERIC_CONCEPTS:
            continue
        longest = max((len(alias) for alias in aliases), default=0)
        if longest >= 4:
            anchors.append(aliases)
    return anchors[:3]


def _tokens(text: str) -> list[str]:
    normalized = _normalized_text(text)
    raw_tokens = re.findall(r"[a-z0-9]+|[가-힣]+", normalized)
    tokens: list[str] = []
    for raw in raw_tokens:
        token = _strip_korean_particle(raw)
        if token in _STOPWORDS:
            continue
        if len(token) < 2 and token != "x":
            continue
        tokens.append(token)
    return tokens


def _strip_korean_particle(token: str) -> str:
    for suffix in ("으로", "에서", "에게", "까지", "부터", "처럼", "보다", "으로", "로", "에", "의", "을", "를", "은", "는", "이", "가", "와", "과"):
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            return token[: -len(suffix)]
    return token


def _normalized_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _term_in_text(term: str, text: str) -> bool:
    needle = _normalized_text(term).strip()
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9]+", needle):
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text) is not None
    return needle in text


_STOPWORDS = {
    "관련",
    "대해",
    "대한",
    "조사",
    "조사해",
    "주세요",
    "정보",
    "영향",
    "동향",
    "비교",
    "분석",
    "따른",
    "및",
    "the",
    "and",
    "for",
    "with",
    "about",
    "research",
    "analysis",
    "report",
}

_ALIASES = {
    "상장": {"ipo", "listing", "public offering"},
    "주식": {"stock", "stocks", "share", "shares", "equity"},
    "시장": {"market", "markets"},
    "변동성": {"volatility", "volatile"},
    "경쟁사": {"competitor", "competitors", "competition", "rival", "rivals"},
    "국내": {"korea", "korean", "domestic"},
    "증권": {"securities", "brokerage"},
    "실적": {"earnings", "revenue", "financial results"},
    "매출": {"revenue", "sales"},
    "기업": {"company", "corporate"},
}

_GENERIC_CONCEPTS = {
    "ipo",
    "listing",
    "public offering",
    "stock",
    "stocks",
    "share",
    "shares",
    "equity",
    "market",
    "markets",
    "volatility",
    "competitor",
    "competitors",
    "competition",
    "rival",
    "rivals",
    "korea",
    "korean",
    "domestic",
}
