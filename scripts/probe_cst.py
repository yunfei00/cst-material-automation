"""Non-invasive CST availability probe; never opens real engineering projects."""
import importlib.util
import json
import os


def main() -> None:
    modules = ["cst", "cst.interface", "win32com.client"]
    found = {}
    for module in modules:
        try:
            found[module] = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            found[module] = False
    print(json.dumps({
        "status": "NOT_RUN",
        "note": "Module detection only; real CST interface not yet verified",
        "modules": found,
        "cst_install_hint_present": bool(os.environ.get("CST_STUDIO_HOME")),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
