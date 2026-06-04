from __future__ import annotations

import os
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common_modules import configure_common_modules


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSettings:
    name: str = "obsidian-research-agent"
    timezone: str = "Asia/Seoul"

    def __post_init__(self) -> None:
        _validate_timezone(self.timezone)


@dataclass(frozen=True)
class ObsidianSettings:
    vault_path: Path
    draft_dir: str = "00_Inbox"
    source_dir: str = "10_Sources"
    taxonomy_dir: str = "20_Taxonomy"
    blueprint_dir: str = "30_Service-Blueprints"
    final_report_dir: str = "40_Final-Reports"
    evidence_dir: str = "50_Evidence-Ledger"
    run_dir: str = "60_Runs"
    overwrite_reviewed_notes: bool = False


@dataclass(frozen=True)
class ModelSettings:
    planner: str = "gpt-4o-mini"
    extractor: str = "gpt-4o-mini"
    classifier: str = "gpt-4o-mini"
    synthesis: str = "gpt-4o"
    cheap_triage: str = "gpt-4o-mini"


@dataclass(frozen=True)
class OpenAISettings:
    api_key_env: str = "OPENAI_API_KEY"
    models: ModelSettings = field(default_factory=ModelSettings)

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


@dataclass(frozen=True)
class GeminiModelSettings:
    planner: str = "gemini-2.5-flash"
    extractor: str = "gemini-2.5-flash"
    classifier: str = "gemini-2.5-flash"
    synthesis: str = "gemini-2.5-flash"
    cheap_triage: str = "gemini-2.5-flash"


@dataclass(frozen=True)
class GeminiSettings:
    api_key_env: str = "GEMINI_API_KEY"
    google_api_key_env: str = "GOOGLE_API_KEY"
    models: GeminiModelSettings = field(default_factory=GeminiModelSettings)


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "auto"


@dataclass(frozen=True)
class CommonModuleSettings:
    enabled: bool = True
    module_path: Path | None = None


@dataclass(frozen=True)
class SourceSettings:
    priority: list[str] = field(default_factory=lambda: [
        "official-docs",
        "standards",
        "papers",
        "api-metadata",
        "engineering-articles",
        "general-web",
    ])
    official_doc_domains: list[str] = field(default_factory=list)
    standards_domains: list[str] = field(default_factory=list)
    paper_sources: list[str] = field(default_factory=lambda: ["arxiv", "semantic-scholar", "crossref", "openalex"])


@dataclass(frozen=True)
class QualityGateSettings:
    min_official_sources: int = 2
    require_checked_at: bool = True
    require_source_urls: bool = True
    require_evidence_ledger: bool = True
    require_uncertainty_section: bool = True
    block_vault_write_on_fail: bool = True


@dataclass(frozen=True)
class ReportSettings:
    bilingual: bool = True


@dataclass(frozen=True)
class PipelineSettings:
    cleanup_partial_artifacts: bool = True


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    obsidian: ObsidianSettings
    openai: OpenAISettings
    sources: SourceSettings
    quality_gates: QualityGateSettings
    llm: LLMSettings = field(default_factory=LLMSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    common: CommonModuleSettings = field(default_factory=CommonModuleSettings)
    report: ReportSettings = field(default_factory=ReportSettings)
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)


def load_dotenv(path: Path, *, override: bool = False, common_module_path: Path | None = None) -> None:
    if not path.exists():
        return
    values = _dotenv_values(path, common_module_path=common_module_path)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value


