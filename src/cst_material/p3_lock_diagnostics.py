"""Read-only Windows lock-owner diagnostics for CST temporary-file cleanup.

Uses the built-in Windows Restart Manager *query* APIs only. Never invokes
RmShutdown/RmRestart, terminates processes, or changes open-file permissions.
Results are diagnostic snapshots: an empty owner list cannot prove that
no process has the file open (the lock may already have disappeared).
"""
from __future__ import annotations

import os
from pathlib import Path


def identify_file_owners(file: Path) -> dict:
    """Best-effort process/PID snapshot for a file that raised WinError 32."""
    if os.name != "nt":
        return {"status": "unsupported_platform", "processes": []}
    if not file.is_file():
        return {"status": "file_already_gone", "processes": []}

    import ctypes
    from ctypes import wintypes

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [
            ("dwProcessId", wintypes.DWORD),
            ("ProcessStartTime", wintypes.FILETIME),
        ]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", ctypes.c_wchar * 256),
            ("strServiceShortName", ctypes.c_wchar * 64),
            ("ApplicationType", ctypes.c_uint),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    try:
        api = ctypes.WinDLL("rstrtmgr.dll", use_last_error=True)
        start = api.RmStartSession
        start.argtypes = [
            ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_wchar_p
        ]
        start.restype = wintypes.DWORD
        register = api.RmRegisterResources
        register.argtypes = [
            wintypes.DWORD, wintypes.UINT, ctypes.POINTER(ctypes.c_wchar_p),
            wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
        ]
        register.restype = wintypes.DWORD
        get_list = api.RmGetList
        get_list.argtypes = [
            wintypes.DWORD, ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT), ctypes.POINTER(RM_PROCESS_INFO),
            ctypes.POINTER(wintypes.DWORD),
        ]
        get_list.restype = wintypes.DWORD
        end = api.RmEndSession
        end.argtypes = [wintypes.DWORD]
        end.restype = wintypes.DWORD

        handle = wintypes.DWORD()
        session_key = ctypes.create_unicode_buffer(33)
        code = start(ctypes.byref(handle), 0, session_key)
        if code:
            return {"status": "rm_start_error", "code": code, "processes": []}
        try:
            files = (ctypes.c_wchar_p * 1)(str(file.resolve()))
            code = register(handle, 1, files, 0, None, 0, None)
            if code:
                return {
                    "status": "rm_register_error",
                    "code": code,
                    "processes": [],
                }
            needed = wintypes.UINT(0)
            actual = wintypes.UINT(0)
            reason = wintypes.DWORD(0)
            code = get_list(
                handle, ctypes.byref(needed), ctypes.byref(actual),
                None, ctypes.byref(reason),
            )
            if code == 0:
                return {"status": "no_owners_found", "processes": []}
            if code != 234:
                return {"status": "rm_getlist_error", "code": code, "processes": []}
            if needed.value > 128:
                return {"status": "too_many_owners", "processes": []}
            buffer = (RM_PROCESS_INFO * max(needed.value, 1))()
            actual.value = max(needed.value, 1)
            code = get_list(
                handle, ctypes.byref(needed), ctypes.byref(actual),
                buffer, ctypes.byref(reason),
            )
            if code:
                return {"status": "rm_getlist_error", "code": code, "processes": []}
            processes = [
                {
                    "pid": int(item.Process.dwProcessId),
                    "application": str(item.strAppName),
                    "service": str(item.strServiceShortName),
                }
                for item in buffer[: actual.value]
            ]
            return {
                "status": "owners_found" if processes else "no_owners_found",
                "processes": processes,
            }
        finally:
            end(handle)
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        return {
            "status": "probe_error",
            "error": f"{type(exc).__name__}: {exc}",
            "processes": [],
        }
