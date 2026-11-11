import json

from cst_material.probe import ProbeResult, module_available, to_json


def test_missing_module_is_false():
    assert not module_available("definitely_not_a_real_cst_package_123456")


def test_json_preserves_chinese():
    result = ProbeResult(
        status="NOT_RUN",
        python_version="3.11",
        platform="Windows",
        modules={"cst": False},
        paths={},
        next_step="等待本机验证",
    )
    parsed = json.loads(to_json(result))
    assert parsed["next_step"] == "等待本机验证"
    assert parsed["status"] == "NOT_RUN"
