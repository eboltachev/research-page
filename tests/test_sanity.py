from pathlib import Path


def test_lock_file_exists() -> None:
    assert Path("uv.lock").exists()


def test_review03_exists() -> None:
    assert Path("review03.md").exists()
