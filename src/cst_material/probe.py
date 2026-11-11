"""Offline, read-only CST interface discovery.

This module never starts CST, opens a project, or invokes an automation method.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProbeResult:
    status: str
    python_version: str
    platform: str
    modules: dict[str, bool]
    paths: dict[str, bool]
    next_step: str


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def probe() -> ProbeResult:
    names = ("cst", "cst.interface", "win32com", "win32com.client")
    modules = {name: module_available(name) for name in names}
    hints = {
        "CST_STUDIO_HOME": os.environ.get("CST_STUDIO_HOME"),
        "CST_INSTALL_DIR": os.environ.get("CST_INSTALL_DIR"),
    }
    paths = {name: bool(value and Path(value).is_dir()) for name, value in hints.items()}
    return ProbeResult(
        status="NOT_RUN",
        python_version=sys.version.split()[0],
        platform=platform.system(),
        modules=modules,
        paths=paths,
        next_step="只检测模块存在性；CST 实际启动、读取和修改能力仍需单独验证",
    )


def to_json(result: ProbeResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2)
