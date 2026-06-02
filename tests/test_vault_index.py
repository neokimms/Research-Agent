from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import (
    AppSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    Settings,
    SourceSettings,
)
from research_agent.vault_index import (
    apply_reviewed_backlinks,
    build_backlink_history,
    build_backlink_proposal_suggestions,
    build_backlink_review_queue,
    build_vault_index,
    render_apply_reviewed_backlinks_result,
    render_backlink_history,
    render_backlink_history_write_result,
    render_backlink_review_queue,
    render_vault_index,
    write_backlink_history_state,
    write_backlink_proposals,
    write_vault_index,
)


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
    )


class VaultIndexTests(unittest.TestCase):
    def test_builds_backlink_suggestions_for_shared_topic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Service-Blueprints").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            (vault / "30_Service-Blueprints" / "agent-blueprint.md").write_text(
                """---
type: service-blueprint
topic: Agentic RAG
status: draft
checked_at: 2000-01-01
generated_by: research-agent
---
# Blueprint
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
checked_at: 2000-01-01
generated_by: research-agent
---
# Evidence
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault, stale_days=1, max_suggestions=10)

        self.assertEqual(len(index.notes), 2)
        self.assertEqual(len(index.suggestions), 1)
        self.assertEqual(index.suggestions[0].score, 5)
        self.assertEqual(len(index.stale_notes), 2)

    def test_builds_rerun_lineage_backlink_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "20_Taxonomy").mkdir()
            (vault / "60_Runs").mkdir()
            (vault / "20_Taxonomy" / "agent-topic-map.md").write_text(
                """---
type: topic-map
topic: Agentic RAG
status: draft
generated_by: research-agent
rerun_of: failed-source
---
# Topic Map
""",
                encoding="utf-8",
            )
            (vault / "60_Runs" / "agent-run.md").write_text(
                """---
type: run-log
topic: Agentic RAG
status: draft
generated_by: research-agent
rerun_of: failed-source
---
# Run Log
""",
                encoding="utf-8",
            )

            suggestions = build_backlink_proposal_suggestions(vault, max_suggestions=10, min_score=3)

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].score, 6)
        self.assertEqual(suggestions[0].source.relative_path, "20_Taxonomy/agent-topic-map.md")
        self.assertEqual(suggestions[0].target.relative_path, "60_Runs/agent-run.md")
        self.assertIn("same rerun lineage `failed-source`", suggestions[0].reason)

    def test_existing_wikilink_suppresses_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Service-Blueprints").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            (vault / "30_Service-Blueprints" / "agent-blueprint.md").write_text(
                """---
type: service-blueprint
topic: Agentic RAG
status: draft
---
# Blueprint

