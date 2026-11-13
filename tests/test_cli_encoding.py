"""CLI encoding error diagnostics. No CST installation needed."""
import json

from cst_material import cli


class ModelWithInvalidEncoding:
    def get_tree_items(self):
        raise AssertionError("P1 must never call get_tree_items")

    def add_to_history(self, title, commands):
        raise UnicodeDecodeError(
            "utf-8", b"before\xb2after", 6, 7, "invalid start byte"
        )


class FakeProject:
    model3d = ModelWithInvalidEncoding()

    def close(self):
        pass


class FakeEnvironment:
    def open_project(self, path):
        return FakeProject()

    def close(self):
        pass


def test_p1_unicode_error_report_contains_stage_and_byte(tmp_path, monkeypatch):
    src = tmp_path / "existing.cst"
    src.write_bytes(b"dummy")
    report = tmp_path / "P1_Report.json"
    monkeypatch.setattr(cli, "connect", FakeEnvironment)

    rc = cli.main(["inventory", "--source", str(src), "--report", str(report)])
    data = json.loads(report.read_text(encoding="utf-8"))
    assert rc == 1
    assert data["status"] == "FAIL"
    assert data["stage"] == "CST VBA 枚举 Solid 和 Material"
    assert data["encoding_error"]["invalid_bytes_hex"] == "b2"
    assert "add_to_history" in data["traceback"]


def test_p2_mapping_non_utf8_is_reported_without_silent_replacement(tmp_path):
    mapping = tmp_path / "mapping.json"
    mapping.write_bytes('{"some:solid": "材料"}'.encode("gbk"))
    report = tmp_path / "P2_Report.json"

    rc = cli.main([
        "assign",
        "--source", str(tmp_path / "source.cst"),
        "--dest", str(tmp_path / "out.cst"),
        "--mapping", str(mapping),
        "--report", str(report),
    ])
    data = json.loads(report.read_text(encoding="utf-8"))
    assert rc == 1
    assert "UTF-8" in data["error"]
    assert data["stage"] == "读取 Solid→Material 映射"
    assert not (tmp_path / "out.cst").exists()
