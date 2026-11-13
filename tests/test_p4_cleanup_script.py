"""R001 one-command PowerShell wrapper: syntax and safety contract."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_r001.ps1"


def test_r001_script_runs_plan_before_actual_copy_only_apply():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "python @planArguments" in source
    assert "python @applyArguments" in source
    assert source.index("python @planArguments") < source.index("python @applyArguments")
    assert "'cleanup-plan'" in source
    assert "'cleanup-apply'" in source
    assert "'--source'" in source
    assert "'--dest'" in source
    assert "'--plan'" in source
    assert "'--geometry-report'" in source
    assert "NewCstProject" in source
    assert "RemainingMaterialsUnchanged" in source
    assert re.search(r"(?i)\$input\b", source) is None
    assert "if ($LASTEXITCODE -ne 0)" in source
    assert "$report.status -ne 'PASS'" in source


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not installed")
def test_r001_wrapper_has_valid_powershell_syntax():
    # Parse without executing: CST cannot run in CI and no user project exists.
    path = str(SCRIPT).replace("'", "''")
    statement = (
        "$tokens=$null; $parseErrors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$tokens, [ref]$parseErrors) | Out-Null; "
        "if ($parseErrors.Count -gt 0) { "
        "$parseErrors | ForEach-Object { Write-Output $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", statement],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
