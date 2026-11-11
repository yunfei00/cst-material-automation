from pathlib import Path

from cst_material.discovery import inspect


def test_missing_directory_is_blocked(tmp_path: Path):
    assert inspect(tmp_path / "missing")["status"] == "BLOCKED"


def test_find_names_without_leaking_paths(tmp_path: Path):
    (tmp_path / "Python").mkdir()
    (tmp_path / "python_cst_libraries").mkdir()
    (tmp_path / "adapter.whl").write_bytes(b"")
    result = inspect(tmp_path)
    assert result["status"] == "PASS"
    assert "python" in result["features"]
    assert "python_cst_libraries" in result["features"]
    assert "extension:.whl" in result["features"]
    assert str(tmp_path) not in str(result)


def test_symlinks_are_not_followed(tmp_path: Path):
    (tmp_path / "MacRoS").mkdir()
    result = inspect(tmp_path)
    assert result["features"] == ["macros"]
