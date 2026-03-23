import random
import string

import pytest

from app.validation import normalize_and_validate_path

VALID_CHARS = string.ascii_letters + string.digits + "._-"
PATH_CHARS = VALID_CHARS + "~/"


def _rand(alphabet: str, min_len: int, max_len: int) -> str:
    size = random.randint(min_len, max_len)
    return "".join(random.choice(alphabet) for _ in range(size))


def test_valid_paths_property() -> None:
    for _ in range(200):
        user = _rand(VALID_CHARS, 1, 12)
        path = _rand(PATH_CHARS, 1, 24).strip("/") or "a"
        value = f"/{user}/{path}/"
        normalized = normalize_and_validate_path(value)
        assert normalized == f"{user}/{path}"


def test_invalid_paths_property() -> None:
    invalid_values = ["", "onlyuser", "a b/c", "a//", "/", "юзер/путь", "a/"]
    for value in invalid_values:
        with pytest.raises(ValueError):
            normalize_and_validate_path(value)
