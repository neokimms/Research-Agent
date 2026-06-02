from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.blueprint_refresh import (
    apply_blueprint_refresh,
    build_blueprint_refresh,
    render_blueprint_refresh,
    render_blueprint_refresh_note,
    render_blueprint_refresh_apply_result,
    write_blueprint_refresh_note,
)
from research_agent.cli import main
from research_agent.config import (
    AppSettings,
    CommonModuleSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    Settings,
    SourceSettings,
)


class BlueprintRefreshTests(unittest.TestCase):
    def test_builds_refresh_candidate_from_evidence_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            result = build_blueprint_refresh(_settings(vault))
            rendered = render_blueprint_refresh(result)

        self.assertEqual(result.blueprints_scanned, 1)
        self.assertEqual(result.evidence_ledgers_scanned, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].proposal_id, "B001")
        self.assertIn("Blueprint refresh candidates: 1", rendered)
        self.assertIn("official-docs: 1", rendered)
        self.assertIn("papers: 1", rendered)

    def test_write_proposal_note_includes_candidate_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            write_result = write_blueprint_refresh_note(_settings(vault))
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "blueprint-refresh"', text)
        self.assertIn("- [ ] B001 Refresh", text)
        self.assertIn("## Candidate Records", text)
        self.assertIn('"One-Line Conclusion"', text)

    def test_apply_checked_candidate_updates_blueprint_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            blueprint = _write_fixture(vault)
            proposal = _write_blueprint_refresh_proposal(vault, checked=True)

            result = apply_blueprint_refresh(
                _settings(vault),
                dry_run=False,
                applied_at="2026-06-01T10:00:00+09:00",
            )
            text = blueprint.read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            rendered = render_blueprint_refresh_apply_result(result)

        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 1)
        self.assertIn('blueprint_refresh_provider: "deterministic-evidence-ledger"', text)
        self.assertIn("Use OpenAI managed assistant patterns", text)
        self.assertIn("관리형 assistant 플랫폼 패턴", text)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Updated notes: 1", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            blueprint = _write_fixture(vault)
            _write_blueprint_refresh_proposal(vault, checked=True)
            before = blueprint.read_text(encoding="utf-8")

            result = apply_blueprint_refresh(_settings(vault), dry_run=True)
            after = blueprint.read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 1)

    def test_no_candidate_after_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            _write_blueprint_refresh_proposal(vault, checked=True)
            apply_blueprint_refresh(_settings(vault), dry_run=False)

            result = build_blueprint_refresh(_settings(vault))

        self.assertEqual(result.candidates, [])

    def test_cli_apply_blueprint_refresh_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_fixture(vault)
            _write_blueprint_refresh_proposal(vault, checked=True)
            config = root / "config.toml"
            config.write_text(
                f"""[obsidian]
vault_path = "{vault.as_posix()}"

[common_modules]
enabled = false
""",
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--config", str(config), "apply-blueprint-refresh", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Blueprint Refresh Apply Dry Run", output.getvalue())
        self.assertIn("Would update notes: 1", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_fixture(vault: Path) -> Path:
    blueprint = vault / "30_Service-Blueprints" / "blueprint.md"
    blueprint.parent.mkdir(parents=True, exist_ok=True)
    blueprint.write_text(
        """---
type: service-blueprint
topic: "Agentic RAG"
status: draft
generated_by: research-agent
---
# Agentic RAG Service Blueprint

## One-Line Conclusion

Old conclusion.

## When To Use

- Old use case.

## Structure Classification

- Old classification.

## Recommended Baseline

```text
old
```

## Implementation Order

1. Old order.

## Operational Risks

- Old risk.

## Verification

- Old verification.

## Evidence

- Existing evidence.

## Still Uncertain

- Paper metadata still needs review.

## Related Notes
""",
        encoding="utf-8",
    )

    ledger = vault / "50_Evidence-Ledger" / "evidence.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        """---
type: evidence-ledger
topic: "Agentic RAG"
status: draft
generated_by: research-agent
---
# Evidence Ledger: Agentic RAG

| claim_id | claim | source | source_type | checked_at | confidence | note |
|---|---|---|---|---|---|---|
| E001 | OpenAI assistants support managed tools. | https://docs.example.com/openai | official-docs | 2026-06-01 | medium | official-docs: tools |
| E002 | LangGraph supports stateful multi-actor applications. | https://doi.org/10.example/langgraph | papers | 2026-06-01 | medium | papers: LangGraph |
""",
        encoding="utf-8",
    )
    return blueprint


def _write_blueprint_refresh_proposal(vault: Path, *, checked: bool) -> Path:
    result = build_blueprint_refresh(_settings(vault))
    path = vault / "60_Runs" / "2026-06-01_blueprint-refresh.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    text = render_blueprint_refresh_note(result, checked_at="2026-06-01T10:00:00+09:00").replace("- [ ] B001", f"- [{state}] B001")
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
