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
from .secrets import select_llm_provider
from .timeutil import now_local
from .vault_health import build_vault_health


PORTAL_JOB_STORE_FILE = "research_portal_jobs.json"
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
ACTIVE_JOB_STATUSES = {"queued", "running"}


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
    rerun_of: str | None = None
    run_id: str | None = None
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
            "run_id": self.run_id,
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
            rerun_of=_optional_safe_id(payload.get("rerun_of")),
            run_id=_optional_string(payload.get("run_id")),
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
        max_papers = _positive_int(payload.get("max_papers_per_source"), default=2)
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
                rerun_of=rerun_of,
            )
            self._jobs[job_id] = record
            self._save_jobs()
            future = self._executor.submit(self._run_job, job_id, topic, provider, offline, dry_run, max_papers, rerun_of)
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
    ) -> None:
        with self._lock:
            record = self._jobs[job_id]
            record.status = "running"
            record.started_at = _now_iso()
            self._save_jobs()

        try:
            settings = self._settings_for_provider(provider)
            pipeline = ResearchPipeline(settings)
            if dry_run:
                plan = pipeline.dry_run(topic, offline=offline, max_papers_per_source=max_papers_per_source)
                summary = {
                    "type": "dry_run",
                    "topic": plan.topic,
                    "vault_path": plan.vault_path,
                    "mode": plan.mode,
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
                )
                summary = {
                    "type": "run",
                    "topic": topic,
                    "artifacts": asdict(artifacts),
                    "paths": {
                        "run_note": artifacts.run_note,
                        "source_notes": artifacts.source_notes,
                        "evidence_ledger": artifacts.evidence_ledger,
                        "service_blueprint": artifacts.service_blueprint,
                        "topic_map": artifacts.topic_map,
                    },
                }
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

    def _settings_for_provider(self, provider: str) -> Settings:
        if provider == self.settings.llm.provider:
            return self.settings
        return replace(self.settings, llm=LLMSettings(provider=provider))

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
      <input id="tokenInput" class="token-input" type="password" autocomplete="off" placeholder="Bearer 토큰">
      <button id="refreshButton" type="button">새로고침</button>
    </div>
  </header>

  <main class="layout">
    <section class="panel run-panel">
      <div class="section-head">
        <h2>실행</h2>
        <span id="apiBadge" class="badge">확인 중</span>
      </div>
      <form id="runForm" class="run-form">
        <label class="field wide">
          <span>주제</span>
          <textarea id="topicInput" rows="4" required placeholder="OpenAI Agents SDK와 LangGraph 비교"></textarea>
        </label>
        <label class="field">
          <span>제공자</span>
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
          <span>드라이런</span>
        </label>
        <label class="switch">
          <input id="offlineInput" type="checkbox">
          <span>오프라인</span>
        </label>
        <button id="runButton" class="primary" type="submit">실행 시작</button>
      </form>
    </section>

    <section class="status-grid" aria-live="polite">
      <article class="stat">
        <span>제공자</span>
        <strong id="providerStatus">-</strong>
      </article>
      <article class="stat">
        <span>볼트</span>
        <strong id="vaultStatus">-</strong>
      </article>
      <article class="stat">
        <span>상태</span>
        <strong id="healthStatus">-</strong>
      </article>
      <article class="stat">
        <span>후속 작업</span>
        <strong id="actionStatus">-</strong>
      </article>
      <article class="stat">
        <span>작업 저장소</span>
        <strong id="jobStoreStatus">-</strong>
      </article>
    </section>

    <section class="panel action-panel">
      <div class="section-head">
        <h2>후속 작업</h2>
        <span id="actionCount" class="badge">0</span>
      </div>
      <div id="actionList" class="action-list">
        <div class="action-row">
          <p class="action-title">후속 작업을 불러오는 중입니다</p>
          <div class="action-detail">Vault 상태를 확인하고 있습니다.</div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>작업</h2>
        <span id="jobCount" class="badge">0</span>
      </div>
      <div id="jobList" class="job-list"></div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>결과</h2>
        <span id="resultBadge" class="badge">대기</span>
      </div>
      <pre id="resultOutput" class="result-output">선택된 실행이 없습니다.</pre>
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

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
  gap: 18px;
  padding: 24px clamp(20px, 4vw, 48px) 40px;
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
  grid-row: span 2;
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

.stat {
  min-height: 86px;
  padding: 14px;
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
  margin-top: 12px;
  font-size: 18px;
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

.result-output {
  min-height: 260px;
  max-height: 520px;
  margin: 0;
  padding: 14px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #101716;
  color: #e8f2ee;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
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
  .run-form {
    grid-template-columns: 1fr;
  }
}
"""


_PORTAL_JS = """
const state = {
  currentJobId: "",
  pollTimer: null
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
  el("resultOutput").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
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

async function refreshStatus() {
  try {
    const health = await requestJson("/health");
    setBadge("apiBadge", "온라인", "ok");
    el("providerStatus").textContent = `${health.provider}${health.provider_available ? "" : " 사용 불가"}`;
    el("vaultStatus").textContent = health.vault_path || "-";
  } catch (error) {
    setBadge("apiBadge", "오프라인", "fail");
    printResult("오류", error.payload || error.message);
  }

  try {
    const vault = await requestJson("/vault-health");
    el("healthStatus").textContent = vault.status || "-";
    el("healthStatus").className = (vault.status || "").toLowerCase();
  } catch (error) {
    el("healthStatus").textContent = "인증";
  }

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

  await refreshJobStoreHealth();
  await refreshJobs();
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
  } catch (error) {
    printResult("오류", error.payload || error.message);
  }
}

function startPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
  }
  state.pollTimer = setInterval(async () => {
    if (!state.currentJobId) return;
    await loadJob(state.currentJobId);
    await refreshJobs();
  }, 2000);
}

async function submitRun(event) {
  event.preventDefault();
  const payload = {
    topic: el("topicInput").value.trim(),
    provider: el("providerInput").value,
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
    await refreshJobs();
    await refreshJobStoreHealth();
    startPolling();
  } catch (error) {
    printResult("오류", error.payload || error.message);
  } finally {
    el("runButton").disabled = false;
  }
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
  el("runForm").addEventListener("submit", submitRun);
  el("refreshButton").addEventListener("click", refreshStatus);
  refreshStatus();
}

document.addEventListener("DOMContentLoaded", init);
"""
