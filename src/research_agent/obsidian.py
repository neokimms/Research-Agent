from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from .common_modules import configure_common_modules
from .config import ObsidianSettings
from .textutil import slugify


REVIEWED_STATUSES = {"reviewed", "evergreen"}
KNOWN_STATUSES = REVIEWED_STATUSES | {"draft", "active", "archived"}
logger = logging.getLogger(__name__)


class ObsidianWriter:
    def __init__(self, settings: ObsidianSettings, *, common_module_path: Path | None = None, use_common_module: bool = True):
        self.settings = settings
        self.vault_path = settings.vault_path.expanduser().resolve()
        self._common_module_path = common_module_path
        self._use_common_module = use_common_module
        self._connector = None

    @property
    def using_common_connector(self) -> bool:
        return self._connector is not None

    def ensure_structure(self) -> None:
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self._ensure_connector()
        dirs = [
            self.settings.draft_dir,
            f"{self.settings.source_dir}/official-docs",
            f"{self.settings.source_dir}/standards",
            f"{self.settings.source_dir}/papers",
            f"{self.settings.source_dir}/web",
            self.settings.taxonomy_dir,
            self.settings.blueprint_dir,
            self.settings.final_report_dir,
            self.settings.evidence_dir,
            self.settings.run_dir,
            "90_Templates",
            "99_Archive",
        ]
        for directory in dirs:
            if self._connector:
                self._connector.ensure_folder(directory)
            else:
                self.safe_path(directory).mkdir(parents=True, exist_ok=True)

    def write_note(self, directory: str, filename: str, markdown: str, *, allow_overwrite: bool = False) -> Path:
        self._ensure_connector()
        target_dir = self.safe_path(directory)
        if self._connector:
            self._connector.ensure_folder(directory)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = sanitize_filename(filename)
        if not safe_name.endswith(".md"):
            safe_name = f"{safe_name}.md"
        target = target_dir / safe_name

        if target.exists():
            status = read_frontmatter_status(target)
            protected = _status_requires_protection(target, status) and not self.settings.overwrite_reviewed_notes
            if protected or not allow_overwrite:
                target = self._available_variant(target)

        content = markdown.rstrip() + "\n"
        if self._connector:
            note_path = target.relative_to(self.vault_path).as_posix()
            self._connector.write_note(note_path, content, overwrite=True)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def safe_path(self, relative: str) -> Path:
        clean = relative.strip().strip("/")
        candidate = (self.vault_path / clean).resolve()
        if candidate != self.vault_path and self.vault_path not in candidate.parents:
            raise ValueError(f"Path escapes Obsidian vault: {relative}")
        return candidate

    def _available_variant(self, path: Path) -> Path:
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        for index in range(2, 1000):
            candidate = parent / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        while True:
            candidate = parent / f"{stem}-{uuid.uuid4().hex[:12]}{suffix}"
            if not candidate.exists():
                return candidate

    def _ensure_connector(self) -> None:
        if self._connector is not None or not self._use_common_module:
            return
        status = configure_common_modules(self._common_module_path)
        if not status.obsidian_connector:
            return
        try:
            from obsidian_connector import ObsidianConfig, ObsidianConnector

            self.vault_path.mkdir(parents=True, exist_ok=True)
            self._connector = ObsidianConnector(
                ObsidianConfig(vault_path=self.vault_path, default_folder=None)
            )
        except Exception as exc:
            logger.warning(
                "common Obsidian connector unavailable; using filesystem writer",
                extra={"stage": "obsidian_connector", "vault_path": str(self.vault_path), "error": str(exc)},
            )
            self._connector = None


def sanitize_filename(filename: str) -> str:
    stem = filename[:-3] if filename.endswith(".md") else filename
    stem = stem.strip()
    stem = re.sub(r"[/:\\]+", "-", stem)
    stem = re.sub(r"\s+", "-", stem)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    stem = stem.strip(".-_")
    return (stem or slugify(filename)) + ".md"


def read_frontmatter_status(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    frontmatter = text[4:end]
    for line in frontmatter.splitlines():
        match = re.match(r"^\s*status\s*:\s*(.*)\s*$", line)
        if match:
            status = match.group(1).strip().strip('"').strip("'")
            return status.lower() if status else None
    return None


def _status_requires_protection(path: Path, status: str | None) -> bool:
    if not status:
        return False
    if status in REVIEWED_STATUSES:
        return True
    if status not in KNOWN_STATUSES:
        logger.warning(
            "unknown frontmatter status; protecting existing note from overwrite",
            extra={"stage": "obsidian_frontmatter", "file_path": str(path), "status": status},
        )
        return True
    return False
