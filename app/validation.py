from __future__ import annotations

import re

PATH_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._~/-]+$")


def normalize_and_validate_path(value: str) -> str:
    normalized = value.strip("/")
    if not PATH_PATTERN.fullmatch(normalized):
        raise ValueError("path must match <USER>/<PATH>")
    return normalized