[[50_Evidence-Ledger/agent-evidence|Evidence]]
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
---
# Evidence
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault)

        self.assertEqual(index.suggestions, [])

    def test_write_vault_index_creates_taxonomy_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "note.md").write_text("# Loose Note\n", encoding="utf-8")

            path = write_vault_index(_settings(vault), stale_days=90, max_suggestions=5)
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("type: vault-index", text)
            self.assertIn("## Backlink Suggestions", text)
            self.assertIn("## Orphan Notes To Review", text)
            self.assertIn("## Reference Orphan Notes", text)

    def test_backlink_proposal_note_does_not_modify_sources_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Service-Blueprints").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            source_path = vault / "30_Service-Blueprints" / "agent-blueprint.md"
            source_path.write_text(
                """---
type: service-blueprint
topic: Agentic RAG
status: draft
---
# Blueprint
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
---
# Evidence
""",
                encoding="utf-8",
            )
            before = source_path.read_text(encoding="utf-8")

            result = write_backlink_proposals(_settings(vault), max_suggestions=10)
            after = source_path.read_text(encoding="utf-8")
            proposal_exists = bool(result.proposal_path and result.proposal_path.exists())
            proposal = result.proposal_path.read_text(encoding="utf-8") if result.proposal_path else ""

        self.assertTrue(proposal_exists)
        self.assertEqual(after, before)
        self.assertEqual(result.appended_paths, [])
        self.assertIn("type: backlink-proposals", proposal)
        self.assertIn('proposal_state: "proposed"', proposal)
        self.assertIn("[[50_Evidence-Ledger/agent-evidence|agent-evidence]]", proposal)

    def test_apply_backlink_proposals_appends_checklist_to_draft_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "10_Sources").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            source_path = vault / "10_Sources" / "agent-source.md"
            source_path.write_text(
                """---
type: source-note
topic: Agentic RAG
status: draft
---
# Source
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
---
# Evidence
""",
                encoding="utf-8",
            )

            result = write_backlink_proposals(_settings(vault), max_suggestions=10, apply=True)
            text = source_path.read_text(encoding="utf-8")
            proposal = result.proposal_path.read_text(encoding="utf-8") if result.proposal_path else ""

        self.assertEqual(len(result.applied_suggestions), 1)
        self.assertEqual([path.resolve() for path in result.appended_paths], [source_path.resolve()])
        self.assertIn("## Backlink Proposals", text)
        self.assertIn("- [ ] Add [[50_Evidence-Ledger/agent-evidence|agent-evidence]]", text)
        self.assertIn('proposal_state: "applied"', proposal)
        self.assertIn("applied_at:", proposal)

    def test_apply_backlink_proposals_skips_reviewed_sources_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "20_Taxonomy").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            source_path = vault / "20_Taxonomy" / "topic-map.md"
            source_path.write_text(
                """---
type: topic-map
topic: Agentic RAG
status: reviewed
---
# Topic Map
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
---
# Evidence
""",
                encoding="utf-8",
            )

            result = write_backlink_proposals(_settings(vault), max_suggestions=10, apply=True)
            text = source_path.read_text(encoding="utf-8")
            proposal = result.proposal_path.read_text(encoding="utf-8") if result.proposal_path else ""

        self.assertEqual(result.applied_suggestions, [])
        self.assertEqual(len(result.skipped_suggestions), 1)
        self.assertNotIn("## Backlink Proposals", text)
        self.assertIn("Skipped protected notes: 1", proposal)

    def test_backlink_proposal_notes_are_skipped_when_reindexing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "60_Runs").mkdir()
            (vault / "note.md").write_text("# Normal Note\n", encoding="utf-8")
            (vault / "60_Runs" / "backlink-proposals.md").write_text(
                """---
type: backlink-proposals
status: draft
generated_by: research-agent
---
# Backlink Proposals
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault)

        self.assertEqual(len(index.notes), 1)
        self.assertEqual(index.notes[0].title, "Normal Note")

    def test_build_backlink_proposals_filters_by_min_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "10_Sources").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            (vault / "10_Sources" / "agent-source.md").write_text(
                """---
type: source-note
topic: Agentic RAG
status: draft
---
# Source
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
---
# Evidence
""",
                encoding="utf-8",
            )

            suggestions = build_backlink_proposal_suggestions(vault, max_suggestions=10, min_score=4)

        self.assertEqual(suggestions, [])

    def test_backlink_proposals_can_supersede_previous_proposed_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "10_Sources").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            (vault / "60_Runs").mkdir()
            previous = vault / "60_Runs" / "old-proposal.md"
            previous.write_text(
                """---
type: backlink-proposals
created_at: "2026-05-30"
checked_at: "2026-05-30"
status: draft
proposal_state: proposed
generated_by: research-agent
---

# Backlink Proposals

## Summary

- Candidate links: 1
- Applied checklist items: 0
- Skipped protected notes: 0
""",
                encoding="utf-8",
            )
            applied = vault / "60_Runs" / "applied-proposal.md"
            applied.write_text(
                """---
type: backlink-proposals
created_at: "2026-05-30"
checked_at: "2026-05-30"
status: draft
proposal_state: applied
generated_by: research-agent
---

# Backlink Proposals
""",
                encoding="utf-8",
            )
            (vault / "10_Sources" / "agent-source.md").write_text(
                """---
type: source-note
topic: Agentic RAG
status: draft
---
# Source
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
---
# Evidence
""",
                encoding="utf-8",
            )

            result = write_backlink_proposals(_settings(vault), max_suggestions=10, supersede_previous=True)
            previous_text = previous.read_text(encoding="utf-8")
            applied_text = applied.read_text(encoding="utf-8")
            new_relative = result.proposal_path.resolve().relative_to(vault.resolve()).as_posix() if result.proposal_path else ""

        self.assertEqual([path.resolve() for path in result.superseded_paths], [previous.resolve()])
        self.assertIn('proposal_state: "superseded"', previous_text)
        self.assertIn("superseded_at:", previous_text)
        self.assertIn(f'superseded_by: "{new_relative}"', previous_text)
        self.assertIn("proposal_state: applied", applied_text)
        self.assertNotIn("superseded_by:", applied_text)

    def test_build_backlink_review_queue_splits_pending_completed_and_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "Notes").mkdir()
            (vault / "Notes" / "pending.md").write_text(
                """# Pending

## Backlink Proposals

- [ ] Add [[Targets/ledger|ledger]] (score 3): same topic
""",
                encoding="utf-8",
            )
            (vault / "Notes" / "completed.md").write_text(
                """# Completed

## Backlink Proposals

- [x] Add [[Targets/blueprint|blueprint]] (score 5): reviewed
""",
                encoding="utf-8",
            )
            (vault / "Notes" / "resolved.md").write_text(
                """# Resolved

See [[Targets/run|run]] for execution details.

## Backlink Proposals

- [ ] Add [[Targets/run|run]] (score 4): already linked
""",
                encoding="utf-8",
            )

            queue = build_backlink_review_queue(vault)
            rendered = render_backlink_review_queue(queue)

        self.assertEqual(len(queue.items), 3)
        self.assertEqual(len(queue.pending), 1)
        self.assertEqual(queue.pending[0].relative_path, "Notes/pending.md")
        self.assertEqual(len(queue.completed), 1)
        self.assertEqual(queue.completed[0].relative_path, "Notes/completed.md")
        self.assertEqual(len(queue.resolved), 1)
        self.assertEqual(queue.resolved[0].relative_path, "Notes/resolved.md")
        self.assertIn("Pending: 1", rendered)
        self.assertIn("Completed: 1", rendered)
        self.assertIn("Resolved by existing wikilink: 1", rendered)

    def test_apply_reviewed_backlinks_moves_checked_items_to_related_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "Notes").mkdir()
            note = vault / "Notes" / "source.md"
            note.write_text(
                """# Source

## Related Notes

## Backlink Proposals

- [x] Add [[Targets/ledger|ledger]] (score 3): approved
- [ ] Add [[Targets/pending|pending]] (score 3): not approved
""",
                encoding="utf-8",
            )

            result = apply_reviewed_backlinks(vault)
            text = note.read_text(encoding="utf-8")
            rendered = render_apply_reviewed_backlinks_result(result)

        self.assertEqual(len(result.applied_items), 1)
        self.assertEqual([path.resolve() for path in result.updated_paths], [note.resolve()])
        self.assertIn("## Related Notes\n\n- [[Targets/ledger|ledger]]", text)
        self.assertIn("- [[Targets/ledger|ledger]]\n\n## Backlink Proposals", text)
        self.assertNotIn("- [[Targets/pending|pending]]", text)
        self.assertIn("Applied items: 1", rendered)

    def test_apply_reviewed_backlinks_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "Notes").mkdir()
            note = vault / "Notes" / "source.md"
            original = """# Source

## Backlink Proposals

- [x] Add [[Targets/ledger|ledger]] (score 3): approved
"""
            note.write_text(original, encoding="utf-8")

            result = apply_reviewed_backlinks(vault, dry_run=True)
            text = note.read_text(encoding="utf-8")

        self.assertTrue(result.dry_run)
        self.assertEqual(len(result.applied_items), 1)
        self.assertEqual(text, original)

    def test_apply_reviewed_backlinks_skips_already_resolved_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "Notes").mkdir()
            note = vault / "Notes" / "source.md"
            note.write_text(
                """# Source

Already linked: [[Targets/ledger|ledger]]

## Backlink Proposals

- [x] Add [[Targets/ledger|ledger]] (score 3): approved
""",
                encoding="utf-8",
            )

            result = apply_reviewed_backlinks(vault)

        self.assertEqual(result.applied_items, [])
        self.assertEqual(len(result.already_resolved_items), 1)
        self.assertEqual(result.updated_paths, [])

    def test_build_backlink_history_infers_latest_and_superseded_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "60_Runs").mkdir()
            (vault / "60_Runs" / "2026-05-31_backlink-proposals.md").write_text(
                """---
type: backlink-proposals
created_at: "2026-05-31"
checked_at: "2026-05-31"
status: draft
generated_by: research-agent
---

# Backlink Proposals

## Summary

- Candidate links: 16
- Applied checklist items: 0
- Skipped protected notes: 0
- Minimum score: 3
""",
                encoding="utf-8",
            )
            (vault / "60_Runs" / "2026-05-31_backlink-proposals-2.md").write_text(
                """---
type: backlink-proposals
created_at: "2026-05-31"
checked_at: "2026-05-31"
status: draft
generated_by: research-agent
---

# Backlink Proposals

## Summary

- Candidate links: 16
- Applied checklist items: 16
- Skipped protected notes: 0
- Minimum score: 3
""",
                encoding="utf-8",
            )

            history = build_backlink_history(vault)
            rendered = render_backlink_history(history)

        self.assertEqual(len(history.entries), 2)
        self.assertIsNotNone(history.latest)
        self.assertEqual(history.latest.relative_path, "60_Runs/2026-05-31_backlink-proposals-2.md")
        self.assertEqual(history.entries[0].effective_state, "superseded")
        self.assertEqual(history.entries[1].effective_state, "applied")
        self.assertEqual(history.state_counts["superseded"], 1)
        self.assertEqual(history.state_counts["applied"], 1)
        self.assertIn("Latest note: 60_Runs/2026-05-31_backlink-proposals-2.md", rendered)
        self.assertIn("| applied | 60_Runs/2026-05-31_backlink-proposals-2.md | 16 | 16 | 0 | 2026-05-31 |", rendered)

    def test_backlink_history_respects_explicit_proposal_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "60_Runs").mkdir()
            (vault / "60_Runs" / "explicit.md").write_text(
                """---
type: backlink-proposals
created_at: "2026-05-31"
checked_at: "2026-05-31"
status: draft
proposal_state: archived
generated_by: research-agent
---

# Backlink Proposals

## Summary

- Candidate links: 0
- Applied checklist items: 0
- Skipped protected notes: 0
""",
                encoding="utf-8",
            )

            history = build_backlink_history(vault)

        self.assertEqual(len(history.entries), 1)
        self.assertEqual(history.entries[0].proposal_state, "archived")
        self.assertEqual(history.entries[0].effective_state, "archived")

    def test_write_backlink_history_state_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "60_Runs").mkdir()
            note = vault / "60_Runs" / "2026-05-31_backlink-proposals.md"
            original = """---
type: backlink-proposals
created_at: "2026-05-31"
checked_at: "2026-05-31"
status: draft
generated_by: research-agent
---

# Backlink Proposals

## Summary

- Candidate links: 0
- Applied checklist items: 0
- Skipped protected notes: 0
"""
            note.write_text(original, encoding="utf-8")

            result = write_backlink_history_state(vault, dry_run=True)
            rendered = render_backlink_history_write_result(result)
            text = note.read_text(encoding="utf-8")

        self.assertTrue(result.dry_run)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].before_state, "missing")
        self.assertEqual(result.changes[0].after_state, "empty")
        self.assertEqual(text, original)
        self.assertIn("Would update notes: 1", rendered)

    def test_write_backlink_history_state_updates_missing_proposal_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "60_Runs").mkdir()
            note = vault / "60_Runs" / "2026-05-31_backlink-proposals.md"
            note.write_text(
                """---
type: backlink-proposals
created_at: "2026-05-31"
checked_at: "2026-05-31"
status: draft
generated_by: research-agent
---

# Backlink Proposals

## Summary

- Candidate links: 16
- Applied checklist items: 16
- Skipped protected notes: 0
""",
                encoding="utf-8",
            )

            result = write_backlink_history_state(vault)
            text = note.read_text(encoding="utf-8")
            second_result = write_backlink_history_state(vault)

        self.assertFalse(result.dry_run)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].after_state, "applied")
        self.assertIn('proposal_state: "applied"', text)
        self.assertEqual(second_result.changes, [])
        self.assertEqual(second_result.unchanged_count, 1)

    def test_render_vault_index_includes_count_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "note.md").write_text(
                """---
type: source-note
status: draft
---
# Note
""",
                encoding="utf-8",
            )
            index = build_vault_index(vault)
            markdown = render_vault_index(index, checked_at="2026-05-31", stale_days=90)

        self.assertIn("| source-note | 1 |", markdown)
        self.assertIn("| draft | 1 |", markdown)

    def test_suggests_links_from_shared_tags_without_exact_topic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Service-Blueprints").mkdir()
            (vault / "20_Taxonomy").mkdir()
            (vault / "30_Service-Blueprints" / "generated.md").write_text(
                """---
type: service-blueprint
topic: Agentic RAG production baseline
status: draft
generated_by: research-agent
tags:
  - agentic-rag
  - production
---
# Generated Blueprint
""",
                encoding="utf-8",
            )
            (vault / "20_Taxonomy" / "existing.md").write_text(
                """---
type: taxonomy
status: reviewed
aliases: [Agentic RAG, Production RAG]
tags:
  - agentic-rag
---
# Existing Agentic RAG Note
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault, max_suggestions=10)

        self.assertEqual(len(index.suggestions), 1)
        suggestion = index.suggestions[0]
        self.assertEqual(suggestion.score, 3)
        self.assertEqual(suggestion.source.generated_by, "research-agent")
        self.assertEqual(suggestion.target.status, "reviewed")
        self.assertIn("shared tags", suggestion.reason)

    def test_suggests_links_from_shared_terms_without_frontmatter_topic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Service-Blueprints").mkdir()
            (vault / "20_Taxonomy").mkdir()
            (vault / "30_Service-Blueprints" / "langgraph-baseline.md").write_text(
                """---
type: service-blueprint
status: draft
generated_by: research-agent
---
# LangGraph Durable Execution Baseline
""",
                encoding="utf-8",
            )
            (vault / "20_Taxonomy" / "langgraph-ops.md").write_text(
                """---
type: taxonomy
status: reviewed
---
# LangGraph Durable Workflow Operations
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault, max_suggestions=10)

        self.assertEqual(len(index.suggestions), 1)
        self.assertGreaterEqual(index.suggestions[0].score, 3)
        self.assertIn("shared terms", index.suggestions[0].reason)

    def test_generic_ai_system_terms_do_not_create_backlink_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "10_Sources" / "standards").mkdir(parents=True)
            (vault / "Reference").mkdir()
            (vault / "10_Sources" / "standards" / "ai-system-standard.md").write_text(
                """---
type: source-note
status: reviewed
generated_by: research-agent
---
# AI System Standard
""",
                encoding="utf-8",
            )
            (vault / "Reference" / "system-attributes.md").write_text(
                """---
type: reference
status: active
---
# AI System Attributes
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault, max_suggestions=10)

        self.assertEqual(index.suggestions, [])

    def test_render_vault_index_uses_suggestion_table_and_topic_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Service-Blueprints").mkdir()
            (vault / "50_Evidence-Ledger").mkdir()
            (vault / "30_Service-Blueprints" / "agent-blueprint.md").write_text(
                """---
type: service-blueprint
topic: Agentic RAG
status: draft
---
# Blueprint
""",
                encoding="utf-8",
            )
            (vault / "50_Evidence-Ledger" / "agent-evidence.md").write_text(
                """---
type: evidence-ledger
topic: Agentic RAG
status: draft
---
# Evidence
""",
                encoding="utf-8",
            )
            index = build_vault_index(vault)
            markdown = render_vault_index(index, checked_at="2026-05-31", stale_days=90)

        self.assertIn("## Topic Clusters", markdown)
        self.assertIn("Agentic RAG: 2 notes", markdown)
        self.assertIn("| score | source | target | reason |", markdown)

    def test_render_vault_index_separates_low_priority_backlink_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "60_Runs").mkdir()
            (vault / "60_Runs" / "official-refresh.md").write_text(
                """---
type: official-docs-refresh
status: draft
generated_by: research-agent
---
# Manual Refresh Review
""",
                encoding="utf-8",
            )
            (vault / "60_Runs" / "paper-refresh.md").write_text(
                """---
type: paper-refresh
status: draft
generated_by: research-agent
---
# Paper Refresh Review
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault, max_suggestions=10)
            markdown = render_vault_index(index, checked_at="2026-05-31", stale_days=90)

        actionable_section = markdown.split("## Low-Priority Backlink Signals", 1)[0]
        self.assertEqual(len(index.suggestions), 1)
        self.assertEqual(index.suggestions[0].score, 2)
        self.assertIn("Backlink suggestions: 0", markdown)
        self.assertIn("Low-priority backlink signals: 1", markdown)
        self.assertNotIn("official-refresh", actionable_section)
        self.assertNotIn("shared terms", actionable_section)
        self.assertIn("## Low-Priority Backlink Signals", markdown)
        self.assertIn("| 2 |", markdown)
        self.assertIn("official-refresh", markdown)

    def test_skips_generated_vault_index_notes_when_reindexing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "20_Taxonomy").mkdir()
            (vault / "note.md").write_text("# Normal Note\n", encoding="utf-8")
            (vault / "20_Taxonomy" / "vault-index.md").write_text(
                """---
type: vault-index
status: draft
generated_by: research-agent
---
# Vault Index
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault)

        self.assertEqual(len(index.notes), 1)
        self.assertEqual(index.notes[0].title, "Normal Note")

    def test_date_tokens_do_not_create_backlink_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "10_Sources").mkdir()
            (vault / "PM").mkdir()
            (vault / "10_Sources" / "2026-05-31_openai-source.md").write_text(
                """---
type: source-note
status: draft
generated_by: research-agent
---
# 2026 05 OpenAI Source
""",
                encoding="utf-8",
            )
            (vault / "PM" / "2026-05-22_cohort-analysis.md").write_text(
                """---
status: active
---
# 2026 05 Cohort Analysis
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault)

        self.assertEqual(index.suggestions, [])

    def test_splits_orphans_into_review_and_reference_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "10_Sources").mkdir()
            (vault / "60_Runs").mkdir()
            (vault / "References").mkdir()
            (vault / "10_Sources" / "generated.md").write_text(
                """---
type: source-note
status: draft
generated_by: research-agent
---
# Generated Source
""",
                encoding="utf-8",
            )
            (vault / "60_Runs" / "source-audit.md").write_text(
                """---
type: source-audit
status: draft
generated_by: research-agent
---
# Source Audit
""",
                encoding="utf-8",
            )
            (vault / "References" / "active-artifact.md").write_text(
                """---
status: active
---
# Active Artifact
""",
                encoding="utf-8",
            )
            (vault / "References" / "manual.md").write_text("# Manual Reference\n", encoding="utf-8")

            index = build_vault_index(vault)
            markdown = render_vault_index(index, checked_at="2026-05-31", stale_days=90)

        self.assertEqual(len(index.orphan_notes), 4)
        self.assertEqual(len(index.orphan_review_notes), 1)
        self.assertEqual(index.orphan_review_notes[0].title, "Generated Source")
        self.assertEqual(len(index.orphan_manual_review_notes), 1)
        self.assertEqual(index.orphan_manual_review_notes[0].title, "Active Artifact")
        self.assertEqual(len(index.orphan_history_notes), 1)
        self.assertEqual(index.orphan_history_notes[0].title, "Source Audit")
        self.assertEqual(len(index.orphan_reference_notes), 1)
        self.assertIn("Orphan notes to review: 1", markdown)
        self.assertIn("Manual orphan notes to review: 1", markdown)
        self.assertIn("## Manual Orphan Notes To Review", markdown)
        self.assertIn("Generated history orphan notes: 1", markdown)
        self.assertIn("## Generated History Orphan Notes", markdown)
        self.assertIn("Reference orphan notes: 1", markdown)

    def test_run_logs_only_suggest_to_topic_maps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "10_Sources").mkdir()
            (vault / "20_Taxonomy").mkdir()
            (vault / "60_Runs").mkdir()
            (vault / "10_Sources" / "source.md").write_text(
                """---
type: source-note
topic: Agentic RAG
status: draft
---
# Source
""",
                encoding="utf-8",
            )
            (vault / "20_Taxonomy" / "topic-map.md").write_text(
                """---
type: topic-map
topic: Agentic RAG
status: draft
---
# Topic Map
""",
                encoding="utf-8",
            )
            (vault / "60_Runs" / "run.md").write_text(
                """---
type: run-log
topic: Agentic RAG
status: draft
---
# Run
""",
                encoding="utf-8",
            )

            index = build_vault_index(vault, max_suggestions=10)

        run_suggestions = [
            suggestion
            for suggestion in index.suggestions
            if suggestion.source.note_type == "run-log" or suggestion.target.note_type == "run-log"
        ]
        self.assertEqual(len(run_suggestions), 1)
        self.assertIn("topic map", run_suggestions[0].reason)


if __name__ == "__main__":
    unittest.main()
