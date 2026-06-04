from __future__ import annotations

import logging
from pathlib import Path

from .bilingual_audit import render_bilingual_audit_run_summary, run_bilingual_audit
from .blueprint import stabilize_service_blueprint
from .citations import normalize_source_record
from .collectors import DEFAULT_OFFICIAL_DOC_LIMIT, collect_official_doc_sources, collect_paper_sources, seed_official_sources, seed_standard_sources
from .config import Settings
from .evidence import extract_evidence
from .gemini_client import GeminiError, GeminiGenerateClient, gemini_output_text
from .models import DryRunPlan, PlannedArtifact, RunArtifacts, RunWarning, SafetyCheck, SourceRecord
from .obsidian import ObsidianWriter
from .openai_client import OpenAIError, OpenAIResponsesClient, output_text
from .prompts import synthesis_instructions, synthesis_prompt
from .quality import FAIL, evaluate_quality_gates
from .render import render_evidence_ledger, render_evidence_synthesis_context, render_fallback_blueprint, render_run_note, render_source_note, render_topic_map
from .secrets import ProviderSelection, select_llm_provider
from .textutil import slugify, yaml_scalar
from .timeutil import now_local


logger = logging.getLogger(__name__)
MAX_TOPIC_LENGTH = 500


class QualityGateFailure(RuntimeError):
    def __init__(self, failures):
        self.failures = list(failures)
        detail = "; ".join(f"{failure.name}: {failure.detail}" for failure in self.failures)
        super().__init__(f"Quality gate failure blocked vault write: {detail}")


class ResearchPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.writer = ObsidianWriter(
            settings.obsidian,
            common_module_path=settings.common.module_path,
            use_common_module=settings.common.enabled,
        )

    def run(
        self,
        topic: str,
        *,
        offline: bool = False,
        max_papers_per_source: int = 2,
        rerun_of: str | None = None,
    ) -> RunArtifacts:
        _validate_topic(topic)
        timestamp = now_local(self.settings.app.timezone)
        checked_at = timestamp.date().isoformat()
        date_prefix = timestamp.strftime("%Y-%m-%d")
        slug = slugify(topic)

        self.writer.ensure_structure()
        warnings: list[RunWarning] = []
        sources = [
            normalize_source_record(source)
            for source in self._collect_sources(
                topic,
                offline=offline,
                max_papers_per_source=max_papers_per_source,
                warnings=warnings,
            )
        ]
        sources = self._sort_sources_by_priority(sources)
        provider = select_llm_provider(self.settings)
        evidence = extract_evidence(
            topic,
            sources,
            api_key=provider.api_key,
            model=self._model_for(provider, "extractor"),
            provider=provider.provider,
            offline=offline,
        )

        evidence_filename = f"{date_prefix}_{slug}_evidence-ledger.md"
        evidence_path_preview = self.writer.safe_path(f"{self.settings.obsidian.evidence_dir}/{evidence_filename}")
        evidence_markdown_for_synthesis = render_evidence_synthesis_context(topic, evidence, checked_at=checked_at)

        blueprint_markdown = self._synthesize_blueprint(topic, evidence_markdown_for_synthesis, sources, checked_at, offline)
        quality_gates = evaluate_quality_gates(
            self.settings.quality_gates,
            sources=sources,
            evidence=evidence,
            blueprint_markdown=blueprint_markdown,
            checked_at=checked_at,
            evidence_path=str(evidence_path_preview),
        )
        self._raise_on_quality_gate_failures(quality_gates)

        created_paths: list[Path] = []
        try:
            source_paths: list[Path] = []
            for index, source in enumerate(sources, start=1):
                source_id = f"S{index:03d}"
                filename = f"{date_prefix}_{slug}_source-{index:02d}.md"
                directory = self._source_directory(source)
                source_claims = [claim for claim in evidence.claims if claim.source_id == source_id]
                source_paths.append(
                    self._write_note(
                        created_paths,
                        directory,
                        filename,
                        render_source_note(
                            source,
                            topic=topic,
                            checked_at=checked_at,
                            source_id=source_id,
                            claims=source_claims,
                        ),
                    )
                )

            blueprint_path = self._write_note(
                created_paths,
                self.settings.obsidian.blueprint_dir,
                f"{date_prefix}_{slug}_service-blueprint.md",
                blueprint_markdown,
            )

            evidence_markdown = render_evidence_ledger(
                topic,
                evidence=evidence,
                checked_at=checked_at,
                quality_gates=quality_gates,
            )
            evidence_path = self._write_note(
                created_paths,
                self.settings.obsidian.evidence_dir,
                evidence_filename,
                evidence_markdown,
            )

            topic_map_path = self._write_note(
                created_paths,
                self.settings.obsidian.taxonomy_dir,
                f"{date_prefix}_{slug}_topic-map.md",
                render_topic_map(
                    topic,
                    source_paths=[str(path) for path in source_paths],
                    evidence_path=str(evidence_path),
                    blueprint_path=str(blueprint_path),
                    evidence=evidence,
                    checked_at=checked_at,
                    vault_path=str(self.writer.vault_path),
                    rerun_of=rerun_of,
                ),
            )

            artifact_paths = [*source_paths, evidence_path, blueprint_path, topic_map_path]
            artifact_strings = [str(path) for path in artifact_paths]
            run_filename = f"{date_prefix}_{slug}_run.md"
            bilingual_audit_summary = None
            if self.settings.report.bilingual:
                audit = run_bilingual_audit(
                    self.writer.vault_path,
                    target_paths=artifact_paths,
                )
                bilingual_audit_summary = render_bilingual_audit_run_summary(audit)
            run_markdown = render_run_note(
                topic,
                artifact_strings,
                checked_at=checked_at,
                mode="offline" if offline else provider.provider,
                quality_gates=quality_gates,
                warnings=warnings,
                bilingual_audit=bilingual_audit_summary,
                rerun_of=rerun_of,
            )
            run_path = self._write_note(created_paths, self.settings.obsidian.run_dir, run_filename, run_markdown)
        except Exception:
            self._cleanup_partial_artifacts(created_paths)
            raise

        return RunArtifacts(
            run_note=str(run_path),
            source_notes=[str(path) for path in source_paths],
            evidence_ledger=str(evidence_path),
            service_blueprint=str(blueprint_path),
            topic_map=str(topic_map_path),
        )

    def dry_run(self, topic: str, *, offline: bool = False, max_papers_per_source: int = 2) -> DryRunPlan:
        _validate_topic(topic)
        timestamp = now_local(self.settings.app.timezone)
        date_prefix = timestamp.strftime("%Y-%m-%d")
        slug = slugify(topic)

        artifacts: list[PlannedArtifact] = []
        sources = self._sort_sources_by_priority(
            self._planned_sources(topic, offline=offline, max_papers_per_source=max_papers_per_source)
        )
        for index, source in enumerate(sources, start=1):
            filename = f"{date_prefix}_{slug}_source-{index:02d}.md"
            directory = self._source_directory(source)
            status = "dynamic" if source.source_type in {"official-docs", "papers"} and source.url == "" else "planned"
            if source.source_type == "official-docs" and status == "dynamic":
                note = "exact official documentation URL depends on selected provider search results"
            elif source.source_type == "papers" and status == "dynamic":
                note = "exact paper metadata depends on collector results"
            else:
                note = ""
            artifacts.append(
                PlannedArtifact(
                    path=str(self.writer.safe_path(f"{directory}/{filename}")),
                    kind="source-note",
                    status=status,
                    note=note,
                )
            )

        artifacts.extend(
            [
                PlannedArtifact(
                    path=str(self.writer.safe_path(f"{self.settings.obsidian.evidence_dir}/{date_prefix}_{slug}_evidence-ledger.md")),
                    kind="evidence-ledger",
                ),
                PlannedArtifact(
                    path=str(self.writer.safe_path(f"{self.settings.obsidian.blueprint_dir}/{date_prefix}_{slug}_service-blueprint.md")),
                    kind="service-blueprint",
                ),
                PlannedArtifact(
                    path=str(self.writer.safe_path(f"{self.settings.obsidian.taxonomy_dir}/{date_prefix}_{slug}_topic-map.md")),
                    kind="topic-map",
                ),
                PlannedArtifact(
                    path=str(self.writer.safe_path(f"{self.settings.obsidian.run_dir}/{date_prefix}_{slug}_run.md")),
                    kind="run-log",
                ),
            ]
        )

        return DryRunPlan(
            topic=topic,
            vault_path=str(self.writer.vault_path),
            mode="offline" if offline else select_llm_provider(self.settings).provider,
            artifacts=artifacts,
            safety=self._dry_run_safety(offline=offline),
        )

    def _collect_sources(
        self,
        topic: str,
        *,
        offline: bool,
        max_papers_per_source: int,
        warnings: list[RunWarning] | None = None,
    ) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        provider = select_llm_provider(self.settings)
        if offline:
            records.extend(seed_official_sources(topic, self.settings.sources))
        else:
            records.extend(
                collect_official_doc_sources(
                    topic,
                    self.settings.sources,
                    api_key=provider.api_key,
                    model=self._model_for(provider, "planner"),
                    provider=provider.provider,
                )
            )
        records.extend(seed_standard_sources(topic, self.settings.sources))
        if not offline:
            records.extend(
                collect_paper_sources(
                    topic,
                    self.settings.sources.paper_sources,
                    limit_each=max_papers_per_source,
                    warnings=warnings,
                )
            )
        return records

    def _planned_sources(self, topic: str, *, offline: bool, max_papers_per_source: int) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        provider = select_llm_provider(self.settings)
        if offline or not provider.available:
            records.extend(seed_official_sources(topic, self.settings.sources))
        else:
            official_count = min(DEFAULT_OFFICIAL_DOC_LIMIT, max(1, len(self.settings.sources.official_doc_domains)))
            for index in range(official_count):
                records.append(
                    SourceRecord(
                        title=f"official docs search result {index + 1}",
                        url="",
                        source_type="official-docs",
                        summary="Dynamic official documentation result placeholder for dry-run planning.",
                    )
                )
        records.extend(seed_standard_sources(topic, self.settings.sources))
        if not offline:
            for paper_source in self.settings.sources.paper_sources:
                for index in range(max(0, max_papers_per_source)):
                    records.append(
                        SourceRecord(
                            title=f"{paper_source} paper result {index + 1}",
                            url="",
                            source_type="papers",
                            summary="Dynamic paper metadata placeholder for dry-run planning.",
                        )
                    )
        return records

    def _dry_run_safety(self, *, offline: bool) -> list[SafetyCheck]:
        checks = [
            SafetyCheck("OK", "dry-run", "no files will be written"),
            SafetyCheck("OK", "overwrite policy", "reviewed/evergreen notes are protected by writer policy"),
        ]
        if offline:
            checks.extend(
                [
                    SafetyCheck("OK", "llm provider", "offline mode; no LLM provider will be called"),
                    SafetyCheck("OK", "evidence extraction", "offline mode; deterministic fallback evidence will be used"),
                    SafetyCheck("OK", "paper collectors", "offline mode; network collectors will not run"),
                ]
            )
        else:
            provider = select_llm_provider(self.settings)
            search_tool = "OpenAI web_search" if provider.provider == "openai" else "Gemini Google Search"
            checks.append(
                SafetyCheck(
                    "OK" if provider.available else "WARN",
                    "official docs",
                    f"{search_tool} will be used against configured official domains"
                    if provider.available
                    else "API key not configured; official docs will fall back to seed domains",
                )
            )
            checks.append(
                SafetyCheck(
                    "OK" if provider.available else "WARN",
                    "llm provider",
                    f"{provider.provider} selected; synthesis can call {provider.provider}"
                    if provider.available
                    else "No supported API key configured; fallback blueprint will be used",
                )
            )
            checks.append(
                SafetyCheck(
                    "OK" if provider.available else "WARN",
                    "evidence extraction",
                    f"structured JSON extraction can call {provider.provider}"
                    if provider.available
                    else "API key not configured; fallback evidence extraction will be used",
                )
            )
            checks.append(
                SafetyCheck(
                    "WARN",
                    "paper collectors",
                    "paper source note count is an upper-bound preview; exact results depend on arXiv/Semantic Scholar/Crossref/OpenAlex",
                )
            )
        return checks

    def _synthesize_blueprint(
        self,
        topic: str,
        evidence_markdown: str,
        sources: list[SourceRecord],
        checked_at: str,
        offline: bool,
    ) -> str:
        if offline:
            return render_fallback_blueprint(
                topic,
                sources,
                checked_at=checked_at,
                bilingual=self.settings.report.bilingual,
                source_priority=self.settings.sources.priority,
            )

        provider = select_llm_provider(self.settings)
        if not provider.available:
            return render_fallback_blueprint(
                topic,
                sources,
                checked_at=checked_at,
                bilingual=self.settings.report.bilingual,
                source_priority=self.settings.sources.priority,
            )

        if provider.provider == "gemini":
            return self._synthesize_blueprint_with_gemini(topic, evidence_markdown, sources, checked_at, provider)
        return self._synthesize_blueprint_with_openai(topic, evidence_markdown, sources, checked_at, provider)

    def _synthesize_blueprint_with_openai(
        self,
        topic: str,
        evidence_markdown: str,
        sources: list[SourceRecord],
        checked_at: str,
        provider: ProviderSelection,
    ) -> str:
        client = OpenAIResponsesClient(
            api_key=provider.api_key or "",
            default_model=self.settings.openai.models.synthesis,
        )
        try:
            response = client.create(
                input_text=synthesis_prompt(topic, evidence_markdown, bilingual=self.settings.report.bilingual),
                instructions=synthesis_instructions(bilingual=self.settings.report.bilingual),
                reasoning_effort="medium",
            )
            markdown = output_text(response)
            if markdown.strip():
                stable_markdown = stabilize_service_blueprint(markdown, topic=topic, bilingual=self.settings.report.bilingual)
                return self._with_frontmatter(topic, stable_markdown, checked_at=checked_at)
        except OpenAIError as exc:
            logger.warning(
                "service blueprint synthesis failed; using fallback blueprint",
                extra={"stage": "synthesize_blueprint", "provider": "openai", "topic": topic, "error": str(exc)},
            )
            return render_fallback_blueprint(
                topic,
                sources,
                checked_at=checked_at,
                bilingual=self.settings.report.bilingual,
                source_priority=self.settings.sources.priority,
            )
        logger.warning(
            "service blueprint synthesis returned empty markdown; using fallback blueprint",
            extra={"stage": "synthesize_blueprint", "provider": "openai", "topic": topic},
        )
        return render_fallback_blueprint(
            topic,
            sources,
            checked_at=checked_at,
            bilingual=self.settings.report.bilingual,
            source_priority=self.settings.sources.priority,
        )

    def _synthesize_blueprint_with_gemini(
        self,
        topic: str,
        evidence_markdown: str,
        sources: list[SourceRecord],
        checked_at: str,
        provider: ProviderSelection,
    ) -> str:
        client = GeminiGenerateClient(
            api_key=provider.api_key or "",
            default_model=self.settings.gemini.models.synthesis,
        )
        try:
            response = client.generate(
                input_text=synthesis_prompt(topic, evidence_markdown, bilingual=self.settings.report.bilingual),
                instructions=synthesis_instructions(bilingual=self.settings.report.bilingual),
                model=self.settings.gemini.models.synthesis,
            )
            markdown = gemini_output_text(response)
            if markdown.strip():
                stable_markdown = stabilize_service_blueprint(markdown, topic=topic, bilingual=self.settings.report.bilingual)
                return self._with_frontmatter(topic, stable_markdown, checked_at=checked_at)
        except GeminiError as exc:
            logger.warning(
                "service blueprint synthesis failed; using fallback blueprint",
                extra={"stage": "synthesize_blueprint", "provider": "gemini", "topic": topic, "error": str(exc)},
            )
            return render_fallback_blueprint(
                topic,
                sources,
                checked_at=checked_at,
                bilingual=self.settings.report.bilingual,
                source_priority=self.settings.sources.priority,
            )
        logger.warning(
            "service blueprint synthesis returned empty markdown; using fallback blueprint",
            extra={"stage": "synthesize_blueprint", "provider": "gemini", "topic": topic},
        )
        return render_fallback_blueprint(
            topic,
            sources,
            checked_at=checked_at,
            bilingual=self.settings.report.bilingual,
            source_priority=self.settings.sources.priority,
        )

    def _model_for(self, provider: ProviderSelection, role: str) -> str:
        if provider.provider == "gemini":
            return str(getattr(self.settings.gemini.models, role))
        return str(getattr(self.settings.openai.models, role))

    def _with_frontmatter(self, topic: str, markdown: str, *, checked_at: str) -> str:
        body = markdown.strip()
        if body.startswith("---\n"):
            return body
        return f"""---
type: service-blueprint
topic: {yaml_scalar(topic)}
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
confidence: medium
source_priority:
{self._source_priority_frontmatter()}
generated_by: research-agent
{self._language_frontmatter()}
---

{body}
"""

    def _source_directory(self, source: SourceRecord) -> str:
        source_dir = self.settings.obsidian.source_dir
        if source.source_type == "official-docs":
            return f"{source_dir}/official-docs"
        if source.source_type == "standards":
            return f"{source_dir}/standards"
        if source.source_type == "papers":
            return f"{source_dir}/papers"
        return f"{source_dir}/web"

    def _sort_sources_by_priority(self, sources: list[SourceRecord]) -> list[SourceRecord]:
        order = {source_type: index for index, source_type in enumerate(self.settings.sources.priority)}
        fallback = len(order)
        return [
            source
            for _, source in sorted(
                enumerate(sources),
                key=lambda item: (order.get(item[1].source_type, fallback), item[0]),
            )
        ]

    def _source_priority_frontmatter(self) -> str:
        priority = [item for item in self.settings.sources.priority if item.strip()]
        if not priority:
            priority = ["official-docs", "standards", "papers"]
        return "\n".join(f"  - {yaml_scalar(item)}" for item in priority)

    def _language_frontmatter(self) -> str:
        if self.settings.report.bilingual:
            return "language: bilingual\noriginal_language: en\ntranslation_language: ko"
        return "language: en"

    def _raise_on_quality_gate_failures(self, quality_gates) -> None:
        if not self.settings.quality_gates.block_vault_write_on_fail:
            return
        failures = [gate for gate in quality_gates if gate.status == FAIL]
        if failures:
            raise QualityGateFailure(failures)

    def _write_note(
        self,
        created_paths: list[Path],
        directory: str,
        filename: str,
        markdown: str,
        *,
        allow_overwrite: bool = False,
    ) -> Path:
        path = self.writer.write_note(directory, filename, markdown, allow_overwrite=allow_overwrite)
        created_paths.append(path)
        return path

    def _cleanup_partial_artifacts(self, created_paths: list[Path]) -> None:
        if not self.settings.pipeline.cleanup_partial_artifacts:
            return
        for path in reversed(created_paths):
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError as exc:
                logger.warning(
                    "partial artifact cleanup failed",
                    extra={"stage": "partial_cleanup", "file_path": str(path), "error": str(exc)},
                )


def _validate_topic(topic: str) -> None:
    if not topic.strip():
        raise ValueError("Research topic must not be blank")
    if len(topic) > MAX_TOPIC_LENGTH:
        raise ValueError(f"Research topic must be {MAX_TOPIC_LENGTH} characters or fewer")
