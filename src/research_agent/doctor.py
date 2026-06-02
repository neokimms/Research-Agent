from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .common_modules import configure_common_modules
from .config import Settings
from .gemini_client import GeminiError, GeminiGenerateClient, gemini_output_text
from .obsidian import ObsidianWriter
from .openai_client import OpenAIError, OpenAIResponsesClient, output_text
from .secrets import resolve_gemini_api_key, resolve_openai_api_key, select_llm_provider


OK = "OK"
WARN = "WARN"
FAIL = "FAIL"


@dataclass(frozen=True)
class DoctorCheck:
    status: str
    name: str
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    checks: list[DoctorCheck]

    @property
    def has_failures(self) -> bool:
        return any(check.status == FAIL for check in self.checks)

    def to_text(self) -> str:
        lines = ["Research Agent Doctor", ""]
        for check in self.checks:
            lines.append(f"[{check.status}] {check.name}: {check.detail}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({"checks": [asdict(check) for check in self.checks]}, ensure_ascii=False, indent=2)


def run_doctor(
    settings: Settings,
    *,
    config_path: Path,
    env_file: Path,
    write_test: bool = True,
    openai_smoke: bool = False,
    gemini_smoke: bool = False,
) -> DoctorReport:
    checks: list[DoctorCheck] = [
        DoctorCheck(OK, "config loaded", str(config_path)),
    ]

    checks.extend(_common_module_checks(settings))
    checks.extend(_vault_checks(settings, write_test=write_test))
    checks.append(_openai_key_check(settings, env_file=env_file))
    checks.append(_gemini_key_check(settings, env_file=env_file))
    checks.append(_provider_selection_check(settings))
    checks.append(_openai_smoke_check(settings, enabled=openai_smoke))
    checks.append(_gemini_smoke_check(settings, enabled=gemini_smoke))

    if _status_for(checks, "vault writable") == OK:
        checks.append(DoctorCheck(OK, "offline smoke ready", "offline run can write Obsidian artifacts"))
    else:
        checks.append(DoctorCheck(WARN, "offline smoke ready", "skipped because vault write check is not OK"))

    return DoctorReport(checks)


def _common_module_checks(settings: Settings) -> list[DoctorCheck]:
    if not settings.common.enabled:
        return [
            DoctorCheck(OK, "common modules", "disabled by configuration"),
            DoctorCheck(WARN, "llm_key_manager", "not checked because common modules are disabled"),
            DoctorCheck(WARN, "obsidian_connector", "not checked because common modules are disabled"),
        ]

    status = configure_common_modules(settings.common.module_path, enabled=True)
    module_path = settings.common.module_path
    checks: list[DoctorCheck] = []
    if module_path and module_path.exists():
        checks.append(DoctorCheck(OK, "common module path", str(module_path)))
    else:
        detail = str(module_path) if module_path else "not configured"
        checks.append(DoctorCheck(WARN, "common module path", f"{detail}; fallback implementations will be used"))

    checks.append(
        DoctorCheck(
            OK if status.llm_key_manager else WARN,
            "llm_key_manager",
            "available" if status.llm_key_manager else "not available; fallback .env parsing will be used",
        )
    )
    checks.append(
        DoctorCheck(
            OK if status.obsidian_connector else WARN,
            "obsidian_connector",
            "available" if status.obsidian_connector else "not available; fallback Obsidian writer will be used",
        )
    )
    return checks


def _vault_checks(settings: Settings, *, write_test: bool) -> list[DoctorCheck]:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    checks: list[DoctorCheck] = []

    if _looks_like_placeholder_path(settings.obsidian.vault_path):
        return [
            DoctorCheck(FAIL, "vault path configured", f"{settings.obsidian.vault_path} looks like a placeholder"),
            DoctorCheck(WARN, "vault exists", "skipped because vault path is a placeholder"),
            DoctorCheck(WARN, "vault writable", "skipped because vault path is a placeholder"),
            DoctorCheck(WARN, "reviewed note overwrite protection", "skipped because vault path is a placeholder"),
        ]

    checks.append(DoctorCheck(OK, "vault path configured", str(vault)))

    if not vault.exists():
        parent = vault.parent
        if parent.exists() and parent.is_dir():
            checks.append(DoctorCheck(WARN, "vault exists", "vault does not exist yet; run init-vault"))
        else:
            checks.append(DoctorCheck(FAIL, "vault exists", f"parent directory does not exist: {parent}"))
        checks.append(DoctorCheck(WARN, "vault writable", "skipped because vault does not exist"))
        checks.append(DoctorCheck(WARN, "reviewed note overwrite protection", "skipped because vault does not exist"))
        return checks

    if not vault.is_dir():
        checks.append(DoctorCheck(FAIL, "vault exists", "configured vault path is not a directory"))
        checks.append(DoctorCheck(WARN, "vault writable", "skipped because vault path is not a directory"))
        checks.append(DoctorCheck(WARN, "reviewed note overwrite protection", "skipped because vault path is not a directory"))
        return checks

    checks.append(DoctorCheck(OK, "vault exists", str(vault)))

    if not write_test:
        checks.append(DoctorCheck(WARN, "vault writable", "write test disabled"))
        checks.append(DoctorCheck(WARN, "reviewed note overwrite protection", "write test disabled"))
        return checks

    checks.append(_vault_writable_check(vault))
    if _status_for(checks, "vault writable") == OK:
        checks.append(_reviewed_protection_check(settings))
    else:
        checks.append(DoctorCheck(WARN, "reviewed note overwrite protection", "skipped because vault write check failed"))
    return checks


def _vault_writable_check(vault: Path) -> DoctorCheck:
    probe = vault / ".research-agent-doctor-write-test.md"
    try:
        probe.write_text("doctor write test\n", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "doctor write test\n":
            return DoctorCheck(FAIL, "vault writable", "temporary file round trip failed")
        return DoctorCheck(OK, "vault writable", "temporary file round trip succeeded")
    except OSError as exc:
        return DoctorCheck(FAIL, "vault writable", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _reviewed_protection_check(settings: Settings) -> DoctorCheck:
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    folder = ".research-agent-doctor"
    doctor_dir = writer.safe_path(folder)
    try:
        doctor_dir.mkdir(parents=True, exist_ok=True)
        first = writer.write_note(
            folder,
            "protection.md",
            "---\nstatus: reviewed\n---\n# Reviewed\n",
            allow_overwrite=True,
        )
        second = writer.write_note(folder, "protection.md", "# Draft")
        if first == second:
            return DoctorCheck(FAIL, "reviewed note overwrite protection", "reviewed note was overwritten")
        if "# Reviewed" not in first.read_text(encoding="utf-8"):
            return DoctorCheck(FAIL, "reviewed note overwrite protection", "reviewed note content changed")
        return DoctorCheck(OK, "reviewed note overwrite protection", f"protected {first.name}; wrote {second.name}")
    except Exception as exc:
        return DoctorCheck(FAIL, "reviewed note overwrite protection", f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(doctor_dir, ignore_errors=True)


def _openai_key_check(settings: Settings, *, env_file: Path) -> DoctorCheck:
    api_key = resolve_openai_api_key(settings)
    if api_key:
        return DoctorCheck(OK, "openai api key", f"{settings.openai.api_key_env} is configured")
    source_hint = f"set {settings.openai.api_key_env} in environment or {env_file}"
    return DoctorCheck(WARN, "openai api key", f"not configured or placeholder; {source_hint}")


def _gemini_key_check(settings: Settings, *, env_file: Path) -> DoctorCheck:
    api_key = resolve_gemini_api_key(settings)
    if api_key:
        return DoctorCheck(
            OK,
            "gemini api key",
            f"{settings.gemini.google_api_key_env} or {settings.gemini.api_key_env} is configured",
        )
    source_hint = (
        f"set {settings.gemini.api_key_env} or {settings.gemini.google_api_key_env} "
        f"in environment or {env_file}"
    )
    return DoctorCheck(WARN, "gemini api key", f"not configured or placeholder; {source_hint}")


def _provider_selection_check(settings: Settings) -> DoctorCheck:
    selection = select_llm_provider(settings)
    if selection.available:
        return DoctorCheck(OK, "llm provider", f"{selection.provider} selected ({selection.reason})")
    return DoctorCheck(WARN, "llm provider", selection.reason)


def _openai_smoke_check(settings: Settings, *, enabled: bool) -> DoctorCheck:
    if not enabled:
        return DoctorCheck(WARN, "openai smoke", "skipped; pass --openai-smoke to run a paid API check")

    api_key = resolve_openai_api_key(settings)
    if not api_key:
        return DoctorCheck(FAIL, "openai smoke", "cannot run because OpenAI API key is not configured")

    model = settings.openai.models.cheap_triage or settings.openai.models.synthesis
    client = OpenAIResponsesClient(api_key=api_key, default_model=model, timeout_seconds=30)
    try:
        response = client.create(
            input_text="Reply with exactly: OK",
            instructions="Return the single word OK.",
            model=model,
        )
        text = output_text(response).strip()
        if not text:
            return DoctorCheck(FAIL, "openai smoke", f"model {model} returned no output text")
        return DoctorCheck(OK, "openai smoke", f"Responses API returned output with {model}")
    except OpenAIError as exc:
        return DoctorCheck(FAIL, "openai smoke", _compact_error(exc))
    except Exception as exc:
        return DoctorCheck(FAIL, "openai smoke", f"{type(exc).__name__}: {_compact_error(exc)}")


def _gemini_smoke_check(settings: Settings, *, enabled: bool) -> DoctorCheck:
    if not enabled:
        return DoctorCheck(WARN, "gemini smoke", "skipped; pass --gemini-smoke to run a paid API check")

    api_key = resolve_gemini_api_key(settings)
    if not api_key:
        return DoctorCheck(FAIL, "gemini smoke", "cannot run because Gemini API key is not configured")

    model = settings.gemini.models.cheap_triage or settings.gemini.models.synthesis
    client = GeminiGenerateClient(api_key=api_key, default_model=model, timeout_seconds=30)
    try:
        response = client.generate(
            input_text="Reply with exactly: OK",
            instructions="Return the single word OK.",
            model=model,
        )
        text = gemini_output_text(response).strip()
        if not text:
            return DoctorCheck(FAIL, "gemini smoke", f"model {model} returned no output text")
        return DoctorCheck(OK, "gemini smoke", f"Gemini API returned output with {model}")
    except GeminiError as exc:
        return DoctorCheck(FAIL, "gemini smoke", _compact_error(exc))
    except Exception as exc:
        return DoctorCheck(FAIL, "gemini smoke", f"{type(exc).__name__}: {_compact_error(exc)}")


def _looks_like_placeholder_path(path: Path) -> bool:
    text = str(path)
    return text.startswith("/absolute/path") or text == "/path/to/ObsidianVault"


def _status_for(checks: list[DoctorCheck], name: str) -> str | None:
    for check in checks:
        if check.name == name:
            return check.status
    return None


def _compact_error(exc: Exception, *, limit: int = 300) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")
