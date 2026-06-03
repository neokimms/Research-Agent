from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import (
    AppSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    ReportSettings,
    Settings,
    SourceSettings,
)
from research_agent.models import SourceRecord
from research_agent.pipeline import ResearchPipeline


class PipelineTests(unittest.TestCase):
    def test_offline_run_writes_expected_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(),
                sources=SourceSettings(
                    official_doc_domains=["developers.openai.com", "docs.langchain.com"],
                    standards_domains=["nist.gov"],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(),
            )
            artifacts = ResearchPipeline(settings).run("agentic RAG", offline=True)
            self.assertTrue(Path(artifacts.run_note).exists())
            self.assertTrue(Path(artifacts.evidence_ledger).exists())
            self.assertTrue(Path(artifacts.service_blueprint).exists())
            self.assertTrue(Path(artifacts.topic_map).exists())
            self.assertGreaterEqual(len(artifacts.source_notes), 3)
            first_source_note = Path(artifacts.source_notes[0]).read_text(encoding="utf-8")
            self.assertIn('source_id: "S001"', first_source_note)
            self.assertIn("language: bilingual", first_source_note)
            self.assertIn("## Important Claims", first_source_note)
            self.assertIn("**원본**", first_source_note)
            self.assertIn("**한국어 번역**", first_source_note)
            self.assertIn("E001", first_source_note)
            run_note = Path(artifacts.run_note).read_text(encoding="utf-8")
            self.assertIn('mode: "offline"', run_note)
            self.assertIn("## Mode", run_note)
            self.assertIn("**원본**\n\noffline", run_note)
            self.assertIn("**한국어 번역**\n\n오프라인", run_note)
            self.assertIn("## Quality Gates", run_note)
            self.assertIn("| PASS | min official sources |", run_note)
            self.assertIn("## Bilingual Audit", run_note)
            self.assertIn("Bilingual audit status: PASS", run_note)
            self.assertIn("한글 병기 점검 상태: 통과", run_note)
            evidence_ledger = Path(artifacts.evidence_ledger).read_text(encoding="utf-8")
            self.assertIn("translation_language: ko", evidence_ledger)
            self.assertIn("## Claim Translations", evidence_ledger)
            self.assertIn("## Quality Gates", evidence_ledger)
            self.assertIn("| PASS | evidence ledger |", evidence_ledger)
            topic_map = Path(artifacts.topic_map).read_text(encoding="utf-8")
            self.assertIn("type: topic-map", topic_map)
            self.assertIn("**한국어 번역**", topic_map)
            self.assertIn("[[30_Service-Blueprints/", topic_map)

    def test_run_note_records_selected_gemini_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(),
                sources=SourceSettings(
                    official_doc_domains=[],
                    standards_domains=[],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(),
            )

            with patch.dict("os.environ", {"GEMINI_API_KEY": "gemini-test-key"}, clear=True):
                with patch.object(ResearchPipeline, "_collect_sources", return_value=[]):
                    with patch.object(
                        ResearchPipeline,
                        "_synthesize_blueprint",
                        return_value="---\ntype: service-blueprint\n---\n# Blueprint\n",
                    ):
                        artifacts = ResearchPipeline(settings).run("gemini provider run", offline=False)

            run_note = Path(artifacts.run_note).read_text(encoding="utf-8")
            self.assertIn('mode: "gemini"', run_note)
            self.assertIn("**원본**\n\ngemini", run_note)
            self.assertIn("**한국어 번역**\n\nGemini", run_note)

    def test_run_outputs_record_rerun_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(),
                sources=SourceSettings(
                    official_doc_domains=["developers.openai.com"],
                    standards_domains=[],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(),
            )

            artifacts = ResearchPipeline(settings).run("agentic RAG rerun", offline=True, rerun_of="failed-source")

            run_note = Path(artifacts.run_note).read_text(encoding="utf-8")
            topic_map = Path(artifacts.topic_map).read_text(encoding="utf-8")
            for markdown in (run_note, topic_map):
                self.assertIn('rerun_of: "failed-source"', markdown)
                self.assertIn("## Run Lineage", markdown)
                self.assertIn("- Re-run of portal job `failed-source`.", markdown)
                self.assertIn("- 포털 작업 `failed-source`의 재실행입니다.", markdown)

    def test_run_note_is_written_once_after_bilingual_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(),
                sources=SourceSettings(
                    official_doc_domains=["developers.openai.com"],
                    standards_domains=[],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(),
            )
            pipeline = ResearchPipeline(settings)
            original_write_note = pipeline.writer.write_note
            run_writes = []

            def tracking_write_note(directory, filename, markdown, *, allow_overwrite=False):
                if directory == settings.obsidian.run_dir:
                    run_writes.append((filename, allow_overwrite))
                return original_write_note(directory, filename, markdown, allow_overwrite=allow_overwrite)

            with patch.object(pipeline.writer, "write_note", side_effect=tracking_write_note):
                pipeline.run("agentic RAG single write", offline=True)

            self.assertEqual(len(run_writes), 1)
            self.assertFalse(run_writes[0][1])

    def test_offline_fallback_blueprint_respects_non_bilingual_report_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(),
                sources=SourceSettings(
                    official_doc_domains=["developers.openai.com"],
                    standards_domains=[],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(),
                report=ReportSettings(bilingual=False),
            )

            artifacts = ResearchPipeline(settings).run("english only fallback", offline=True)

            blueprint = Path(artifacts.service_blueprint).read_text(encoding="utf-8")
            run_note = Path(artifacts.run_note).read_text(encoding="utf-8")
            self.assertIn("language: en", blueprint)
            self.assertNotIn("translation_language: ko", blueprint)
            self.assertNotIn("**한국어 번역**", blueprint)
            self.assertNotIn("## Bilingual Audit", run_note)

    def test_run_quality_gates_report_missing_source_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(api_key_env="RESEARCH_AGENT_MISSING_KEY"),
                sources=SourceSettings(
                    official_doc_domains=[],
                    standards_domains=[],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(min_official_sources=1),
            )

            with patch.object(
                ResearchPipeline,
                "_collect_sources",
                return_value=[SourceRecord(title="Missing URL source", url="", source_type="official-docs")],
            ):
                artifacts = ResearchPipeline(settings).run("quality gate failure", offline=False)

            run_note = Path(artifacts.run_note).read_text(encoding="utf-8")
            evidence_ledger = Path(artifacts.evidence_ledger).read_text(encoding="utf-8")
            self.assertIn("| FAIL | min official sources |", run_note)
            self.assertIn("| FAIL | source urls |", run_note)
            self.assertIn("| FAIL | source urls |", evidence_ledger)

    def test_paper_collector_failures_are_recorded_as_run_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(api_key_env="RESEARCH_AGENT_MISSING_KEY"),
                sources=SourceSettings(
                    official_doc_domains=[],
                    standards_domains=[],
                    paper_sources=["crossref"],
                ),
                quality_gates=QualityGateSettings(min_official_sources=0),
            )

            with patch("research_agent.collectors.search_crossref", side_effect=RuntimeError("rate limited")):
                artifacts = ResearchPipeline(settings).run("collector warning", offline=False)

            run_note = Path(artifacts.run_note).read_text(encoding="utf-8")
            self.assertIn("## Warnings", run_note)
            self.assertIn("| paper collector | crossref | RuntimeError: rate limited |", run_note)
            self.assertEqual(artifacts.source_notes, [])

    def test_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(api_key_env="RESEARCH_AGENT_MISSING_KEY"),
                sources=SourceSettings(
                    official_doc_domains=["developers.openai.com", "docs.langchain.com"],
                    standards_domains=["nist.gov"],
                    paper_sources=["arxiv"],
                ),
                quality_gates=QualityGateSettings(),
            )

            with patch.dict("os.environ", {}, clear=True):
                plan = ResearchPipeline(settings).dry_run(
                    "agentic RAG",
                    offline=False,
                    max_papers_per_source=1,
                )

            self.assertEqual(plan.mode, "none")
            self.assertTrue(any(artifact.status == "dynamic" for artifact in plan.artifacts))
            self.assertTrue(any(artifact.kind == "topic-map" for artifact in plan.artifacts))
            self.assertTrue(any(check.status == "WARN" and check.name == "llm provider" for check in plan.safety))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_dry_run_marks_official_docs_dynamic_when_api_key_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(),
                sources=SourceSettings(
                    official_doc_domains=["developers.openai.com", "docs.langchain.com"],
                    standards_domains=[],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(),
            )

            with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-123456"}, clear=True):
                plan = ResearchPipeline(settings).dry_run("agent frameworks", offline=False)

            official_artifacts = [
                artifact
                for artifact in plan.artifacts
                if "/10_Sources/official-docs/" in artifact.path
            ]
            self.assertTrue(official_artifacts)
            self.assertTrue(all(artifact.status == "dynamic" for artifact in official_artifacts))

    def test_dry_run_auto_selects_gemini_when_openai_key_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                app=AppSettings(),
                obsidian=ObsidianSettings(vault_path=Path(temp)),
                openai=OpenAISettings(),
                sources=SourceSettings(
                    official_doc_domains=["ai.google.dev"],
                    standards_domains=[],
                    paper_sources=[],
                ),
                quality_gates=QualityGateSettings(),
            )

            with patch.dict("os.environ", {"GEMINI_API_KEY": "gemini-test-key"}, clear=True):
                plan = ResearchPipeline(settings).dry_run("gemini provider", offline=False)

            self.assertEqual(plan.mode, "gemini")
            self.assertTrue(any(check.name == "llm provider" and check.status == "OK" for check in plan.safety))


if __name__ == "__main__":
    unittest.main()
