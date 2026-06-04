from __future__ import annotations

import hmac
import json
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib import parse
from uuid import uuid4

from .config import LLMSettings, Settings
from .doctor import run_doctor
from .next_actions import build_next_actions
from .pipeline import ResearchPipeline
from .portal_guide import render_portal_guide_html
from .secrets import select_llm_provider
from .timeutil import now_local
from .vault_health import build_vault_health


PORTAL_JOB_STORE_FILE = "research_portal_jobs.json"
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
ACTIVE_JOB_STATUSES = {"queued", "running"}
RUN_PREVIEW_LIMIT = 24_000


@dataclass(frozen=True)
class PortalAPIResponse:
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


@dataclass
class PortalJobRecord:
    job_id: str
    topic_preview: str
    topic: str | None = None
    status: str = "queued"
    created_at: str = field(default_factory=lambda: _now_iso())
    started_at: str | None = None
    finished_at: str | None = None
    mode: str = "run"
    provider: str = "auto"
    offline: bool = False
    dry_run: bool = False
    max_papers_per_source: int = 2
    research_type: str = "architecture"
    research_depth: str = "standard"
    source_priority: list[str] = field(default_factory=list)
    domain_focus: str = ""
    bilingual: bool | None = None
    rerun_of: str | None = None
    run_id: str | None = None
    pipeline_stage: str = ""
    summary: dict[str, Any] | None = None
    error: dict[str, str] | None = None

    def to_dict(self, *, include_result: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "topic_preview": self.topic_preview,
            "objective_preview": self.topic_preview,
            "mode": self.mode,
            "provider": self.provider,
            "offline": self.offline,
            "dry_run": self.dry_run,
            "max_papers_per_source": self.max_papers_per_source,
            "research_type": self.research_type,
            "research_depth": self.research_depth,
            "source_priority": self.source_priority,
            "domain_focus": self.domain_focus,
            "bilingual": self.bilingual,
            "run_id": self.run_id,
            "pipeline_stage": self.pipeline_stage,
            "status_url": f"/jobs/{self.job_id}",
        }
        if self.rerun_of:
            payload["rerun_of"] = self.rerun_of
        if include_result and self.topic:
            payload["topic"] = self.topic
            payload["objective"] = self.topic
        if self.run_id:
            payload["run_url"] = f"/runs/{self.run_id}"
        if self.error is not None:
            payload["error"] = self.error
        if include_result and self.status == "completed":
            payload["summary"] = self.summary or {}
            payload["paths"] = (self.summary or {}).get("paths", {})
        return payload

    def to_storage_dict(self) -> dict[str, Any]:
        return self.to_dict(include_result=True)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PortalJobRecord":
        job_id = str(payload.get("job_id", "")).strip()
        if not _safe_id(job_id):
            raise ValueError(f"Invalid portal job id in store: {job_id!r}")
        return cls(
            job_id=job_id,
            topic_preview=str(payload.get("topic_preview") or payload.get("objective_preview") or "").strip(),
            topic=str(payload.get("topic") or payload.get("objective") or "").strip() or None,
            status=str(payload.get("status", "queued")).strip() or "queued",
            created_at=str(payload.get("created_at", "")).strip() or _now_iso(),
            started_at=_optional_string(payload.get("started_at")),
            finished_at=_optional_string(payload.get("finished_at")),
            mode=str(payload.get("mode", "run")).strip() or "run",
            provider=str(payload.get("provider", "auto")).strip() or "auto",
            offline=bool(payload.get("offline", False)),
            dry_run=bool(payload.get("dry_run", False)),
            max_papers_per_source=_positive_int(payload.get("max_papers_per_source"), default=2),
            research_type=str(payload.get("research_type", "architecture")).strip() or "architecture",
            research_depth=str(payload.get("research_depth", "standard")).strip() or "standard",
            source_priority=_string_list(payload.get("source_priority")),
            domain_focus=str(payload.get("domain_focus", "")).strip(),
            bilingual=_optional_bool(payload.get("bilingual")),
            rerun_of=_optional_safe_id(payload.get("rerun_of")),
            run_id=_optional_string(payload.get("run_id")),
            pipeline_stage=str(payload.get("pipeline_stage") or "").strip(),
            summary=_optional_mapping(payload.get("summary")),
            error=_optional_error(payload.get("error")),
        )


@dataclass(frozen=True)
class PortalJobRetentionPolicy:
    retention_days: int = 0
    retention_limit: int = 0

    def __post_init__(self) -> None:
        if self.retention_days < 0:
            raise ValueError("Portal job retention_days must be zero or greater.")
        if self.retention_limit < 0:
            raise ValueError("Portal job retention_limit must be zero or greater.")

    @property
    def enabled(self) -> bool:
        return self.retention_days > 0 or self.retention_limit > 0


@dataclass(frozen=True)
class PortalJobCleanupItem:
    job_id: str
    status: str
    created_at: str
    finished_at: str | None
    reason: str


@dataclass(frozen=True)
class PortalJobCleanupResult:
    job_store_path: Path
    dry_run: bool
    retention_days: int
    retention_limit: int
    total_before: int
    total_after: int
    active_jobs: int
    terminal_jobs: int
    removed_jobs: list[PortalJobCleanupItem]


class PortalJobStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, PortalJobRecord]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        raw_jobs = payload.get("jobs", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_jobs, list):
            raise ValueError("Portal job store must contain a list or a top-level 'jobs' list.")
        jobs: dict[str, PortalJobRecord] = {}
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            record = PortalJobRecord.from_mapping(item)
            jobs[record.job_id] = record
        return jobs

    def save(self, jobs: Mapping[str, PortalJobRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": _now_iso(),
            "total_jobs": len(jobs),
            "jobs": [job.to_storage_dict() for job in jobs.values()],
        }
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(self.path)


def cleanup_portal_job_store(
    path: str | Path,
    *,
    retention_days: int = 0,
    retention_limit: int = 0,
    dry_run: bool = True,
    now: datetime | None = None,
) -> PortalJobCleanupResult:
    store = PortalJobStore(path)
    jobs = store.load()
    result, working = _build_portal_job_cleanup_result(
        jobs,
        store.path,
        retention_days=retention_days,
        retention_limit=retention_limit,
        dry_run=dry_run,
        now=now,
    )
    if result.removed_jobs and not dry_run:
        store.save(working)
    return result


def render_portal_job_cleanup_result(result: PortalJobCleanupResult, *, max_removed: int = 50) -> str:
    retention_days = f"{result.retention_days} day(s)" if result.retention_days > 0 else "disabled"
    retention_limit = f"newest {result.retention_limit} terminal job(s)" if result.retention_limit > 0 else "disabled"
    shown = result.removed_jobs[:max_removed]
    hidden = len(result.removed_jobs) - len(shown)
    lines = [
        "Portal Job Store Cleanup",
        "",
        f"Job store: {result.job_store_path}",
        f"Mode: {'dry-run' if result.dry_run else 'apply'}",
        f"Retention days: {retention_days}",
        f"Retention limit: {retention_limit}",
        f"Jobs before: {result.total_before}",
        f"Projected jobs after: {result.total_after}",
        f"Active jobs kept: {result.active_jobs}",
        f"Terminal jobs scanned: {result.terminal_jobs}",
        f"Prune candidates: {len(result.removed_jobs)}",
        "",
        "Removed jobs:",
    ]
    if not shown:
        lines.append("- none")
    else:
        for item in shown:
            finished = item.finished_at or "-"
            lines.append(f"- {item.job_id} [{item.status}] finished={finished} reason={item.reason}")
    if hidden > 0:
        lines.append(f"- ... {hidden} more hidden; raise --max-removed to show more")
    if result.dry_run:
        lines.extend(["", "No changes written. Re-run with --apply to prune these terminal jobs."])
    return "\n".join(lines) + "\n"


class ResearchPortalAPIAdapter:
    """Small JSON runtime API for web portals and portal BFFs."""

    def __init__(
        self,
        settings: Settings,
        *,
        config_path: str | Path = "config/research-agent.example.toml",
        env_file: str | Path = ".env",
        auth_mode: str = "none",
        bearer_token: str | None = None,
        max_request_bytes: int = 200_000,
        max_response_bytes: int = 5_000_000,
        max_workers: int = 1,
        max_active_jobs: int = 20,
        job_store_path: str | Path | None = None,
        job_retention_days: int = 0,
        job_retention_limit: int = 0,
        job_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if auth_mode not in {"none", "bearer"}:
            raise ValueError("Portal API auth_mode must be 'none' or 'bearer'.")
        if auth_mode == "bearer" and not bearer_token:
            raise ValueError("Portal API bearer auth requires a bearer_token.")
        if max_request_bytes <= 0:
            raise ValueError("Portal API max_request_bytes must be greater than 0.")
        if max_response_bytes <= 0:
            raise ValueError("Portal API max_response_bytes must be greater than 0.")
        if max_workers <= 0:
            raise ValueError("Portal API max_workers must be greater than 0.")
        if max_active_jobs <= 0:
            raise ValueError("Portal API max_active_jobs must be greater than 0.")

        self.settings = settings
        self.config_path = Path(config_path)
        self.env_file = Path(env_file)
        self.auth_mode = auth_mode
        self.bearer_token = bearer_token
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.max_active_jobs = max_active_jobs
        self.job_retention = PortalJobRetentionPolicy(
            retention_days=job_retention_days,
            retention_limit=job_retention_limit,
        )
        self.job_id_factory = job_id_factory or (lambda: uuid4().hex)
        run_dir = settings.obsidian.vault_path.expanduser().resolve() / settings.obsidian.run_dir
        self.job_store = PortalJobStore(job_store_path or run_dir / PORTAL_JOB_STORE_FILE)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="research-portal-api")
        self._lock = Lock()
        self._jobs = self.job_store.load()
        changed = self._mark_interrupted_jobs()
        removed = self._apply_job_retention()
        if changed or removed:
            self.job_store.save(self._jobs)
        self._futures: dict[str, Future[None]] = {}

    def handle_request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> PortalAPIResponse:
        normalized_method = method.upper()
        route_path, query = _split_path(path)
        request_headers = headers or {}

        if route_path == "/health" and normalized_method in {"GET", "HEAD"}:
            return self._health_response(head=normalized_method == "HEAD")
        if normalized_method not in {"GET", "HEAD", "POST"}:
            return _json_response(405, {"error": "method_not_allowed", "allowed": ["GET", "HEAD", "POST"]})
        if normalized_method in {"GET", "HEAD"}:
            static_response = self._static_response(route_path, head=normalized_method == "HEAD")
            if static_response is not None:
                return static_response
        if not self._authorized(request_headers):
            return _json_response(
                401,
                {"error": "unauthorized", "auth": "bearer"},
                headers={"WWW-Authenticate": 'Bearer realm="research-portal-api"'},
                head=normalized_method == "HEAD",
            )

        if route_path == "/doctor" and normalized_method in {"GET", "HEAD"}:
            return self._doctor_response(query, head=normalized_method == "HEAD")
        if route_path == "/vault-health" and normalized_method in {"GET", "HEAD"}:
            return self._vault_health_response(query, head=normalized_method == "HEAD")
        if route_path == "/next-actions" and normalized_method in {"GET", "HEAD"}:
            return self._next_actions_response(query, head=normalized_method == "HEAD")
        if route_path == "/job-store-health" and normalized_method in {"GET", "HEAD"}:
            return self._job_store_health_response(query, head=normalized_method == "HEAD")
        if route_path == "/runs" and normalized_method == "POST":
            return self._create_run(body)
        if route_path == "/runs" and normalized_method in {"GET", "HEAD"}:
            return self._runs_response(head=normalized_method == "HEAD")
        if route_path.startswith("/runs/") and normalized_method in {"GET", "HEAD"}:
            run_id = route_path.removeprefix("/runs/")
            if not _safe_id(run_id):
                return _json_response(404, {"error": "not_found", "path": route_path}, head=normalized_method == "HEAD")
            return self._run_response(run_id, head=normalized_method == "HEAD")
        if route_path == "/jobs" and normalized_method in {"GET", "HEAD"}:
            return self._jobs_response(head=normalized_method == "HEAD")
        if route_path.startswith("/jobs/") and normalized_method in {"GET", "HEAD"}:
            job_id = route_path.removeprefix("/jobs/")
            if not _safe_id(job_id):
                return _json_response(404, {"error": "not_found", "path": route_path}, head=normalized_method == "HEAD")
            return self._job_response(job_id, head=normalized_method == "HEAD")

        return _json_response(404, {"error": "not_found", "path": route_path})

    def close(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def wait_for_job(self, job_id: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        with self._lock:
            future = self._futures.get(job_id)
        if future is None:
            raise KeyError(f"Unknown portal API job: {job_id}")
        future.result(timeout=timeout_seconds)
        return self._job_payload(job_id)

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        if self.auth_mode == "none":
            return True
        authorization = _header(headers, "authorization")
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            return False
        return hmac.compare_digest(authorization[len(prefix) :].strip(), self.bearer_token or "")

    def _static_response(self, route_path: str, *, head: bool = False) -> PortalAPIResponse | None:
        if route_path in {"/", "/index.html"}:
            return _text_response(200, "text/html; charset=utf-8", _PORTAL_HTML, head=head)
        if route_path in {"/guide", "/guide.html"}:
            return _text_response(200, "text/html; charset=utf-8", render_portal_guide_html(), head=head)
        if route_path == "/assets/portal.css":
            return _text_response(200, "text/css; charset=utf-8", _PORTAL_CSS, head=head)
        if route_path == "/assets/portal.js":
            return _text_response(200, "application/javascript; charset=utf-8", _PORTAL_JS, head=head)
        return None

    def _health_response(self, *, head: bool = False) -> PortalAPIResponse:
        provider = select_llm_provider(self.settings)
        return _json_response(
            200,
            {
                "ok": True,
                "service": "research-agent",
                "vault_path": str(self.settings.obsidian.vault_path.expanduser().resolve()),
                "provider": provider.provider,
                "provider_available": provider.available,
                "auth_mode": self.auth_mode,
            },
            head=head,
        )

    def _doctor_response(self, query: Mapping[str, list[str]], *, head: bool = False) -> PortalAPIResponse:
        write_test = _bool_query(query, "write_test", default=False)
        report = run_doctor(
            self.settings,
            config_path=self.config_path,
            env_file=self.env_file,
            write_test=write_test,
            openai_smoke=False,
            gemini_smoke=False,
        )
        return _json_response(
            200 if not report.has_failures else 503,
            {
                "ok": not report.has_failures,
                "checks": [asdict(check) for check in report.checks],
            },
            head=head,
        )

    def _vault_health_response(self, query: Mapping[str, list[str]], *, head: bool = False) -> PortalAPIResponse:
        report = build_vault_health(
            self.settings,
            stale_days=_int_query(query, "stale_days", 90),
            max_suggestions=_int_query(query, "max_suggestions", 20),
            min_score=_int_query(query, "min_score", 3),
        )
        return _json_response(200, {"status": report.status, "report": _json_safe(report)}, head=head)

    def _next_actions_response(self, query: Mapping[str, list[str]], *, head: bool = False) -> PortalAPIResponse:
        report = build_next_actions(
            self.settings,
            stale_days=_int_query(query, "stale_days", 90),
            max_suggestions=_int_query(query, "max_suggestions", 20),
            min_score=_int_query(query, "min_score", 3),
            max_items=_int_query(query, "max_items", 20),
        )
        return _json_response(200, {"health_status": report.health.status, "report": _json_safe(report)}, head=head)

    def _job_store_health_response(self, query: Mapping[str, list[str]], *, head: bool = False) -> PortalAPIResponse:
        retention_days = _non_negative_int_query(query, "retention_days", self.job_retention.retention_days)
        retention_limit = _non_negative_int_query(query, "retention_limit", self.job_retention.retention_limit)
        max_removed = _positive_int(query.get("max_removed", ["10"])[-1] if query.get("max_removed") else 10, default=10)
        with self._lock:
            jobs = dict(self._jobs)
        cleanup, _working = _build_portal_job_cleanup_result(
            jobs,
            self.job_store.path,
            retention_days=retention_days,
            retention_limit=retention_limit,
            dry_run=True,
        )
        removed_jobs = cleanup.removed_jobs[:max_removed]
        payload = {
            "job_store_path": str(self.job_store.path),
            "exists": self.job_store.path.exists(),
            "total_jobs": cleanup.total_before,
            "active_jobs": cleanup.active_jobs,
            "terminal_jobs": cleanup.terminal_jobs,
            "status_counts": _status_counts(jobs),
            "retention": {
                "auto_enabled": self.job_retention.enabled,
                "configured_days": self.job_retention.retention_days,
                "configured_limit": self.job_retention.retention_limit,
                "preview_days": retention_days,
                "preview_limit": retention_limit,
            },
            "cleanup_preview": {
                "dry_run": True,
                "projected_total_after": cleanup.total_after,
                "prune_candidates": len(cleanup.removed_jobs),
                "removed_jobs": [asdict(item) for item in removed_jobs],
                "hidden_removed_jobs": max(0, len(cleanup.removed_jobs) - len(removed_jobs)),
            },
        }
        return _json_response(200, payload, head=head)

    def _create_run(self, body: bytes) -> PortalAPIResponse:
        if len(body) > self.max_request_bytes:
            return _json_response(413, {"error": "request_too_large", "max_request_bytes": self.max_request_bytes})
        try:
            payload = json.loads(body.decode("utf-8") if body else "{}")
        except json.JSONDecodeError as exc:
            return _json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return _json_response(400, {"error": "invalid_payload", "detail": "request body must be a JSON object"})

        topic = str(payload.get("topic") or payload.get("objective") or "").strip()
        if not topic:
            return _json_response(400, {"error": "missing_topic", "detail": "Provide 'topic' or AI Agent Architecture-compatible 'objective'."})
        provider = str(payload.get("provider") or self.settings.llm.provider or "auto").strip().lower()
        if provider not in {"auto", "openai", "gemini"}:
            return _json_response(400, {"error": "invalid_provider", "allowed": ["auto", "openai", "gemini"]})
        offline = bool(payload.get("offline", False))
        dry_run = bool(payload.get("dry_run", False))
        research_type = str(payload.get("research_type") or "architecture").strip().lower()
        _VALID_RESEARCH_TYPES = {"architecture", "paper", "papers", "standards", "market", "official-docs"}
        if research_type not in _VALID_RESEARCH_TYPES:
            research_type = "architecture"
        research_depth = str(payload.get("research_depth") or "standard").strip().lower()
        if research_depth not in {"quick", "standard", "deep"}:
            return _json_response(400, {"error": "invalid_research_depth", "allowed": ["quick", "standard", "deep"]})
        source_priority = _normalize_source_priority(payload.get("source_priority"), research_type=research_type)
        domain_focus = str(payload.get("domain_focus") or "").strip()[:240]
        bilingual = _optional_bool(payload.get("bilingual"))
        max_papers = _positive_int(
            payload.get("max_papers_per_source"),
            default=_default_paper_limit(research_depth),
        )
        rerun_of = _optional_string(payload.get("rerun_of"))
        if rerun_of is not None and not _safe_id(rerun_of):
            return _json_response(400, {"error": "invalid_rerun_of", "detail": "rerun_of must be a safe job id."})

        with self._lock:
            active = sum(1 for job in self._jobs.values() if job.status in {"queued", "running"})
            if active >= self.max_active_jobs:
                return _json_response(429, {"error": "job_queue_full", "max_active_jobs": self.max_active_jobs})
            job_id = self.job_id_factory()
            record = PortalJobRecord(
                job_id=job_id,
                topic_preview=topic[:240],
                topic=topic,
                mode="dry_run" if dry_run else "run",
                provider=provider,
                offline=offline,
                dry_run=dry_run,
                max_papers_per_source=max_papers,
                research_type=research_type,
                research_depth=research_depth,
                source_priority=source_priority,
                domain_focus=domain_focus,
                bilingual=bilingual,
                rerun_of=rerun_of,
            )
            self._jobs[job_id] = record
            self._save_jobs()
            future = self._executor.submit(
                self._run_job,
                job_id,
                topic,
                provider,
                offline,
                dry_run,
                max_papers,
                rerun_of,
                research_type,
                research_depth,
                source_priority,
                domain_focus,
                bilingual,
            )
            self._futures[job_id] = future

        return _json_response(202, record.to_dict(include_result=False), headers={"Location": f"/jobs/{job_id}"})

    def _run_job(
        self,
        job_id: str,
        topic: str,
        provider: str,
        offline: bool,
        dry_run: bool,
        max_papers_per_source: int,
        rerun_of: str | None,
        research_type: str,
        research_depth: str,
        source_priority: list[str],
        domain_focus: str,
        bilingual: bool | None,
    ) -> None:
        with self._lock:
            record = self._jobs[job_id]
            record.status = "running"
            record.started_at = _now_iso()
            self._save_jobs()

        try:
            settings = self._settings_for_run(
                provider,
                source_priority=source_priority,
                bilingual=bilingual,
            )
            research_context = {
                "research_type": research_type,
                "research_depth": research_depth,
                "source_priority": source_priority,
                "domain_focus": domain_focus,
                "bilingual": settings.report.bilingual,
            }
            pipeline = ResearchPipeline(settings)
            if dry_run:
                plan = pipeline.dry_run(topic, offline=offline, max_papers_per_source=max_papers_per_source)
                summary = {
                    "type": "dry_run",
                    "topic": plan.topic,
                    "vault_path": plan.vault_path,
                    "mode": plan.mode,
                    "research_context": research_context,
                    "artifacts": [asdict(artifact) for artifact in plan.artifacts],
                    "safety": [asdict(check) for check in plan.safety],
                    "paths": {"planned_artifacts": [artifact.path for artifact in plan.artifacts]},
                }
                if rerun_of:
                    summary["rerun_of"] = rerun_of
            else:
                artifacts = pipeline.run(
                    topic,
                    offline=offline,
                    max_papers_per_source=max_papers_per_source,
                    rerun_of=rerun_of,
                    domain_focus=domain_focus,
                    on_stage=lambda stage: self._update_job_stage(job_id, stage),
                )
                summary = {
                    "type": "run",
                    "topic": topic,
                    "vault_path": str(settings.obsidian.vault_path.expanduser().resolve()),
                    "research_context": research_context,
                    "artifacts": asdict(artifacts),
                    "paths": {
                        "run_note": artifacts.run_note,
                        "source_notes": artifacts.source_notes,
                        "evidence_ledger": artifacts.evidence_ledger,
                        "service_blueprint": artifacts.service_blueprint,
                        "topic_map": artifacts.topic_map,
                    },
                }
                summary["review"] = _build_run_review_summary(summary["paths"], settings.obsidian.vault_path)
                if rerun_of:
                    summary["rerun_of"] = rerun_of

            with self._lock:
                record = self._jobs[job_id]
                record.status = "completed"
                record.finished_at = _now_iso()
                record.run_id = job_id
                record.summary = summary
                self._save_jobs()
        except Exception as exc:
            with self._lock:
                record = self._jobs[job_id]
                record.status = "failed"
                record.finished_at = _now_iso()
                record.error = {"type": type(exc).__name__, "message": str(exc)}
                self._save_jobs()

    def _settings_for_run(
        self,
        provider: str,
        *,
        source_priority: list[str],
        bilingual: bool | None,
    ) -> Settings:
        llm = self.settings.llm if provider == self.settings.llm.provider else LLMSettings(provider=provider)
        sources = self.settings.sources
        if source_priority:
            sources = replace(sources, priority=source_priority)
        report = self.settings.report
        if bilingual is not None:
            report = replace(report, bilingual=bilingual)
        return replace(self.settings, llm=llm, sources=sources, report=report)

    def _update_job_stage(self, job_id: str, stage: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record.pipeline_stage = stage
                self._save_jobs()

    def _jobs_response(self, *, head: bool = False) -> PortalAPIResponse:
        with self._lock:
            jobs = [job.to_dict(include_result=False) for job in self._jobs.values()]
        jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return _json_response(200, {"jobs": jobs}, head=head)

    def _job_response(self, job_id: str, *, head: bool = False) -> PortalAPIResponse:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return _json_response(404, {"error": "job_not_found", "job_id": job_id}, head=head)
            payload = record.to_dict(include_result=True)
        return _json_response(200, payload, head=head)

    def _runs_response(self, *, head: bool = False) -> PortalAPIResponse:
        with self._lock:
            runs = [
                record.to_dict(include_result=True)
                for record in self._jobs.values()
                if record.status == "completed" and record.run_id
            ]
        runs.sort(key=lambda item: str(item.get("finished_at") or item.get("created_at") or ""), reverse=True)
        return _json_response(200, {"runs": runs}, head=head)

    def _run_response(self, run_id: str, *, head: bool = False) -> PortalAPIResponse:
        with self._lock:
            record = next((job for job in self._jobs.values() if job.run_id == run_id or job.job_id == run_id), None)
            if record is None or record.status != "completed":
                return _json_response(404, {"error": "run_not_found", "run_id": run_id}, head=head)
            payload = record.to_dict(include_result=True)
        return _json_response(200, payload, head=head)

    def _job_payload(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._jobs[job_id]
            return record.to_dict(include_result=True)

    def _mark_interrupted_jobs(self) -> bool:
        changed = False
        for job in self._jobs.values():
            if job.status in {"queued", "running"}:
                job.status = "interrupted"
                job.finished_at = _now_iso()
                job.error = {"type": "Interrupted", "message": "Portal API process stopped before this job completed."}
                changed = True
        return changed

    def _apply_job_retention(self) -> list[PortalJobCleanupItem]:
        return _prune_terminal_jobs(self._jobs, self.job_retention)

    def _save_jobs(self) -> None:
        self._apply_job_retention()
        self.job_store.save(self._jobs)


def serve_portal_api(
    settings: Settings,
    *,
    config_path: str | Path = "config/research-agent.example.toml",
    env_file: str | Path = ".env",
    host: str = "127.0.0.1",
    port: int = 8780,
    auth_mode: str = "none",
    bearer_token: str | None = None,
    max_workers: int = 1,
    max_active_jobs: int = 20,
    job_store_path: str | Path | None = None,
    job_retention_days: int = 0,
    job_retention_limit: int = 0,
) -> None:
    if port < 1 or port > 65535:
        raise ValueError("Portal API port must be between 1 and 65535.")
    adapter = ResearchPortalAPIAdapter(
        settings,
        config_path=config_path,
        env_file=env_file,
        auth_mode=auth_mode,
        bearer_token=bearer_token,
        max_workers=max_workers,
        max_active_jobs=max_active_jobs,
        job_store_path=job_store_path,
        job_retention_days=job_retention_days,
        job_retention_limit=job_retention_limit,
    )
    server = ThreadingHTTPServer((host, port), _handler_class(adapter))
    try:
        server.serve_forever()
    finally:
        adapter.close(wait=False)
        server.server_close()


def _handler_class(adapter: ResearchPortalAPIAdapter) -> type[BaseHTTPRequestHandler]:
    class PortalRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send(adapter.handle_request(self.path, method="GET", headers=dict(self.headers.items())))

        def do_HEAD(self) -> None:
            self._send(adapter.handle_request(self.path, method="HEAD", headers=dict(self.headers.items())))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            self._send(adapter.handle_request(self.path, method="POST", headers=dict(self.headers.items()), body=body))

        def log_message(self, format: str, *args: Any) -> None:
            return None

        def _send(self, response: PortalAPIResponse) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

    return PortalRequestHandler


def _json_response(
    status: int,
    payload: Mapping[str, Any],
    *,
    headers: dict[str, str] | None = None,
    head: bool = False,
) -> PortalAPIResponse:
    body = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(body) > 5_000_000:
        compact = {"error": "response_too_large", "max_response_bytes": 5_000_000}
        body = json.dumps(compact, ensure_ascii=False, sort_keys=True).encode("utf-8")
        status = 413
    return PortalAPIResponse(
        status,
        "application/json; charset=utf-8",
        b"" if head else body,
        headers=dict(headers or {}) | {"Content-Length": str(len(body))},
    )


def _text_response(status: int, content_type: str, text: str, *, head: bool = False) -> PortalAPIResponse:
    body = text.encode("utf-8")
    return PortalAPIResponse(
        status,
        content_type,
        b"" if head else body,
        headers={"Content-Length": str(len(body))},
    )


def _split_path(path: str) -> tuple[str, dict[str, list[str]]]:
    parsed = parse.urlparse(path or "/")
    route_path = parse.unquote(parsed.path or "/")
    if not route_path.startswith("/"):
        route_path = f"/{route_path}"
    return route_path, parse.parse_qs(parsed.query or "", keep_blank_values=True)


def _safe_id(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and value not in {".", ".."}


def _header(headers: Mapping[str, str], key: str) -> str:
    lowered = key.lower()
    for candidate, value in headers.items():
        if candidate.lower() == lowered:
            return value
    return ""


def _now_iso() -> str:
    return now_local("Asia/Seoul").isoformat(timespec="seconds")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _count_statuses(jobs: Mapping[str, PortalJobRecord], statuses: set[str]) -> int:
    return sum(1 for job in jobs.values() if job.status.lower() in statuses)


def _status_counts(jobs: Mapping[str, PortalJobRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs.values():
        status = job.status.lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _build_portal_job_cleanup_result(
    jobs: Mapping[str, PortalJobRecord],
    path: Path,
    *,
    retention_days: int,
    retention_limit: int,
    dry_run: bool,
    now: datetime | None = None,
) -> tuple[PortalJobCleanupResult, dict[str, PortalJobRecord]]:
    policy = PortalJobRetentionPolicy(retention_days=retention_days, retention_limit=retention_limit)
    before = len(jobs)
    active_jobs = _count_statuses(jobs, ACTIVE_JOB_STATUSES)
    terminal_jobs = _count_statuses(jobs, TERMINAL_JOB_STATUSES)
    working = dict(jobs)
    removed_jobs = _prune_terminal_jobs(working, policy, now=now)
    return (
        PortalJobCleanupResult(
            job_store_path=path,
            dry_run=dry_run,
            retention_days=retention_days,
            retention_limit=retention_limit,
            total_before=before,
            total_after=before - len(removed_jobs),
            active_jobs=active_jobs,
            terminal_jobs=terminal_jobs,
            removed_jobs=removed_jobs,
        ),
        working,
    )


def _prune_terminal_jobs(
    jobs: dict[str, PortalJobRecord],
    policy: PortalJobRetentionPolicy,
    *,
    now: datetime | None = None,
) -> list[PortalJobCleanupItem]:
    if not policy.enabled:
        return []
    reference_now = _normalize_datetime(now) if now is not None else _now_utc()
    cutoff = reference_now - timedelta(days=policy.retention_days) if policy.retention_days > 0 else None
    terminal_jobs = [job for job in jobs.values() if job.status.lower() in TERMINAL_JOB_STATUSES]
    terminal_jobs.sort(key=lambda job: (_job_reference_time(job), job.job_id), reverse=True)

    reasons_by_id: dict[str, list[str]] = {}
    if cutoff is not None:
        for job in terminal_jobs:
            timestamp = _job_reference_time(job)
            if timestamp < cutoff:
                reasons_by_id.setdefault(job.job_id, []).append(f"older than {policy.retention_days} day(s)")

    if policy.retention_limit > 0:
        keep_ids = {job.job_id for job in terminal_jobs[: policy.retention_limit]}
        for job in terminal_jobs[policy.retention_limit :]:
            if job.job_id not in keep_ids:
                reasons_by_id.setdefault(job.job_id, []).append(
                    f"beyond newest {policy.retention_limit} terminal job(s)"
                )

    removed: list[PortalJobCleanupItem] = []
    for job in sorted(
        (job for job in terminal_jobs if job.job_id in reasons_by_id),
        key=lambda item: (_job_reference_time(item), item.job_id),
    ):
        reason = "; ".join(reasons_by_id[job.job_id])
        removed.append(
            PortalJobCleanupItem(
                job_id=job.job_id,
                status=job.status,
                created_at=job.created_at,
                finished_at=job.finished_at,
                reason=reason,
            )
        )
        jobs.pop(job.job_id, None)
    return removed


def _job_reference_time(job: PortalJobRecord) -> datetime:
    return (
        _parse_iso_datetime(job.finished_at)
        or _parse_iso_datetime(job.started_at)
        or _parse_iso_datetime(job.created_at)
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _normalize_datetime(datetime.fromisoformat(value))
    except ValueError:
        return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_safe_id(value: Any) -> str | None:
    text = _optional_string(value)
    return text if text and _safe_id(text) else None


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _optional_error(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items()}


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _default_paper_limit(research_depth: str) -> int:
    if research_depth == "quick":
        return 1
    if research_depth == "deep":
        return 5
    return 2


def _normalize_source_priority(value: Any, *, research_type: str) -> list[str]:
    explicit = _string_list(value)
    if explicit:
        return explicit
    presets = {
        "paper": ["papers", "standards", "official-docs", "engineering-articles", "general-web"],
        "papers": ["papers", "standards", "official-docs", "engineering-articles", "general-web"],
        "architecture": ["official-docs", "standards", "papers", "engineering-articles", "general-web"],
        "standards": ["standards", "official-docs", "papers", "engineering-articles", "general-web"],
        "market": ["general-web", "engineering-articles", "papers", "official-docs", "standards"],
        "official-docs": ["official-docs", "standards", "papers", "engineering-articles", "general-web"],
    }
    return presets.get(research_type, presets["architecture"])


def _bool_query(query: Mapping[str, list[str]], key: str, *, default: bool) -> bool:
    values = query.get(key)
    if not values:
        return default
    return values[-1].strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_query(query: Mapping[str, list[str]], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[-1])
    except ValueError:
        return default


def _non_negative_int_query(query: Mapping[str, list[str]], key: str, default: int) -> int:
    return max(0, _int_query(query, key, default))


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def _build_run_review_summary(paths: Mapping[str, Any], vault_path: Path) -> dict[str, Any]:
    blueprint_path = _optional_string(paths.get("service_blueprint"))
    evidence_path = _optional_string(paths.get("evidence_ledger"))
    run_path = _optional_string(paths.get("run_note"))
    blueprint_markdown = _read_markdown_preview(blueprint_path)
    evidence_markdown = _read_markdown_preview(evidence_path)
    run_markdown = _read_markdown_preview(run_path)
    return {
        "service_blueprint_markdown": blueprint_markdown,
        "evidence_ledger_markdown": evidence_markdown,
        "run_note_markdown": run_markdown,
        "quality_gates": _parse_quality_gates(run_markdown or evidence_markdown),
        "review_tasks": _build_review_tasks(evidence_markdown, run_markdown),
        "obsidian_links": _obsidian_links(paths, vault_path),
    }


def _read_markdown_preview(path: str | None, *, limit: int = RUN_PREVIEW_LIMIT) -> str:
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[preview truncated]"


def _parse_quality_gates(markdown: str) -> list[dict[str, str]]:
    gates: list[dict[str, str]] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line or "status" in line.lower():
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        status = cells[0].upper()
        if status not in {"PASS", "WARN", "FAIL"}:
            continue
        gates.append({"status": status, "name": cells[1], "detail": cells[2]})
    return gates


def _build_review_tasks(evidence_markdown: str, run_markdown: str) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    needs = _extract_markdown_section(evidence_markdown, "Needs Verification")
    for line in needs.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and "None captured yet" not in stripped:
            tasks.append({"kind": "verify", "title": stripped[2:], "severity": "warn"})
    for gate in _parse_quality_gates(run_markdown):
        if gate["status"] in {"FAIL", "WARN"}:
            tasks.append({"kind": "quality_gate", "title": f"{gate['name']}: {gate['detail']}", "severity": gate["status"].lower()})
    if not tasks:
        tasks.append({"kind": "review", "title": "Service Blueprint와 Evidence Ledger를 검토하고 유용한 노트를 reviewed로 승격하세요.", "severity": "ok"})
    return tasks[:8]


def _extract_markdown_section(markdown: str, heading: str) -> str:
    marker = f"## {heading}"
    start = markdown.find(marker)
    if start == -1:
        return ""
    section_start = markdown.find("\n", start)
    if section_start == -1:
        return ""
    next_heading = markdown.find("\n## ", section_start + 1)
    if next_heading == -1:
        return markdown[section_start:].strip()
    return markdown[section_start:next_heading].strip()


def _obsidian_links(paths: Mapping[str, Any], vault_path: Path) -> dict[str, str]:
    links: dict[str, str] = {}
    vault = vault_path.expanduser().resolve()
    vault_name = vault.name
    for key, value in paths.items():
        if isinstance(value, list):
            for index, item in enumerate(value, start=1):
                link = _obsidian_link(vault, vault_name, str(item))
                if link:
                    links[f"{key}_{index}"] = link
            continue
        link = _obsidian_link(vault, vault_name, str(value))
        if link:
            links[key] = link
    return links


def _obsidian_link(vault: Path, vault_name: str, path: str) -> str:
    if not path:
        return ""
    try:
        relative = Path(path).expanduser().resolve().relative_to(vault).as_posix()
    except (OSError, ValueError):
        return ""
    target = relative[:-3] if relative.endswith(".md") else relative
    return "obsidian://open?" + parse.urlencode({"vault": vault_name, "file": target})


_PORTAL_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>리서치 에이전트 포털</title>
  <link rel="stylesheet" href="/assets/portal.css">
</head>
<body>
  <header class="topbar">
    <div>
      <p class="eyebrow">옵시디언 리서치 런타임</p>
      <h1>리서치 에이전트 포털</h1>
    </div>
    <div class="top-actions">
      <a class="nav-button" href="/guide">가이드</a>
      <input id="tokenInput" class="token-input" type="password" autocomplete="off" placeholder="Bearer 토큰">
      <button id="refreshButton" type="button" title="Provider, Vault 상태, 후속 작업, 작업 목록을 다시 불러옵니다.">상태 갱신</button>
      <span id="refreshFeedback" class="refresh-feedback" aria-live="polite"></span>
    </div>
  </header>

  <main class="layout">
    <section class="panel workflow-panel layout-wide" aria-label="Research Workflow">
      <div class="section-head">
        <div>
          <p class="eyebrow">Research Workflow</p>
          <h2>목표 정의에서 Obsidian 리뷰까지</h2>
        </div>
        <span id="workflowBadge" class="badge">대기</span>
      </div>
      <div id="workflowTrack" class="workflow-track"></div>
    </section>

    <details class="system-panel layout-wide">
      <summary>
        <span>시스템 상태</span>
        <strong id="systemSummary">Provider, Vault, 작업 저장소 상태를 확인합니다.</strong>
        <span id="apiBadge" class="badge">확인 중</span>
      </summary>
      <section class="status-grid" aria-live="polite">
        <article class="stat">
          <span>Provider</span>
          <strong id="providerStatus">-</strong>
        </article>
        <article class="stat">
          <span>Obsidian Vault</span>
          <strong id="vaultStatus">-</strong>
        </article>
        <article class="stat">
          <span>Vault Health</span>
          <strong id="healthStatus">-</strong>
        </article>
        <article class="stat">
          <span>Vault 정비</span>
          <strong id="actionStatus">-</strong>
        </article>
        <article class="stat">
          <span>작업 저장소</span>
          <strong id="jobStoreStatus">-</strong>
        </article>
      </section>
    </details>

    <section class="panel run-panel layout-wide">
      <div class="section-head">
        <div>
          <p class="eyebrow">1. 목표 정의</p>
          <h2>리서치 요청</h2>
        </div>
      </div>
      <form id="runForm" class="run-form">
        <label class="field wide">
          <span>리서치 질문 / 목표</span>
          <textarea id="topicInput" rows="4" required placeholder="OpenAI Agents SDK와 LangGraph 비교"></textarea>
          <small class="field-help">비교, 구조 분류, 도입 판단처럼 결과에서 답해야 할 질문을 한 문장으로 적습니다.</small>
        </label>
        <fieldset class="preset-field wide">
          <legend>2. 리서치 전략</legend>
          <div class="preset-grid" id="presetButtons">
            <button class="preset-button active" type="button" data-preset="architecture">IT 아키텍처</button>
            <button class="preset-button" type="button" data-preset="paper">논문 합성</button>
            <button class="preset-button" type="button" data-preset="standards">표준·보안</button>
            <button class="preset-button" type="button" data-preset="market">시장 조사</button>
            <button class="preset-button" type="button" data-preset="official-docs">공식 문서</button>
          </div>
          <div class="strategy-summary">
            <span>출처 전략</span>
            <strong id="priorityPreview">official-docs → standards → papers</strong>
          </div>
        </fieldset>
        <label class="field">
          <span>분석 깊이</span>
          <select id="depthInput">
            <option value="quick">빠른 스캔</option>
            <option value="standard" selected>표준 분석</option>
            <option value="deep">심층 분석</option>
          </select>
        </label>
        <label class="field">
          <span>도메인 초점</span>
          <input id="domainInput" type="text" placeholder="보안, ML, 클라우드">
        </label>
        <fieldset class="option-field wide">
          <legend>3. 결과 형식</legend>
          <div class="option-grid">
            <label class="switch">
              <input id="bilingualInput" type="checkbox" checked>
              <span>한글 보고서 + 원문 병기</span>
            </label>
          </div>
        </fieldset>
        <details class="advanced-options wide">
          <summary>실행 안전 설정</summary>
          <div class="advanced-grid">
            <label class="field">
              <span>AI Provider</span>
              <select id="providerInput">
                <option value="auto">auto</option>
                <option value="openai">openai</option>
                <option value="gemini">gemini</option>
              </select>
            </label>
            <label class="field">
              <span>출처당 논문 수</span>
              <input id="papersInput" type="number" min="1" max="10" value="2">
            </label>
            <label class="switch">
              <input id="dryRunInput" type="checkbox" checked>
              <span>드라이런으로 먼저 확인</span>
            </label>
            <label class="switch">
              <input id="offlineInput" type="checkbox">
              <span>오프라인 검증</span>
            </label>
          </div>
        </details>
        <button id="runButton" class="primary" type="submit">실행 시작</button>
      </form>
    </section>

    <section class="review-board layout-wide" aria-label="Research review board">
      <section class="panel review-board-card result-panel">
        <div class="section-head">
          <div>
            <p class="eyebrow">Workflow Review</p>
            <h2>결과 검토</h2>
          </div>
          <span id="resultBadge" class="badge">대기</span>
        </div>
        <div id="progressSteps" class="progress-steps"></div>
        <div id="resultOutput" class="result-output result-empty">리서치 요청을 실행하면 단계별 검토 화면이 표시됩니다.</div>
      </section>

      <section class="panel review-board-card action-panel">
        <div class="section-head">
          <h2>리서치 리뷰</h2>
          <span id="reviewActionCount" class="badge">0</span>
        </div>
        <div id="reviewActionList" class="action-list">
          <div class="action-row">
            <p class="action-title">실행 결과를 기다리는 중입니다</p>
            <div class="action-detail">완료된 run을 선택하면 검토할 근거와 품질 이슈가 표시됩니다.</div>
          </div>
        </div>
      </section>

      <section class="panel review-board-card action-panel">
        <div class="section-head">
          <h2>Vault 정비</h2>
          <span id="actionCount" class="badge">0</span>
        </div>
        <div id="actionList" class="action-list">
          <div class="action-row">
            <p class="action-title">후속 작업을 불러오는 중입니다</p>
            <div class="action-detail">Vault 상태를 확인하고 있습니다.</div>
          </div>
        </div>
      </section>

      <section class="panel review-board-card">
        <div class="section-head">
          <h2>작업</h2>
          <span id="jobCount" class="badge">0</span>
        </div>
        <div id="jobList" class="job-list"></div>
      </section>
    </section>
  </main>

  <script src="/assets/portal.js"></script>
</body>
</html>
"""


_PORTAL_CSS = """
:root {
  color-scheme: light;
  --bg: #f7f8f5;
  --panel: #ffffff;
  --ink: #18211f;
  --muted: #64706d;
  --line: #dce2dc;
  --accent: #176f68;
  --accent-strong: #0f544f;
  --warn: #9a5a00;
  --bad: #9f2d2d;
  --good: #24724b;
  --soft: #eef5f2;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--ink);
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 24px clamp(20px, 4vw, 48px);
  border-bottom: 1px solid var(--line);
  background: #ffffff;
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1,
h2 {
  margin: 0;
  letter-spacing: 0;
}

h1 {
  font-size: 26px;
  line-height: 1.2;
}

h2 {
  font-size: 18px;
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.refresh-feedback {
  min-width: 78px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

.nav-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0 14px;
  background: #ffffff;
  color: var(--ink);
  font-size: 14px;
  font-weight: 800;
  text-decoration: none;
}

.nav-button:hover {
  border-color: var(--accent);
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
  gap: 18px;
  padding: 24px clamp(20px, 4vw, 48px) 40px;
}

.layout-wide {
  grid-column: 1 / -1;
}

.guide-page {
  display: grid;
  gap: 22px;
  padding: 24px clamp(20px, 4vw, 48px) 48px;
}

.guide-hero,
.guide-section {
  max-width: 1120px;
}

.guide-hero {
  padding: 8px 0 4px;
}

.guide-hero h2 {
  max-width: 980px;
  font-size: 30px;
  line-height: 1.25;
}

.guide-hero p {
  max-width: 900px;
  color: var(--muted);
  font-size: 16px;
  line-height: 1.7;
}

.guide-section {
  display: grid;
  gap: 14px;
}

.guide-section-head {
  display: grid;
  gap: 4px;
}

.step-list,
.check-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding-left: 22px;
}

.step-list li,
.check-list li {
  padding: 10px 0;
  line-height: 1.55;
}

.step-list strong {
  display: block;
  margin-bottom: 4px;
}

.step-list span,
.guide-section p {
  color: var(--muted);
}

.term-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.term-grid article {
  min-height: 142px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
}

.term-grid h3,
.term-grid p {
  margin: 0;
}

.term-grid h3 {
  margin-bottom: 8px;
  font-size: 15px;
}

.term-grid p {
  font-size: 13px;
  line-height: 1.55;
}

.scenario-table {
  display: grid;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
}

.scenario-row {
  display: grid;
  grid-template-columns: 0.75fr 1fr 1.15fr;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  line-height: 1.45;
}

.scenario-row:last-child {
  border-bottom: 0;
}

.scenario-head {
  background: var(--soft);
  color: var(--accent-strong);
  font-weight: 800;
}

.panel,
.stat {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}

.panel {
  padding: 18px;
}

.run-panel {
  align-self: start;
}

.result-panel {
  align-self: start;
  min-width: 0;
}

.review-board {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: stretch;
  gap: 18px;
}

.review-board-card {
  min-width: 0;
}

.review-board-card .section-head {
  min-height: 42px;
}

.review-board .progress-steps {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.review-board .action-list,
.review-board .job-list,
.review-board .result-output {
  max-height: 560px;
  overflow: auto;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.badge {
  min-width: 58px;
  border-radius: 999px;
  padding: 5px 10px;
  background: var(--soft);
  color: var(--accent-strong);
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 12px;
}

.workflow-panel {
  display: grid;
  gap: 14px;
}

.workflow-track {
  display: grid;
  grid-template-columns: repeat(6, minmax(156px, 1fr));
  gap: 12px;
}

.workflow-step {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 10px;
  min-height: 92px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
}

.workflow-step strong,
.workflow-step span {
  display: block;
  line-height: 1.35;
}

.workflow-step strong {
  font-size: 14px;
}

.workflow-step span {
  margin-top: 5px;
  color: var(--muted);
  font-size: 12px;
}

.workflow-step.done {
  border-color: var(--good);
  background: #f1f8f4;
}

.workflow-step.active {
  border-color: var(--accent);
  background: var(--soft);
}

.workflow-step.fail {
  border-color: var(--bad);
  background: #fff5f5;
}

.workflow-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 999px;
  background: #ffffff;
  color: var(--accent-strong);
  font-size: 13px;
  font-weight: 900;
}

.system-panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
}

.system-panel summary {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 50px;
  padding: 0 14px;
  cursor: pointer;
}

.system-panel summary span:first-child {
  font-weight: 900;
}

.system-panel summary strong {
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.system-panel .status-grid {
  padding: 0 14px 14px;
}

.stat {
  min-height: 64px;
  padding: 10px 12px;
}

.stat span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

.stat strong {
  display: block;
  margin-top: 8px;
  font-size: 14px;
  line-height: 1.2;
  overflow-wrap: anywhere;
}

.run-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.field,
.switch {
  display: grid;
  gap: 7px;
}

.field span,
.switch span {
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}

.field.wide {
  grid-column: 1 / -1;
}

.field-help {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.45;
}

.preset-field,
.option-field {
  display: grid;
  gap: 10px;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.preset-field legend,
.option-field legend {
  padding: 0;
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(116px, 1fr));
  gap: 8px;
}

.preset-button {
  min-height: 44px;
  padding: 0 10px;
}

.preset-button.active {
  border-color: var(--accent);
  background: var(--soft);
  color: var(--accent-strong);
}

.strategy-summary {
  display: grid;
  gap: 4px;
  min-height: 34px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.strategy-summary span {
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}

.strategy-summary strong {
  color: var(--accent-strong);
  font-size: 13px;
}

.option-grid,
.advanced-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.advanced-options {
  grid-column: 1 / -1;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
}

.advanced-options summary {
  min-height: 42px;
  padding: 11px 12px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 800;
  cursor: pointer;
}

.advanced-options .advanced-grid {
  padding: 0 12px 12px;
}

textarea,
select,
input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  color: var(--ink);
  font: inherit;
}

textarea {
  min-height: 128px;
  resize: vertical;
  padding: 12px;
}

select,
input {
  min-height: 42px;
  padding: 0 11px;
}

.token-input {
  width: min(260px, 34vw);
}

.switch {
  grid-template-columns: 18px 1fr;
  align-items: center;
  min-height: 42px;
}

.switch input {
  width: 18px;
  min-height: 18px;
}

button {
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0 14px;
  background: #ffffff;
  color: var(--ink);
  font: inherit;
  font-weight: 800;
  cursor: pointer;
}

button:hover {
  border-color: var(--accent);
}

button.primary {
  grid-column: 1 / -1;
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

button.primary:hover {
  background: var(--accent-strong);
}

.job-list {
  display: grid;
  gap: 10px;
}

.action-list {
  display: grid;
  gap: 10px;
}

.action-row {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--accent);
  border-radius: 8px;
  background: #fbfcfa;
}

.action-row.priority-1 {
  border-left-color: var(--bad);
}

.action-row.priority-2 {
  border-left-color: var(--warn);
}

.action-title {
  margin: 0;
  font-weight: 800;
  overflow-wrap: anywhere;
}

.action-detail,
.action-command {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.action-command code {
  color: var(--accent-strong);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.job-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
}

.job-title {
  margin: 0 0 6px;
  font-weight: 800;
  overflow-wrap: anywhere;
}

.job-meta {
  color: var(--muted);
  font-size: 12px;
}

.progress-steps {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.progress-step {
  min-height: 44px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  line-height: 1.35;
}

.progress-step.active {
  border-color: var(--warn);
  color: var(--warn);
}

.progress-step.done {
  border-color: var(--good);
  color: var(--good);
  background: #f1f8f4;
}

.progress-step.fail {
  border-color: var(--bad);
  color: var(--bad);
  background: #fff5f5;
}

.result-output {
  min-height: 260px;
  max-height: 680px;
  margin: 0;
  padding: 0;
  overflow: auto;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-size: 14px;
  line-height: 1.55;
}

.result-empty,
.json-fallback {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
  color: var(--muted);
  white-space: pre-wrap;
}

.result-stack {
  display: grid;
  gap: 18px;
}

.result-summary {
  display: grid;
  gap: 10px;
}

.result-section {
  display: grid;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.result-section:first-child {
  padding-top: 0;
  border-top: 0;
}

.result-section h3 {
  margin: 0;
  font-size: 16px;
}

.section-kicker {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

.workflow-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.workflow-list li {
  padding: 8px 10px;
  border-left: 3px solid var(--accent);
  background: #fbfcfa;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.source-list,
.review-task-grid {
  display: grid;
  gap: 10px;
}

.source-link {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
  color: var(--accent-strong);
  font-weight: 800;
  text-decoration: none;
  overflow-wrap: anywhere;
}

.source-link span {
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}

.context-grid,
.quality-grid,
.artifact-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.context-card,
.quality-card,
.artifact-link {
  min-height: 72px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfa;
}

.context-card span,
.quality-card span {
  display: block;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}

.context-card strong,
.quality-card strong {
  display: block;
  margin-top: 7px;
  overflow-wrap: anywhere;
}

.quality-card.pass {
  border-left: 4px solid var(--good);
}

.quality-card.ok {
  border-left: 4px solid var(--good);
}

.quality-card.warn {
  border-left: 4px solid var(--warn);
}

.quality-card.fail {
  border-left: 4px solid var(--bad);
}

.artifact-link {
  display: grid;
  align-content: center;
  color: var(--accent-strong);
  font-weight: 800;
  text-decoration: none;
  overflow-wrap: anywhere;
}

.markdown-preview {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
}

.markdown-preview h3,
.markdown-preview h4 {
  margin: 16px 0 8px;
}

.markdown-preview h3:first-child,
.markdown-preview h4:first-child {
  margin-top: 0;
}

.markdown-preview p,
.markdown-preview li {
  color: var(--ink);
}

.markdown-preview pre {
  overflow: auto;
  padding: 12px;
  border-radius: 8px;
  background: #111817;
  color: #edf7f3;
}

.markdown-preview table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.markdown-preview th,
.markdown-preview td {
  padding: 8px;
  border: 1px solid var(--line);
  text-align: left;
}

.ok {
  color: var(--good);
}

.warn {
  color: var(--warn);
}

.fail {
  color: var(--bad);
}

@media (max-width: 1180px) {
  .workflow-track {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .review-board {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 920px) {
  .topbar,
  .top-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .token-input {
    width: 100%;
  }

  .layout {
    grid-template-columns: 1fr;
  }

  .status-grid,
  .run-form,
  .workflow-track,
  .review-board,
  .option-grid,
  .advanced-grid,
  .progress-steps {
    grid-template-columns: 1fr;
  }

  .system-panel summary {
    grid-template-columns: 1fr;
    padding: 12px;
  }

  .guide-hero h2 {
    font-size: 24px;
  }

  .scenario-row {
    grid-template-columns: 1fr;
  }
}
"""


_PORTAL_JS = """
const state = {
  currentJobId: "",
  pollTimer: null,
  preset: "architecture",
  isRefreshing: false
};

const WORKFLOW_STEPS = [
  { key: "goal", label: "목표 정의", detail: "리서치 질문을 명확히 적습니다." },
  { key: "strategy", label: "리서치 전략", detail: "출처 우선순위와 깊이를 정합니다." },
  { key: "collect", label: "출처 수집", detail: "공식 문서, 표준, 논문을 모읍니다." },
  { key: "evidence", label: "근거 추출", detail: "claim과 citation을 구조화합니다." },
  { key: "blueprint", label: "Blueprint 합성", detail: "실서비스 기본형을 만듭니다." },
  { key: "review", label: "Obsidian 리뷰", detail: "노트와 다음 작업을 검토합니다." }
];

const PRESETS = {
  architecture: {
    label: "IT 아키텍처",
    depth: "standard",
    papers: 2,
    priority: ["official-docs", "standards", "papers", "engineering-articles", "general-web"],
    placeholder: "OpenAI Agents SDK와 LangGraph 비교"
  },
  paper: {
    label: "논문 합성",
    depth: "deep",
    papers: 5,
    priority: ["papers", "standards", "official-docs", "engineering-articles", "general-web"],
    placeholder: "Agentic RAG 최신 논문 구조 분류"
  },
  standards: {
    label: "표준·보안",
    depth: "standard",
    papers: 2,
    priority: ["standards", "official-docs", "papers", "engineering-articles", "general-web"],
    placeholder: "AI Agent 보안 통제와 NIST/OWASP 기준"
  },
  market: {
    label: "시장 조사",
    depth: "quick",
    papers: 1,
    priority: ["general-web", "engineering-articles", "papers", "official-docs", "standards"],
    placeholder: "엔터프라이즈 AI Agent 도입 동향"
  },
  "official-docs": {
    label: "공식 문서",
    depth: "quick",
    papers: 1,
    priority: ["official-docs", "standards", "papers", "engineering-articles", "general-web"],
    placeholder: "OpenAI Responses API 최신 공식 문서 요약"
  }
};

const el = (id) => document.getElementById(id);

function authHeaders() {
  const token = el("tokenInput").value.trim();
  if (token) {
    localStorage.setItem("researchAgentPortalToken", token);
    return { "Authorization": `Bearer ${token}` };
  }
  localStorage.removeItem("researchAgentPortalToken");
  return {};
}

async function requestJson(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
    ...authHeaders()
  };
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch (error) {
    payload = { error: "invalid_json_response", detail: text };
  }
  if (!response.ok) {
    throw Object.assign(new Error(payload.error || response.statusText), { payload, status: response.status });
  }
  return payload;
}

function setBadge(id, text, status = "") {
  const node = el(id);
  node.textContent = text;
  node.className = `badge ${status}`.trim();
}

function printResult(label, value) {
  el("resultBadge").textContent = label;
  if (value && typeof value === "object" && value.job_id) {
    renderWorkflow(value);
    renderJobResult(value);
    renderProgress(value);
    renderReviewActions(value);
    return;
  }
  renderWorkflow();
  el("resultOutput").className = "result-output json-fallback";
  el("resultOutput").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function renderWorkflow(job = {}) {
  const status = job.status || "";
  const stage = job.pipeline_stage || "";
  const failed = ["failed", "interrupted", "cancelled"].includes(status);
  const completed = status === "completed";
  let activeIdx = 0;
  if (status === "queued") activeIdx = 2;
  if (status === "running" && stage === "collecting") activeIdx = 2;
  if (status === "running" && stage === "synthesizing") activeIdx = 4;
  if (status === "running" && stage === "writing") activeIdx = 5;
  if (completed) activeIdx = WORKFLOW_STEPS.length;
  el("workflowBadge").textContent = completed ? "완료" : failed ? "실패" : status === "running" ? "진행 중" : status === "queued" ? "대기열" : "대기";
  el("workflowBadge").className = `badge ${completed ? "ok" : failed ? "fail" : status ? "warn" : ""}`.trim();
  el("workflowTrack").innerHTML = WORKFLOW_STEPS.map((step, index) => {
    let klass = "";
    if (completed || index < activeIdx) klass = "done";
    else if (failed && index === activeIdx) klass = "fail";
    else if (index === activeIdx) klass = "active";
    return `
      <div class="workflow-step ${klass}">
        <span class="workflow-index">${index + 1}</span>
        <div>
          <strong>${escapeHtml(step.label)}</strong>
          <span>${escapeHtml(step.detail)}</span>
        </div>
      </div>
    `;
  }).join("");
}

function renderNextActions(payload) {
  const items = payload.report && Array.isArray(payload.report.items) ? payload.report.items : [];
  el("actionStatus").textContent = String(items.length);
  el("actionCount").textContent = String(items.length);
  const list = el("actionList");
  if (!items.length) {
    list.innerHTML = `
      <div class="action-row">
        <p class="action-title">후속 작업 없음</p>
        <div class="action-detail">현재 설정 기준으로 Vault가 정리된 상태입니다.</div>
      </div>
    `;
    return;
  }
  list.innerHTML = items.slice(0, 6).map((item) => `
    <div class="action-row priority-${escapeHtml(item.priority || "")}">
      <p class="action-title">${escapeHtml(item.title || item.category || "후속 작업")}</p>
      <div class="action-detail">${escapeHtml(item.category || "-")} / ${escapeHtml(item.count ?? 0)}건${item.detail ? ` / ${escapeHtml(item.detail)}` : ""}</div>
      ${item.command ? `<div class="action-command"><code>${escapeHtml(item.command)}</code></div>` : ""}
    </div>
  `).join("");
}

async function refreshStatus(options = {}) {
  if (state.isRefreshing) {
    return;
  }
  state.isRefreshing = true;
  setRefreshState(true, "갱신 중");
  let feedbackMessage = "";
  try {
    try {
      const health = await requestJson("/health");
      setBadge("apiBadge", "온라인", "ok");
      el("providerStatus").textContent = `${health.provider}${health.provider_available ? "" : " 사용 불가"}`;
      el("vaultStatus").textContent = health.vault_path || "-";
    } catch (error) {
      setBadge("apiBadge", "오프라인", "fail");
      printResult("오류", error.payload || error.message);
    }
    updateSystemSummary();

    try {
      const vault = await requestJson("/vault-health");
      el("healthStatus").textContent = vault.status || "-";
      el("healthStatus").className = (vault.status || "").toLowerCase();
    } catch (error) {
      el("healthStatus").textContent = "인증";
    }
    updateSystemSummary();

    try {
      const actions = await requestJson("/next-actions");
      renderNextActions(actions);
    } catch (error) {
      el("actionStatus").textContent = "인증";
      el("actionCount").textContent = "-";
      el("actionList").innerHTML = `
        <div class="action-row">
          <p class="action-title">후속 작업을 불러올 수 없습니다</p>
          <div class="action-detail">${escapeHtml(error.message)}</div>
        </div>
      `;
    }
    updateSystemSummary();

    await refreshJobStoreHealth();
    await refreshJobs();
    if (options.reloadCurrentJob && state.currentJobId) {
      await loadJob(state.currentJobId);
    }
    updateSystemSummary();
    feedbackMessage = `${formatTime(new Date())} 갱신됨`;
  } catch (error) {
    feedbackMessage = "갱신 실패";
    printResult("오류", error.payload || error.message);
  } finally {
    setRefreshState(false, feedbackMessage || "갱신 완료");
    state.isRefreshing = false;
  }
}

function updateSystemSummary() {
  const provider = el("providerStatus").textContent || "-";
  const health = el("healthStatus").textContent || "-";
  const actions = el("actionStatus").textContent || "-";
  const jobs = el("jobStoreStatus").textContent || "-";
  el("systemSummary").textContent = `${provider} / Vault ${health} / 정비 ${actions} / Jobs ${jobs}`;
}

function setRefreshState(isLoading, message = "") {
  const button = el("refreshButton");
  button.disabled = isLoading;
  button.textContent = isLoading ? "갱신 중" : "상태 갱신";
  el("refreshFeedback").textContent = message;
}

function formatTime(date) {
  return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderReviewActions(job) {
  const summary = job.summary || {};
  const review = summary.review || {};
  const tasks = Array.isArray(review.review_tasks) ? review.review_tasks : [];
  el("reviewActionCount").textContent = String(tasks.length);
  const list = el("reviewActionList");
  if (!tasks.length) {
    list.innerHTML = `
      <div class="action-row">
        <p class="action-title">검토 작업 없음</p>
        <div class="action-detail">완료된 run을 선택하면 검토할 근거와 품질 이슈가 표시됩니다.</div>
      </div>
    `;
    return;
  }
  list.innerHTML = renderReviewTaskCards(tasks);
}

function renderProgress(job = {}) {
  const status = job.status || "";
  const stage = job.pipeline_stage || "";
  const done = status === "completed";
  const failed = ["failed", "interrupted", "cancelled"].includes(status);

  // steps in order — key matches pipeline on_stage values
  const steps = [
    ["queued",      "접수"],
    ["collecting",  "소스·근거 수집"],
    ["synthesizing","합성"],
    ["writing",     "저장"]
  ];

  const stageIndex = (s) => {
    const i = steps.findIndex(([k]) => k === s);
    return i === -1 ? 0 : i;
  };
  const activeIdx = done ? steps.length : stageIndex(stage || (status === "queued" ? "queued" : "collecting"));

  el("progressSteps").innerHTML = steps.map(([key, label], index) => {
    let klass = "";
    if (done) {
      klass = "done";
    } else if (failed) {
      klass = index < activeIdx ? "done" : index === activeIdx ? "fail" : "";
    } else if (status === "running" || status === "queued") {
      klass = index < activeIdx ? "done" : index === activeIdx ? "active" : "";
    }
    return `<div class="progress-step ${klass}">${escapeHtml(label)}</div>`;
  }).join("");
}

async function refreshJobStoreHealth() {
  try {
    const store = await requestJson("/job-store-health?retention_days=90&retention_limit=200&max_removed=5");
    const preview = store.cleanup_preview || {};
    const prune = Number(preview.prune_candidates || 0);
    const total = Number(store.total_jobs || 0);
    el("jobStoreStatus").textContent = `${prune} 정리 / ${total}`;
    el("jobStoreStatus").className = prune > 0 ? "warn" : "ok";
  } catch (error) {
    el("jobStoreStatus").textContent = "인증";
    el("jobStoreStatus").className = "";
  }
}

async function refreshJobs() {
  try {
    const payload = await requestJson("/jobs");
    const jobs = payload.jobs || [];
    el("jobCount").textContent = String(jobs.length);
    renderJobs(jobs);
  } catch (error) {
    el("jobList").innerHTML = `<div class="job-row"><div><p class="job-title">작업 목록을 불러올 수 없습니다</p><div class="job-meta">${escapeHtml(error.message)}</div></div></div>`;
  }
}

function renderJobs(jobs) {
  const list = el("jobList");
  if (!jobs.length) {
    list.innerHTML = `<div class="job-row"><div><p class="job-title">작업 없음</p><div class="job-meta">준비됨</div></div></div>`;
    return;
  }
  list.innerHTML = jobs.map((job) => `
    <button class="job-row" type="button" data-job-id="${escapeHtml(job.job_id)}">
      <div>
        <p class="job-title">${escapeHtml(job.topic_preview || job.objective_preview || job.job_id)}</p>
        <div class="job-meta">${escapeHtml(job.status)} / ${escapeHtml(job.mode)} / ${escapeHtml(job.provider)}</div>
      </div>
      <span class="badge ${statusClass(job.status)}">${escapeHtml(job.status)}</span>
    </button>
  `).join("");
  list.querySelectorAll("[data-job-id]").forEach((node) => {
    node.addEventListener("click", () => loadJob(node.dataset.jobId));
  });
}

async function loadJob(jobId) {
  if (!jobId) return;
  state.currentJobId = jobId;
  try {
    const job = await requestJson(`/jobs/${encodeURIComponent(jobId)}`);
    printResult(job.status, job);
    if (["queued", "running"].includes(job.status)) {
      startPolling();
    }
    return job;
  } catch (error) {
    printResult("오류", error.payload || error.message);
  }
  return null;
}

function renderJobResult(job) {
  const summary = job.summary || {};
  const review = summary.review || {};
  if (job.status !== "completed") {
    el("resultOutput").className = "result-output";
    el("resultOutput").innerHTML = `
      <div class="result-stack">
        ${resultSection("현재 진행 상태", `
          <div class="context-grid">
            ${contextCard("상태", job.status)}
            ${contextCard("단계", job.pipeline_stage || "queued")}
            ${contextCard("모드", job.mode)}
            ${contextCard("리서치 유형", job.research_type || "-")}
          </div>
        `, "Workflow")}
        ${job.error ? `<div class="json-fallback">${escapeHtml(job.error.type || "Error")}: ${escapeHtml(job.error.message || "")}</div>` : ""}
      </div>
    `;
    return;
  }

  if (summary.type === "dry_run") {
    renderDryRunResult(job, summary);
    return;
  }

  const context = summary.research_context || {};
  const markdown = review.service_blueprint_markdown || "";
  const evidenceMarkdown = review.evidence_ledger_markdown || "";
  const quality = Array.isArray(review.quality_gates) ? review.quality_gates : [];
  const tasks = Array.isArray(review.review_tasks) ? review.review_tasks : [];
  const links = review.obsidian_links || {};
  el("resultOutput").className = "result-output";
  el("resultOutput").innerHTML = `
    <div class="result-stack">
      ${resultSection("리서치 전략", `
        <div class="context-grid">
          ${contextCard("리서치 유형", context.research_type || job.research_type || "-")}
          ${contextCard("분석 깊이", context.research_depth || job.research_depth || "-")}
          ${contextCard("도메인 초점", context.domain_focus || job.domain_focus || "-")}
          ${contextCard("출처 전략", (context.source_priority || job.source_priority || []).join(" → ") || "-")}
        </div>
      `, "1. Strategy")}
      ${resultSection("수집된 출처", renderSourceNotes(summary.paths || {}, links), "2. Sources")}
      ${resultSection("추출된 핵심 근거", `<div class="markdown-preview">${renderMarkdown(markdownExcerpt(evidenceMarkdown, 28) || "Evidence Ledger preview가 없습니다.")}</div>`, "3. Evidence")}
      ${resultSection("품질 점검", `<div class="quality-grid">${quality.length ? quality.map(qualityCard).join("") : contextCard("상태", "품질 게이트 정보 없음")}</div>`, "4. Quality Gate")}
      ${resultSection("Service Blueprint", `<div class="markdown-preview">${renderMarkdown(markdown || "Service Blueprint preview가 없습니다.")}</div>`, "5. Blueprint")}
      ${resultSection("Obsidian 저장 위치", `<div class="artifact-grid">${renderArtifactLinks(links, summary.paths || {})}</div>`, "6. Vault")}
      ${resultSection("다음 리뷰 작업", `<div class="review-task-grid">${renderReviewTaskCards(tasks)}</div>`, "7. Human Review")}
    </div>
  `;
}

function renderDryRunResult(job, summary) {
  const context = summary.research_context || {};
  const artifacts = Array.isArray(summary.artifacts) ? summary.artifacts : [];
  const safety = Array.isArray(summary.safety) ? summary.safety : [];
  el("resultOutput").className = "result-output";
  el("resultOutput").innerHTML = `
    <div class="result-stack">
      ${resultSection("리서치 전략", `
        <div class="context-grid">
          ${contextCard("드라이런", "파일 쓰기 없음")}
          ${contextCard("리서치 유형", context.research_type || job.research_type || "-")}
          ${contextCard("분석 깊이", context.research_depth || job.research_depth || "-")}
          ${contextCard("출처 전략", (context.source_priority || job.source_priority || []).join(" → ") || "-")}
        </div>
      `, "1. Strategy")}
      ${resultSection("출처 수집 계획", renderPlannedArtifacts(artifacts, "source-note"), "2. Sources")}
      ${resultSection("Obsidian 저장 계획", renderPlannedArtifacts(artifacts), "3. Vault")}
      ${resultSection("안전 점검", `<div class="quality-grid">${safety.map((item) => qualityCard({ status: item.status, name: item.name, detail: item.detail })).join("")}</div>`, "4. Safety")}
    </div>
  `;
}

function resultSection(title, body, kicker = "") {
  return `
    <section class="result-section">
      ${kicker ? `<div class="section-kicker">${escapeHtml(kicker)}</div>` : ""}
      <h3>${escapeHtml(title)}</h3>
      ${body}
    </section>
  `;
}

function contextCard(label, value) {
  return `<div class="context-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "-")}</strong></div>`;
}

function qualityCard(item) {
  const status = String(item.status || "").toLowerCase();
  return `
    <div class="quality-card ${escapeHtml(status)}">
      <span>${escapeHtml(item.status || "-")}</span>
      <strong>${escapeHtml(item.name || "quality gate")}</strong>
      <div class="action-detail">${escapeHtml(item.detail || "")}</div>
    </div>
  `;
}

function renderArtifactLinks(links, paths) {
  const labels = {
    run_note: "Run Note",
    evidence_ledger: "Evidence Ledger",
    service_blueprint: "Service Blueprint",
    topic_map: "Topic Map"
  };
  const rows = Object.entries(labels).map(([key, label]) => {
    if (links[key]) {
      return `<a class="artifact-link" href="${escapeHtml(links[key])}">${escapeHtml(label)} 열기</a>`;
    }
    if (paths[key]) {
      return `<div class="artifact-link">${escapeHtml(label)}<span class="action-detail">${escapeHtml(paths[key])}</span></div>`;
    }
    return "";
  }).filter(Boolean);
  return rows.join("") || `<div class="json-fallback">산출물 링크가 없습니다.</div>`;
}

function renderSourceNotes(paths, links) {
  const sourceNotes = Array.isArray(paths.source_notes) ? paths.source_notes : [];
  if (!sourceNotes.length) {
    return `<div class="json-fallback">source note 경로가 없습니다.</div>`;
  }
  return `
    <div class="source-list">
      ${sourceNotes.map((path, index) => {
        const link = links[`source_notes_${index + 1}`] || "";
        const label = `Source Note ${index + 1}`;
        if (link) {
          return `<a class="source-link" href="${escapeHtml(link)}">${escapeHtml(label)}<span>${escapeHtml(path)}</span></a>`;
        }
        return `<div class="source-link">${escapeHtml(label)}<span>${escapeHtml(path)}</span></div>`;
      }).join("")}
    </div>
  `;
}

function renderPlannedArtifacts(artifacts, kind = "") {
  const filtered = kind ? artifacts.filter((item) => item.kind === kind) : artifacts;
  if (!filtered.length) {
    return `<div class="json-fallback">planned artifact 없음</div>`;
  }
  return `
    <ul class="workflow-list">
      ${filtered.slice(0, 14).map((item) => `<li><strong>${escapeHtml(item.kind || "artifact")}</strong>: ${escapeHtml(item.path || "")}${item.note ? `<br><span class="action-detail">${escapeHtml(item.note)}</span>` : ""}</li>`).join("")}
      ${filtered.length > 14 ? `<li>${escapeHtml(filtered.length - 14)}개 항목이 더 있습니다.</li>` : ""}
    </ul>
  `;
}

function renderReviewTaskCards(tasks) {
  if (!tasks.length) {
    return `
      <div class="action-row">
        <p class="action-title">검토 작업 없음</p>
        <div class="action-detail">Service Blueprint와 Evidence Ledger를 읽고 필요한 노트를 reviewed로 승격하세요.</div>
      </div>
    `;
  }
  return tasks.map((task) => `
    <div class="action-row priority-${task.severity === "fail" ? "1" : task.severity === "warn" ? "2" : ""}">
      <p class="action-title">${escapeHtml(task.title || "검토 작업")}</p>
      <div class="action-detail">${escapeHtml(task.kind || "review")} / ${escapeHtml(task.severity || "ok")}</div>
    </div>
  `).join("");
}

function markdownExcerpt(markdown, maxLines = 32) {
  const clean = stripFrontmatter(String(markdown || ""));
  const claimOnlyEnd = clean.indexOf("\\n## Claim Translations");
  const focused = claimOnlyEnd >= 0 ? clean.slice(0, claimOnlyEnd) : clean;
  const lines = focused.split("\\n").filter((line) => !line.startsWith("translation_language:"));
  if (lines.length <= maxLines) {
    return lines.join("\\n").trim();
  }
  return `${lines.slice(0, maxLines).join("\\n").trim()}\\n\\n- preview truncated`;
}

function inlineFormat(text) {
  // Escape HTML first, then restore inline markup as safe tags
  return escapeHtml(text)
    .replace(/[*][*](.+?)[*][*]/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderMarkdown(markdown) {
  const lines = stripFrontmatter(String(markdown || "")).split("\\n");
  const html = [];
  let inCode = false;
  let inList = false;
  let tableRows = [];

  const flushTable = () => {
    if (!tableRows.length) return;
    html.push("<table>");
    tableRows.forEach((row, index) => {
      if (/^\\|\\s*-/.test(row)) return;
      const cells = row.split("|").slice(1, -1).map((cell) => cell.trim());
      const tag = index === 0 ? "th" : "td";
      html.push(`<tr>${cells.map((cell) => `<${tag}>${escapeHtml(cell)}</${tag}>`).join("")}</tr>`);
    });
    html.push("</table>");
    tableRows = [];
  };

  const flushList = () => {
    if (!inList) return;
    html.push("</ul>");
    inList = false;
  };

  for (const line of lines) {
    if (line.startsWith("```")) {
      flushTable();
      flushList();
      html.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      html.push(`${escapeHtml(line)}\\n`);
      continue;
    }
    if (line.startsWith("|")) {
      flushList();
      tableRows.push(line);
      continue;
    }
    flushTable();
    if (line.startsWith("## ")) {
      flushList();
      html.push(`<h4>${escapeHtml(line.slice(3))}</h4>`);
    } else if (line.startsWith("# ")) {
      flushList();
      html.push(`<h3>${escapeHtml(line.slice(2))}</h3>`);
    } else if (line.startsWith("- ")) {
      if (!inList) { html.push("<ul>"); inList = true; }
      html.push(`<li>${inlineFormat(line.slice(2))}</li>`);
    } else if (line.trim()) {
      flushList();
      html.push(`<p>${inlineFormat(line)}</p>`);
    } else {
      flushList();
    }
  }
  flushTable();
  flushList();
  if (inCode) html.push("</code></pre>");
  return html.join("");
}

function startPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
  }
  state.pollTimer = setInterval(async () => {
    if (!state.currentJobId) return;
    const job = await loadJob(state.currentJobId);
    await refreshJobs();
    if (job && !["queued", "running"].includes(job.status)) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }, 2000);
}

async function submitRun(event) {
  event.preventDefault();
  const preset = PRESETS[state.preset] || PRESETS.architecture;
  const payload = {
    topic: el("topicInput").value.trim(),
    provider: el("providerInput").value,
    research_type: state.preset,
    research_depth: el("depthInput").value,
    source_priority: preset.priority,
    domain_focus: el("domainInput").value.trim(),
    bilingual: el("bilingualInput").checked,
    offline: el("offlineInput").checked,
    dry_run: el("dryRunInput").checked,
    max_papers_per_source: Number(el("papersInput").value || 2)
  };
  if (!payload.topic) return;
  el("runButton").disabled = true;
  try {
    const queued = await requestJson("/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    state.currentJobId = queued.job_id;
    printResult("대기열 등록", queued);
    renderProgress(queued);
    renderReviewActions(queued);
    await refreshJobs();
    await refreshJobStoreHealth();
    startPolling();
  } catch (error) {
    printResult("오류", error.payload || error.message);
  } finally {
    el("runButton").disabled = false;
  }
}

function applyPreset(name) {
  state.preset = name;
  const preset = PRESETS[name] || PRESETS.architecture;
  el("depthInput").value = preset.depth;
  el("papersInput").value = String(preset.papers);
  el("priorityPreview").textContent = preset.priority.join(" → ");
  el("topicInput").placeholder = preset.placeholder;
  document.querySelectorAll(".preset-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === name);
  });
}

function stripFrontmatter(markdown) {
  if (!markdown.startsWith("---\\n")) {
    return markdown;
  }
  const end = markdown.indexOf("\\n---", 4);
  if (end === -1) {
    return markdown;
  }
  return markdown.slice(end + 4).trimStart();
}

function statusClass(status) {
  if (status === "completed") return "ok";
  if (status === "failed" || status === "interrupted") return "fail";
  if (status === "running" || status === "queued") return "warn";
  return "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function init() {
  const storedToken = localStorage.getItem("researchAgentPortalToken") || "";
  el("tokenInput").value = storedToken;
  renderWorkflow();
  document.querySelectorAll(".preset-button").forEach((button) => {
    button.addEventListener("click", () => applyPreset(button.dataset.preset));
  });
  applyPreset(state.preset);
  el("runForm").addEventListener("submit", submitRun);
  el("refreshButton").addEventListener("click", () => refreshStatus({ reloadCurrentJob: true }));
  refreshStatus();
}

document.addEventListener("DOMContentLoaded", init);
"""
