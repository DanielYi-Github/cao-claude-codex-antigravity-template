from __future__ import annotations

from pathlib import Path

import pytest

from lyria_auto.config_migration import choose_python


@pytest.fixture
def fake_path(tmp_path) -> Path:
    return tmp_path


def test_setup_prefers_supported_python_in_descending_order(fake_path):
    (fake_path / "python3.13").mkdir()
    (fake_path / "python3.12").mkdir()
    assert choose_python(fake_path) == "python3.13"


def test_setup_falls_back_to_3_12_when_3_13_missing(fake_path):
    (fake_path / "python3.12").mkdir()
    assert choose_python(fake_path) == "python3.12"


def test_setup_rejects_3_11(fake_path):
    # 3.11 dropped 2026-08-30 -- StateDB's migration atomicity fix needs
    # sqlite3.Connection.autocommit (3.12+). A 3.11-only interpreter must
    # not be silently accepted.
    (fake_path / "python3.11").mkdir()
    with pytest.raises(RuntimeError, match="python3.12"):
        choose_python(fake_path)


def test_setup_raises_when_no_supported_python(fake_path):
    with pytest.raises(RuntimeError, match="python3.12"):
        choose_python(fake_path)
