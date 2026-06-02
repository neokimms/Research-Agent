from __future__ import annotations

import re


def slugify(text: str, *, fallback: str = "research") -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or fallback


def yaml_scalar(value: str | int | float | bool | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    escaped = str(value).replace('"', '\\"')
    return f'"{escaped}"'
