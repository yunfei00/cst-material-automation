"""P3-C WinError 32 root-cause telemetry regression tests (no company data)."""
from pathlib import Path

import pytest

from cst_material import p3_lock_diagnostics, p3_spatial


def test_cleanup_records_transient_lock_then_succeeds(tmp_path, monkeypatch):
    working = tmp_path / "cst_p3_spatial"
    locked = working / "result" / "dp.parmap"
    locked.parent.mkdir(parents=True)
    locked.write_bytes(b"placeholder")
    calls = []
    snapshots = []

    def remove(work):
        calls.append(work)
        if len(calls) < 3:
            raise PermissionError(13, "sharing violation", str(locked))
        locked.unlink()
        work.joinpath("result").rmdir()
        work.rmdir()

    def owner(file):
        snapshots.append(file)
        return {
            "status": "owners_found",
            "processes": [{"pid": 4321, "application": "CST Studio Suite"}],
        }

    monkeypatch.setattr(p3_spatial.shutil, "rmtree", remove)
    monkeypatch.setattr(p3_spatial, "identify_file_owners", owner)
    monkeypatch.setattr(p3_spatial.time, "sleep", lambda _seconds: None)
    details = {}
    assert p3_spatial._cleanup_workdir(working, details) is True
    assert len(calls) == 3
    assert snapshots == [locked]
    assert details["cleanup_ok"] is True
    assert details["lock_observed"] is True
    assert details["cleanup_attempts"] == 3
    assert details["first_cleanup_failure"]["file"] == "result/dp.parmap"
    assert details["first_owner_snapshot"]["processes"][0]["pid"] == 4321
    assert details["cleanup_elapsed_seconds"] >= 0
    assert not working.exists()


def test_cleanup_diagnostics_do_not_override_original_exception(tmp_path, monkeypatch):
    working = tmp_path / "cst_p3_spatial"
    working.mkdir()
    busy = working / "result" / "dp.parmap"

    def always_locked(_path):
        raise PermissionError(13, "file in use", str(busy))

    monkeypatch.setattr(p3_spatial.shutil, "rmtree", always_locked)
    monkeypatch.setattr(p3_spatial, "identify_file_owners", lambda _: {
        "status": "no_owners_found", "processes": [],
    })
    monkeypatch.setattr(p3_spatial.time, "sleep", lambda _seconds: None)
    details = {}
    with pytest.warns(RuntimeWarning, match="清理失败"):
        result = p3_spatial._cleanup_workdir(working, details, attempts=3)
    assert result is False
    assert details["cleanup_ok"] is False
    assert details["cleanup_attempts"] == 3
    assert details["last_owner_snapshot"]["status"] == "no_owners_found"
    assert details["first_cleanup_failure"]["file"] == "result/dp.parmap"


def test_probe_never_tries_to_stop_or_restart_processes():
    import inspect

    source = inspect.getsource(p3_lock_diagnostics.identify_file_owners)
    assert "RmShutdown" not in source
    assert "RmRestart" not in source
    assert "RmGetList" in source
    assert "RmRegisterResources" in source


@pytest.mark.skipif(p3_lock_diagnostics.os.name == "nt", reason="Non-Windows only")
def test_nonwindows_probe_is_conservative(tmp_path):
    info = p3_lock_diagnostics.identify_file_owners(tmp_path / "dp.parmap")
    assert info["status"] == "unsupported_platform"
    assert info["processes"] == []


@pytest.mark.skipif(p3_lock_diagnostics.os.name != "nt", reason="Windows Restart Manager only")
def test_windows_restart_manager_probe_can_query_temporary_file(tmp_path):
    locked = tmp_path / "test.parmap"
    locked.write_bytes(b"test-data")
    info = p3_lock_diagnostics.identify_file_owners(locked)
    assert info["status"] in {
        "owners_found", "no_owners_found", "rm_register_error",
        "rm_getlist_error", "rm_start_error",
    }
    assert isinstance(info["processes"], list)
