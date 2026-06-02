from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommonModuleStatus:
    enabled: bool
    module_path: Path | None
    obsidian_connector: bool
    llm_key_manager: bool


def configure_common_modules(module_path: Path | None, *, enabled: bool = True) -> CommonModuleStatus:
    if not enabled:
        return CommonModuleStatus(False, module_path, False, False)

    if module_path and module_path.exists():
        module_path_text = str(module_path)
        if module_path_text not in sys.path:
            sys.path.insert(0, module_path_text)

    return CommonModuleStatus(
        enabled=True,
        module_path=module_path,
        obsidian_connector=importlib.util.find_spec("obsidian_connector") is not None,
        llm_key_manager=importlib.util.find_spec("llm_key_manager") is not None,
    )