def load_settings(path: Path, *, vault_override: Path | None = None, provider_override: str | None = None) -> Settings:
    data = _read_toml(path)
    project_root = _project_root_for_config(path)

    app_data = data.get("app", {})
    llm_data = data.get("llm", {})
    common_data = data.get("common_modules", {})
    obsidian_data = data.get("obsidian", {})
    openai_data = data.get("openai", {})
    model_data = openai_data.get("models", {})
    gemini_data = data.get("gemini", {})
    gemini_model_data = gemini_data.get("models", {})
    source_data = data.get("sources", {})
    gate_data = data.get("quality_gates", {})
    report_data = data.get("report", data.get("reports", {}))
    pipeline_data = data.get("pipeline", {})

    vault_path = vault_override or Path(obsidian_data.get("vault_path", "Research"))
    common = CommonModuleSettings(
        enabled=bool(common_data.get("enabled", True)),
        module_path=_resolve_optional_path(
            os.environ.get("RESEARCH_AGENT_COMMON_MODULE_PATH")
            or common_data.get("module_path")
            or "../Common Module/src",
            project_root=project_root,
        ),
    )
    obsidian = ObsidianSettings(
        vault_path=vault_path.expanduser(),
        draft_dir=str(obsidian_data.get("draft_dir", "00_Inbox")),
        source_dir=str(obsidian_data.get("source_dir", "10_Sources")),
        taxonomy_dir=str(obsidian_data.get("taxonomy_dir", "20_Taxonomy")),
        blueprint_dir=str(obsidian_data.get("blueprint_dir", "30_Service-Blueprints")),
        final_report_dir=str(obsidian_data.get("final_report_dir", "40_Final-Reports")),
        evidence_dir=str(obsidian_data.get("evidence_dir", "50_Evidence-Ledger")),
        run_dir=str(obsidian_data.get("run_dir", "60_Runs")),
        overwrite_reviewed_notes=bool(obsidian_data.get("overwrite_reviewed_notes", False)),
    )

    return Settings(
        app=AppSettings(
            name=str(app_data.get("name", "obsidian-research-agent")),
            timezone=str(app_data.get("timezone", "Asia/Seoul")),
        ),
        llm=LLMSettings(
            provider=str(provider_override or llm_data.get("provider", "auto")),
        ),
        obsidian=obsidian,
        openai=OpenAISettings(
            api_key_env=str(openai_data.get("api_key_env", "OPENAI_API_KEY")),
            models=ModelSettings(
                planner=str(model_data.get("planner", "gpt-4o-mini")),
                extractor=str(model_data.get("extractor", "gpt-4o-mini")),
                classifier=str(model_data.get("classifier", "gpt-4o-mini")),
                synthesis=str(model_data.get("synthesis", "gpt-4o")),
                cheap_triage=str(model_data.get("cheap_triage", "gpt-4o-mini")),
            ),
        ),
        gemini=GeminiSettings(
            api_key_env=str(gemini_data.get("api_key_env", "GEMINI_API_KEY")),
            google_api_key_env=str(gemini_data.get("google_api_key_env", "GOOGLE_API_KEY")),
            models=GeminiModelSettings(
                planner=str(gemini_model_data.get("planner", "gemini-2.5-flash")),
                extractor=str(gemini_model_data.get("extractor", "gemini-2.5-flash")),
                classifier=str(gemini_model_data.get("classifier", "gemini-2.5-flash")),
                synthesis=str(gemini_model_data.get("synthesis", "gemini-2.5-flash")),
                cheap_triage=str(gemini_model_data.get("cheap_triage", "gemini-2.5-flash")),
            ),
        ),
        sources=SourceSettings(
            priority=_str_list(source_data.get("priority"), SourceSettings().priority),
            official_doc_domains=_str_list(source_data.get("official_doc_domains"), []),
            standards_domains=_str_list(source_data.get("standards_domains"), []),
            paper_sources=_str_list(source_data.get("paper_sources"), ["arxiv", "semantic-scholar", "crossref", "openalex"]),
        ),
        quality_gates=QualityGateSettings(
            min_official_sources=int(gate_data.get("min_official_sources", 2)),
            require_checked_at=bool(gate_data.get("require_checked_at", True)),
            require_source_urls=bool(gate_data.get("require_source_urls", True)),
            require_evidence_ledger=bool(gate_data.get("require_evidence_ledger", True)),
            require_uncertainty_section=bool(gate_data.get("require_uncertainty_section", True)),
            block_vault_write_on_fail=bool(gate_data.get("block_vault_write_on_fail", True)),
        ),
        common=common,
        report=ReportSettings(
            bilingual=bool(report_data.get("bilingual", True)),
        ),
        pipeline=PipelineSettings(
            cleanup_partial_artifacts=bool(pipeline_data.get("cleanup_partial_artifacts", True)),
        ),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _str_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, list):
        return list(default)
    return [str(item) for item in value]


def _dotenv_values(path: Path, *, common_module_path: Path | None) -> dict[str, str]:
    status = configure_common_modules(common_module_path)
    if status.llm_key_manager:
        try:
            from llm_key_manager.config import parse_dotenv

            return parse_dotenv(path)
        except Exception as exc:
            logger.warning(
                "common dotenv parser failed; using built-in dotenv parser",
                extra={"stage": "load_dotenv", "file_path": str(path), "error": str(exc)},
            )

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _project_root_for_config(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.parent.name == "config":
        return resolved.parent.parent
    return resolved.parent


def _resolve_optional_path(value: Any, *, project_root: Path) -> Path | None:
    if value is None:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _validate_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {timezone}") from exc
