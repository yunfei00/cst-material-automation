"""PySide6 + PyVistaQt 3D Solid browser; no CST process in the GUI.

Only one selected Solid is sent to VTK at a time, avoiding thousands of actors.
The exact STEP B-rep stays separate from its triangle preview mesh.
Dependencies are imported at launch so batch Python remains headless.
"""
from __future__ import annotations

import argparse
import sqlite3
import tempfile
import time
from pathlib import Path

from cst_material.cad_rendering import (
    RENDER_MODES,
    query_point_mesh,
    render_selected_solid,
)
from cst_material.diagnostic_catalog import load_diagnostic_catalog
from cst_material.geometry_search import search_geometry
from cst_material.knowledge_store import store_confirmation
from cst_material.occt_geometry import (
    classify_point,
    export_preview_stl,
    geometry_metrics,
    read_step,
)
from cst_material.pcb_recognition import find_main_pcb
from cst_material.shield_brep import analyze_shield_step
from cst_material.shield_recognition import search_pcb_related_shields, search_shield_candidates
from cst_material.solid_index import ensure_preview_mesh, load_catalog, load_manifest
from cst_material.solid_name_mapping import load_verified_mapping
from cst_material.solid_scan import scan_grid
from cst_material.structure_samples import (
    delete_confirmed_samples,
    load_samples,
    save_confirmed_sample,
    save_confirmed_samples_batch,
    search_similar_samples,
)


def launch_viewer(path: str | Path) -> int:
    import pyvista as pv
    from PySide6.QtCore import QProcess, Qt
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QSplitter,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    from pyvistaqt import QtInteractor

    target = Path(path).resolve(strict=True)
    if not (target.is_dir() or target.suffix.lower() in {".stp", ".step"}):
        raise ValueError("请输入 STEP 文件或者已生成的 .geometry 文件夹")
    app = QApplication.instance() or QApplication([])

    class SolidViewer(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("CST Solid 三维结构查看器（离线）")
            self.resize(1450, 850)
            self.root = target if target.is_dir() else None
            self.records = []
            self.record_lookup = {}
            self.search_results = None
            self.tree_items = {}
            self.original_tree_order = []
            self.similarity_threshold = 0.98
            self.active_record = None
            self.active_solids = None
            self.component_bodies = {}
            self.tempdir = tempfile.TemporaryDirectory(prefix="cst_viewer_")
            self.preview_actor = None
            self.active_mesh = None
            self.active_features = None
            self.picking_enabled = False
            splitter = QSplitter(Qt.Orientation.Horizontal)
            self.setCentralWidget(splitter)

            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.addWidget(QLabel("Solid 搜索 / 列表"))
            self.search = QLineEdit()
            self.search.setPlaceholderText("输入完整 Solid ID 或关键字过滤")
            left_layout.addWidget(self.search)
            search_actions = QHBoxLayout()
            self.find_similar = QPushButton("以当前 Solid 搜索相似")
            self.spring_only = QCheckBox("弹片")
            self.find_pcb = QPushButton("定位主 PCB（最大体积）")
            self.pcb_only = QCheckBox("主PCB")
            self.shield_only = QCheckBox("屏蔽罩")
            self.screw_only = QCheckBox("螺丝")
            self.find_shields = QPushButton("筛选屏蔽罩候选")
            self.inspect_shield = QPushButton("精细检查当前屏蔽罩")
            self.refine_shields = QPushButton("二级精筛屏蔽罩候选")
            self.scan_all_shields = QPushButton("独立识别屏蔽罩（全量几何）")
            self.refine_process = None
            self.group_process = None
            self.geometry_groups = None
            self.refinement_results = {}
            self.pcb_candidate_cache = None
            self.group_button = QPushButton("Solid 自动分组")
            self.clear_results = QPushButton("显示全部")
            for button in (self.find_similar, self.clear_results):
                search_actions.addWidget(button)
            left_layout.addLayout(search_actions)
            filters = QHBoxLayout()
            for checkbox in (self.pcb_only, self.shield_only, self.spring_only, self.screw_only):
                filters.addWidget(checkbox)
            left_layout.addLayout(filters)
            left_layout.addWidget(self.group_button)
            threshold_row = QHBoxLayout()
            threshold_row.addWidget(QLabel("最低相似度"))
            self.similarity_cutoff = QDoubleSpinBox()
            self.similarity_cutoff.setRange(0, 1)
            self.similarity_cutoff.setSingleStep(0.01)
            self.similarity_cutoff.setDecimals(3)
            self.similarity_cutoff.setValue(0.98)
            threshold_row.addWidget(self.similarity_cutoff)
            left_layout.addLayout(threshold_row)
            self.batch_save = QPushButton("将勾选结果批量保存为结构样本")
            left_layout.addWidget(self.batch_save)
            self.search_summary = QLabel("选中 Solid 后可直接搜索，点击结果即可查看三维")
            self.search_summary.setWordWrap(True)
            left_layout.addWidget(self.search_summary)
            self.tree = QTreeWidget()
            self.tree.setHeaderLabels(["Solid ID", "识别结果 / 相似度"])
            self.tree.setColumnWidth(0, 260)
            left_layout.addWidget(self.tree)
            splitter.addWidget(left)

            center = QWidget()
            center_layout = QVBoxLayout(center)
            self.plotter = QtInteractor(center)
            self.plotter.set_background("#17202f")
            center_layout.addWidget(self.plotter.interactor)
            splitter.addWidget(center)

            right = QWidget()
            right_layout = QVBoxLayout(right)
            right_layout.addWidget(QLabel("几何信息 / 点包含判断"))
            self.info = QLabel("选择一个 STEP 或 Solid")
            self.info.setWordWrap(True)
            self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            right_layout.addWidget(self.info)
            form = QFormLayout()
            self.x = QDoubleSpinBox()
            self.y = QDoubleSpinBox()
            self.z = QDoubleSpinBox()
            for widget, name in ((self.x, "X (mm)"), (self.y, "Y (mm)"), (self.z, "Z (mm)")):
                widget.setRange(-1e10, 1e10)
                widget.setDecimals(7)
                form.addRow(name, widget)
            right_layout.addLayout(form)
            self.check_button = QPushButton("判断点在 Solid 内 / 表面 / 外")
            right_layout.addWidget(self.check_button)
            self.answer = QLabel("尚未查询")
            self.answer.setWordWrap(True)
            right_layout.addWidget(self.answer)
            right_layout.addWidget(QLabel("包围盒扫描点数（包含两端点）"))
            self.scan_counts = []
            scan_form = QFormLayout()
            for axis in ("X", "Y", "Z"):
                spin = QSpinBox()
                spin.setRange(2, 2_000_000)
                spin.setValue(30)
                scan_form.addRow(f"{axis} 方向点数", spin)
                self.scan_counts.append(spin)
            right_layout.addLayout(scan_form)
            self.scan_button = QPushButton("扫描并导出 XYZ/value CSV")
            right_layout.addWidget(self.scan_button)
            self.scan_status = QLabel("value: -1 外部 / 0 表面 / 1 内部")
            self.scan_status.setWordWrap(True)
            right_layout.addWidget(self.scan_status)
            right_layout.addWidget(QLabel("三维显示模式"))
            self.render_mode = QComboBox()
            self.render_mode.addItems(RENDER_MODES)
            right_layout.addWidget(self.render_mode)
            self.depth_cue = QCheckBox("增强深度层次（EDL，可关闭）")
            right_layout.addWidget(self.depth_cue)
            self.render_info = QLabel("工程模式：曲面明暗 + 折弯边 + 轮廓")
            self.render_info.setWordWrap(True)
            right_layout.addWidget(self.render_info)
            self.reset_button = QPushButton("重置三维视角")
            right_layout.addWidget(self.reset_button)
            right_layout.addWidget(QLabel("结构相似样本（仅零件类别）"))
            self.similar_results = QLabel("暂无样本")
            self.similar_results.setWordWrap(True)
            right_layout.addWidget(self.similar_results)
            self.manage_samples = QPushButton("管理已确认结构样本")
            right_layout.addWidget(self.manage_samples)
            self.save_shape_sample = QPushButton("确认类别并保存结构样本")
            right_layout.addWidget(self.save_shape_sample)
            right_layout.addWidget(QLabel("人工确认零件身份 / 材料"))
            self.confirmed_type = QLineEdit()
            self.confirmed_type.setPlaceholderText("真实零件类别，例如 SPRING_CONTACT")
            right_layout.addWidget(self.confirmed_type)
            self.confirmed_material = QLineEdit()
            self.confirmed_material.setPlaceholderText("CST 中已有材料名（可留空）")
            right_layout.addWidget(self.confirmed_material)
            self.approve_material = QCheckBox("我已核实具体材料，允许后续自动赋材")
            right_layout.addWidget(self.approve_material)
            self.save_confirmation = QPushButton("保存本机确认结果")
            self.save_confirmation.setEnabled(bool(self.root))
            right_layout.addWidget(self.save_confirmation)
            right_layout.addStretch()
            splitter.addWidget(right)
            splitter.setSizes([260, 880, 310])

            self.search.textChanged.connect(self._filter_tree)
            self.find_similar.clicked.connect(self._find_similar_solids)
            self.spring_only.toggled.connect(self._toggle_spring_only)

            self.pcb_only.toggled.connect(self._toggle_pcb_only)
            self.shield_only.toggled.connect(self._toggle_shield_only)
            self.screw_only.toggled.connect(self._toggle_screw_only)



            self.group_button.clicked.connect(self._start_geometry_grouping)
            self.clear_results.clicked.connect(self._clear_search_results)
            self.batch_save.clicked.connect(self._save_checked_samples)
            self.tree.currentItemChanged.connect(self._select_solid)
            self.check_button.clicked.connect(self._query_point)
            self.scan_button.clicked.connect(self._scan_solid)
            self.reset_button.clicked.connect(self.plotter.reset_camera)
            self.render_mode.currentTextChanged.connect(self._render_mode_changed)
            self.depth_cue.toggled.connect(self._toggle_depth_cue)
            self.save_confirmation.clicked.connect(self._confirm_identity)
            self.save_shape_sample.clicked.connect(self._save_shape_sample)
            self.manage_samples.clicked.connect(self._manage_shape_samples)

            if self.root:
                if (self.root / "verified_solid_mapping.json").is_file():
                    verified = load_verified_mapping(self.root)
                    catalog = {"records": [
                        {"solid_id": sid, "step": item["step"],
                         "part_type": "UNKNOWN", "identity_status": "UNREVIEWED",
                         "candidate": "CST 隔离导出验证", "material": None,
                         "verified_solid_mapping": True}
                        for sid, item in verified.items()
                    ]}
                elif (self.root / "manifest.json").is_file():
                    manifest = load_manifest(self.root, verify_files=False, require_files=False)
                    catalog = {"records": [
                        {"solid_id": item["solid_id"], "step": item["step"],
                         "material": item.get("material"), "part_type": "UNKNOWN",
                         "identity_status": "UNREVIEWED",
                         "candidate": "CST 原始 Solid 独立导出",
                         "direct_solid_export": True}
                        for item in manifest["solids"]
                    ]}
                elif (self.root / "geometry_features.sqlite3").is_file():
                    with sqlite3.connect(self.root / "geometry_features.sqlite3") as db:
                        rows = db.execute(
                            "SELECT solid_id,step,material FROM solids WHERE status='PASS'"
                        ).fetchall()
                    catalog = {"records": [
                        {
                            "solid_id": solid_id, "step": step,
                            "material": material, "part_type": "UNKNOWN",
                            "identity_status": "UNREVIEWED",
                            "candidate": "独立几何特征记录",
                            "direct_solid_export": True,
                        }
                        for solid_id, step, material in rows
                    ]}
                elif (self.root / "report.json").is_file():
                    catalog = load_diagnostic_catalog(self.root)
                else:
                    catalog = load_catalog(self.root, verify_files=False)
                self.records = catalog["records"]
                self.record_lookup = {
                    rec.get("record_id", rec["solid_id"]): rec for rec in self.records
                }
                nodes = {}
                for rec in self.records:
                    if rec.get("diagnostic_export"):
                        component = rec["component"].replace("\\", "/")
                        parent = None
                        parts = component.split("/")
                        for depth in range(1, len(parts) + 1):
                            key = "/".join(parts[:depth])
                            if key not in nodes:
                                node = QTreeWidgetItem([parts[depth - 1], "组件"])
                                if parent is None:
                                    self.tree.addTopLevelItem(node)
                                else:
                                    parent.addChild(node)
                                nodes[key] = node
                            parent = nodes[key]
                        item = QTreeWidgetItem([
                            rec["record_id"],
                            f"组件 STEP · CST Solid {len(rec.get('solid_ids', []))} 个",
                        ])
                        parent.addChild(item)
                    else:
                        item = QTreeWidgetItem([
                            rec["solid_id"],
                            rec.get("part_type") if rec.get("part_type") != "UNKNOWN"
                            else (rec.get("candidate") or "UNKNOWN"),
                        ])
                        self.tree.addTopLevelItem(item)
                self.original_tree_order = [
                    self.tree.topLevelItem(i).text(0)
                    for i in range(self.tree.topLevelItemCount())
                ]
                self.tree_items = {
                    self.tree.topLevelItem(i).text(0): self.tree.topLevelItem(i)
                    for i in range(self.tree.topLevelItemCount())
                }
                self.tree.expandToDepth(1)
                self.statusBar().showMessage(
                    f"已加载 {len(self.records)} 条导出记录；仅按需读取 STEP 几何"
                )
                if self.records and not self.records[0].get("diagnostic_export"):
                    self.tree.setCurrentItem(self.tree.topLevelItem(0))
            else:
                self._display_step(target, {"solid_id": target.name, "step": str(target)})

        def _find_similar_solids(self):
            if self.active_record is None:
                self.search_summary.setText("请先在左侧选中一个 Solid")
                return
            self._apply_geometry_search(reference_id=self.active_record["solid_id"])

        def _inspect_shield(self):
            if not self.root or self.active_record is None:
                self.search_summary.setText("请先选择一个完整 Solid")
                return
            rec = self.active_record
            try:
                step = (self.root / rec["step"]).resolve()
                if not step.is_relative_to(self.root.resolve()):
                    raise ValueError("STEP 路径不能超出导出目录")
                if not step.is_file():
                    raise FileNotFoundError("精细几何检查需要该 Solid 的 STEP 文件")
                self.inspect_shield.setEnabled(False)
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                result = analyze_shield_step(step)
                evidence = "\\n".join(result["evidence"])
                self.similar_results.setText(
                    f"屏蔽罩 B-rep 检查：{result['score']}/100\\n"
                    f"几何候选：{'是' if result['candidate'] else '未达到阈值'}\\n"
                    f"{evidence}\\n"
                    "该结果不是功能身份确认，不会自动赋材料。"
                )
                self.search_summary.setText(
                    f"已检查 {rec['solid_id']} 的精确 STEP 几何；"
                    "结果见右侧结构相似样本区域。"
                )
            except Exception as exc:  # noqa: BLE001
                self.search_summary.setText(
                    f"屏蔽罩精细检查失败：{type(exc).__name__}: {exc}"
                )
            finally:
                QApplication.restoreOverrideCursor()
                self.inspect_shield.setEnabled(True)

        def _start_geometry_grouping(self):
            if not self.root or not (self.root / "geometry_features.sqlite3").is_file():
                self.search_summary.setText("请先完成 P5 几何特征提取")
                return
            if self.group_process is not None:
                return
            import json
            import sys
            import tempfile

            # Background process avoids freezing the GUI on large projects.
            output = Path(tempfile.gettempdir()) / f"cst_groups_{id(self)}.json"
            output.unlink(missing_ok=True)
            self.group_button.setEnabled(False)
            self.search_summary.setText("正在按几何特征自动分组，请稍候…")
            process = QProcess(self)
            self.group_process = process
            process.setWorkingDirectory(str(self.root))
            process.setProgram(sys.executable)
            process.setArguments([
                "-m", "cst_material.geometry_grouping", str(self.root),
                "--output", str(output),
            ])
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

            def finished(exit_code, _status):
                self.group_process = None
                self.group_button.setEnabled(True)
                if exit_code != 0 or not output.is_file():
                    details = bytes(process.readAllStandardOutput()).decode(
                        "utf-8", errors="replace"
                    )
                    self.search_summary.setText(f"自动分组失败：{details[-300:]}")
                    return
                try:
                    self.geometry_groups = json.loads(output.read_text(encoding="utf-8"))
                    self._show_geometry_groups()
                except (OSError, ValueError, KeyError) as exc:
                    self.search_summary.setText(f"分组结果读取失败：{exc}")
                finally:
                    output.unlink(missing_ok=True)

            process.finished.connect(finished)
            process.start()
            if not process.waitForStarted(3000):
                self.group_process = None
                self.group_button.setEnabled(True)
                self.search_summary.setText(
                    f"自动分组进程启动失败：{process.errorString()}"
                )

        def _show_geometry_groups(self):
            groups = self.geometry_groups
            if not groups:
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("Solid 几何自动分组（仅供人工复核）")
            dialog.resize(850, 650)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(
                f"共 {groups['total']} 个 Solid，分成 {groups['group_count']} 组。"
                "双击成员可定位三维；几何相似不代表材料相同。"
            ))
            tree = QTreeWidget()
            tree.setHeaderLabels(["分组 / Solid ID", "数量 / 与代表相似度"])
            tree.setColumnWidth(0, 570)
            layout.addWidget(tree)
            for group in groups["groups"]:
                parent = QTreeWidgetItem(tree, [
                    f"{group['group_id']}  ·  {group['representative']}",
                    f"{group['count']} 件",
                ])
                for member in group["members"]:
                    score = member["similarity"]
                    child = QTreeWidgetItem(parent, [
                        member["solid_id"],
                        "无法评分" if score is None else f"{score:.4f}",
                    ])
                    child.setData(0, Qt.ItemDataRole.UserRole, member["solid_id"])

            def navigate(item, _column):
                sid = item.data(0, Qt.ItemDataRole.UserRole)
                if not sid or sid not in self.tree_items:
                    return
                self._clear_search_results()
                self.tree.setCurrentItem(self.tree_items[sid])
                self.tree.scrollToItem(self.tree_items[sid])
                dialog.accept()

            tree.itemDoubleClicked.connect(navigate)
            self.search_summary.setText(
                f"自动分组完成：{groups['total']} 个 Solid，"
                f"{groups['group_count']} 组；请检查各组代表和成员。"
            )
            dialog.exec()

        def _exclusive_filter(self, active):
            for checkbox in (self.pcb_only, self.shield_only, self.spring_only, self.screw_only):
                if checkbox is active:
                    continue
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

        def _toggle_screw_only(self, checked):
            if checked:
                self._exclusive_filter(self.screw_only)
                self._scan_screw_geometry()
            else:
                if self.refine_process is not None:
                    self.refine_process.kill()
                    self.refine_process = None
                self._clear_search_results()

        def _scan_screw_geometry(self):
            if not self.root or self.refine_process is not None:
                return
            import json
            import sys
            process = QProcess(self)
            self.refine_process = process
            self.search_results = {}
            self._filter_tree("")
            self.search_summary.setText("正在识别螺丝；匹配的 Solid 会实时出现在左侧列表")
            process.setWorkingDirectory(str(self.root))
            process.setProgram(sys.executable)
            process.setArguments(["-m", "cst_material.phillips_screw_recognition", str(self.root)])
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            pending = bytearray()
            unpainted = 0
            progress_text = "正在启动"

            def refresh():
                nonlocal unpainted
                if self.refine_process is not process or not self.screw_only.isChecked():
                    return
                self._filter_tree(self.search.text())
                unpainted = 0

            def on_output():
                nonlocal unpainted, progress_text
                pending.extend(bytes(process.readAllStandardOutput()))
                while b"\n" in pending:
                    raw, _, remainder = pending.partition(b"\n")
                    pending[:] = remainder
                    try:
                        event = json.loads(raw.decode("utf-8", errors="replace"))
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict) or event.get("event") not in ("match", "progress"):
                        continue
                    if event["event"] == "match":
                        sid = event.get("solid_id")
                        if sid in self.tree_items and sid not in self.search_results:
                            self.search_results[sid] = {
                                "solid_id": sid, "verdict": "PHILLIPS_SCREW",
                                "candidate_type": "SCREW_CANDIDATE",
                            }
                            unpainted += 1
                    else:
                        progress_text = (
                            f'已扫描 {event.get("done", 0)}/{event.get("total", 0)}；'
                            f'匹配 {event.get("matched", 0)}；失败 {event.get("failed", 0)}'
                        )
                        refresh()
                    if unpainted >= 10:
                        refresh()
                if self.refine_process is process and self.screw_only.isChecked():
                    self.search_summary.setText(
                        f"螺丝识别中：{progress_text}；左侧已显示 {len(self.search_results)} 个"
                    )

            def finished(code, _status):
                if self.refine_process is not process:
                    return
                on_output()
                self.refine_process = None
                try:
                    if code != 0:
                        # Scanner may return nonzero for individual STEP failures;
                        # completed matches remain visible even in this case.
                        pass
                    report = json.loads((self.root / "phillips_screw_recognition.json").read_text(encoding="utf-8"))
                    if report.get("algorithm_version") != "phillips-geometry-v10":
                        raise ValueError("螺丝结果缓存版本不匹配")
                    # Only use records from the current scan, never stale cached
                    # entries left over from a previous interrupted run.
                    rows = [
                        {"solid_id": sid, "verdict": "PHILLIPS_SCREW",
                         "candidate_type": "SCREW_CANDIDATE"}
                        for sid, rec in report["results"].items()
                        if rec.get("status") == "PASS"
                        and rec.get("verdict") == "PHILLIPS_SCREW"
                        and sid in self.tree_items
                    ]
                    rows.sort(key=lambda row: row["solid_id"])
                    if self.screw_only.isChecked():
                        self.search_results = {row["solid_id"]: row for row in rows}
                        self._sort_tree_by_search_results(rows)
                        self._filter_tree(self.search.text())
                    self.search_summary.setText(
                        f'螺丝识别完成：匹配 {len(rows)} 个；'
                        f'失败 {report["summary"].get("failed", 0)} 个。'
                        '点击左侧 Solid 可查看三维。'
                    )
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    refresh()
                    self.search_summary.setText(
                        f"螺丝扫描未正常完成：{exc}；已匹配 {len(self.search_results)} 个仍可查看"
                    )

            process.readyReadStandardOutput.connect(on_output)
            process.finished.connect(finished)
            process.start()
            if not process.waitForStarted(3000):
                self.refine_process = None
                self.search_summary.setText("螺丝识别进程启动失败")

        def _toggle_shield_only(self, checked):
            if checked:
                self._exclusive_filter(self.shield_only)
                self._refine_shield_candidates(pcb_related=True)
            else:
                if self.refine_process is not None:
                    process = self.refine_process
                    self.refine_process = None
                    process.kill()
                self._clear_search_results()

        def _toggle_spring_only(self, checked):
            if checked:
                self._exclusive_filter(self.spring_only)
                self._find_spring_candidates()
            else:
                self._clear_search_results()

        def _toggle_pcb_only(self, checked):
            if checked:
                self._exclusive_filter(self.pcb_only)
                self._find_main_pcb()
            else:
                self._clear_search_results()

        def _apply_pcb_visibility(self, candidate_id=None):
            """Batch hide other solids without expensive reordering."""
            self.tree.setUpdatesEnabled(False)
            self.tree.blockSignals(True)
            try:
                for sid, item in self.tree_items.items():
                    item.setHidden(candidate_id is not None and sid != candidate_id)
            finally:
                self.tree.blockSignals(False)
                self.tree.setUpdatesEnabled(True)

        def _find_main_pcb(self):
            if not self.root:
                self.search_summary.setText("请先打开 P5 几何特征目录")
                return
            try:
                if self.pcb_candidate_cache is None:
                    self.pcb_candidate_cache = find_main_pcb(self.root)
                result = self.pcb_candidate_cache
                candidate = result["candidate"]
                if candidate is None:
                    self.search_results = {}
                    self._apply_pcb_visibility("__no_pcb_candidate__")
                    self.search_summary.setText(f"主 PCB 定位失败：{result['reason']}；域内 {result['domain_count']} 件")
                    return
                sid = candidate["solid_id"]
                # Checked: show only the candidate; unchecked: locate without filtering.
                self.search_results = ({sid: candidate} if self.pcb_only.isChecked()
                                       else None)
                self.search.blockSignals(True)
                self.search.clear()
                self.search.blockSignals(False)
                self._apply_pcb_visibility(sid if self.pcb_only.isChecked() else None)
                item = self.tree_items.get(sid)
                if item is not None:
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                self.search_summary.setText(
                    f'主 PCB 候选：{sid}；体积 {candidate["volume_mm3"]:.1f} mm³；'
                    f'Z=[{candidate["z_min_mm"]:.3f}, {candidate["z_max_mm"]:.3f}] mm；'
                    f'厚度 {candidate["z_thickness_mm"]:.3f} mm；'
                    f'薄片比例 {candidate["z_thin_ratio"]:.4f}；'
                    f'XY 面积 {candidate["xy_area_mm2"]:.1f} mm²；'
                    f'域内相对面积 {candidate["relative_xy_area"]:.1%}。'
                    f'判定：{result["reason"]}；'
                    '仅定位候选，需人工确认 PCB 身份。'
                )
            except Exception as exc:  # noqa: BLE001
                self.search_summary.setText(
                    f'PCB 定位失败：{type(exc).__name__}: {exc}'
                )

        def _find_pcb_related_shields(self):
            if not self.root:
                self.search_summary.setText("请先打开 P5 几何特征目录")
                return
            try:
                report = search_pcb_related_shields(self.root)
                rows = report["results"]
                self.refinement_results = {}
                self.search_results = {row["solid_id"]: row for row in rows}
                self.search.blockSignals(True)
                self.search.clear()
                self.search.blockSignals(False)
                self._sort_tree_by_search_results(rows)
                self._filter_tree("")
                counts = report["counts"]
                if not report["pcb"]["candidate"]:
                    self.search_summary.setText(report["warning"])
                else:
                    self.search_summary.setText(
                        f"PCB 关联筛选：XY 重叠 {counts['xy_overlap']} 件；"
                        f"Z 贴近 {counts['z_near']} 件；"
                        f"名称命中 {counts['named']} 件；"
                        f"粗几何支持 {counts['geometry_supported']} 件；"
                        f"最终候选 {len(rows)} 件（含疑似）。"
                        "低填充率不等于真实空腔，均需人工复核。"
                    )
            except Exception as exc:  # noqa: BLE001
                self.search_summary.setText(
                    f"PCB 关联屏蔽罩筛选失败：{type(exc).__name__}: {exc}"
                )

        def _find_shield_candidates(self):
            if not self.root:
                self.search_summary.setText("请先打开 P5 特征目录")
                return
            try:
                self.find_shields.setEnabled(False)
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                rows = search_shield_candidates(self.root)
                self.search_results = {row["solid_id"]: row for row in rows}
                for solid_id, item in self.tree_items.items():
                    if solid_id in self.search_results:
                        item.setCheckState(0, Qt.CheckState.Unchecked)
                self.search.setText("")
                self._filter_tree("")
                self.search_summary.setText(
                    f"屏蔽罩初筛候选 {len(rows)} 个；评分仅反映几何启发式证据，"
                    "并未验证顶板、侧壁或开口。必须人工核实，不自动赋材。"
                )
            except Exception as exc:  # noqa: BLE001
                self.search_summary.setText(
                    f"屏蔽罩筛选失败：{type(exc).__name__}: {exc}"
                )
            finally:
                QApplication.restoreOverrideCursor()
                self.find_shields.setEnabled(True)

        def _scan_all_shield_geometry(self):
            self._refine_shield_candidates(exhaustive=True)

        def _refine_shield_candidates(self, exhaustive=False, pcb_related=False):
            if not self.root or self.refine_process is not None:
                return
            if not exhaustive and not pcb_related:
                self._find_shield_candidates()
                if self.search_results is None:
                    return
            import sys
            process = QProcess(self)
            self.refine_process = process
            # Clear any previous similarity/spring/PCB results immediately.
            # Until analysis completes, never display unrelated recognition scores.
            self.search_results = {}
            self.refinement_results = {}
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._filter_tree("")
            self.search_summary.setText('屏蔽罩识别正在后台运行' if exhaustive
                                        else '二级精筛正在后台运行，请等待结果')
            process.setWorkingDirectory(str(self.root))
            process.setProgram(sys.executable)
            args = ['-m', 'cst_material.shield_refinement', str(self.root)]
            if exhaustive:
                args.append('--exhaustive')
            if pcb_related:
                args.append('--pcb-related')
            process.setArguments(args)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

            def finished(code, _status):
                import json
                if self.refine_process is not process:
                    return
                self.refine_process = None
                try:
                    filename = ('shield_refinement_pcb.json' if pcb_related else
                                'shield_refinement_all.json' if exhaustive else
                                'shield_refinement.json')
                    report = json.loads((self.root / filename).read_text(encoding='utf-8'))
                    self.refinement_results = report['results']
                    # REVIEW is not a negative verdict. Keep uncertain results visible
                    # and rank them by geometric evidence instead of hiding them.
                    def shield_rank(evidence):
                        thickness = evidence.get('thickness') or {}
                        paths = evidence.get('wall_paths') or {}
                        cross = evidence.get('cross_section') or {}
                        brep = (evidence.get('brep') or {}).get('metrics') or {}
                        score = 0.0
                        score += 25.0 * min(1.0, max(0.0, float(thickness.get('pair_consistency') or 0)))
                        score += 20.0 * min(1.0, max(0.0, float(paths.get('material_path_ratio') or 0)))
                        score += 15.0 * min(1.0, max(0.0, float(brep.get('transverse_face_count') or 0) / 4))
                        score += 20.0 if cross.get('wall_enclosure_hint') else 0.0
                        cavity = brep.get('axial_cavity_probe') or {}
                        score += 10.0 if cavity.get('axial_void') else 0.0
                        score += 10.0 if cavity.get('open_end_hint') else 0.0
                        return round(score, 1)

                    if pcb_related:
                        coarse = search_pcb_related_shields(self.root)
                        by_id = {r["solid_id"]: r for r in coarse["results"]}
                        visible_rows = []
                        stats = {}
                        for sid, evidence in self.refinement_results.items():
                            if sid not in self.tree_items or sid not in by_id:
                                continue
                            if evidence.get("status") != "PASS":
                                continue
                            detail = evidence.get("pcb_shield") or {}
                            verdict = evidence.get("verdict", detail.get("verdict", "CONTACT_OR_OTHER"))
                            stats[verdict] = stats.get(verdict, 0) + 1
                            if verdict == "CONTACT_OR_OTHER":
                                continue
                            visible_rows.append({
                                **by_id[sid], "verdict": verdict,
                                "score": float(evidence.get("score", detail.get("score", 0)) or 0),
                                "face_count": detail.get("face_count"),
                                "surface_counts": detail.get("surface_counts"),
                                "dominant_thickness_mm": detail.get("dominant_thickness_mm"),
                                "pair_consistency": detail.get("pair_consistency"),
                            })
                        visible_rows.sort(key=lambda r: (
                            {"SHIELD_REVIEW_STRONG": 0, "NAMED_REVIEW": 1,
                             "GEOMETRY_REVIEW": 2}.get(r["verdict"], 3),
                            -r["score"], r["gap_mm"], r["solid_id"]))
                        if self.shield_only.isChecked():
                            self.search_results = {r["solid_id"]: r for r in visible_rows}
                            self._sort_tree_by_search_results(visible_rows)
                            self._filter_tree("")
                        self.search_summary.setText(
                            f"屏蔽罩强候选 {stats.get('SHIELD_REVIEW_STRONG', 0)}；"
                            f"名称待确认 {stats.get('NAMED_REVIEW', 0)}；"
                            f"无名称疑似 {stats.get('GEOMETRY_REVIEW', 0)}；"
                            f"触点或其他 {stats.get('CONTACT_OR_OTHER', 0)}；"
                            f"STEP 失败 {report['summary'].get('failed', 0)}。"
                            "仍需人工确认。")
                        return
                    visible_rows = []
                    for sid, evidence in self.refinement_results.items():
                        if sid not in self.tree_items or evidence.get('status') != 'PASS':
                            continue
                        verdict = evidence.get('verdict')
                        if verdict not in ('REVIEW_STRONG', 'REVIEW'):
                            continue
                        visible_rows.append({
                            'solid_id': sid, 'candidate_type': 'SHIELD_CANDIDATE',
                            'verdict': verdict, 'score': shield_rank(evidence),
                        })
                    visible_rows.sort(
                        key=lambda row: (
                            row['verdict'] == 'REVIEW_STRONG', row['score']
                        ), reverse=True
                    )
                    if self.shield_only.isChecked():
                        self.search_results = {
                            row['solid_id']: row for row in visible_rows
                        }
                        self._sort_tree_by_search_results(visible_rows)
                        self._filter_tree("")
                    strong_count = sum(
                        row['verdict'] == 'REVIEW_STRONG' for row in visible_rows
                    )
                    review_count = sum(
                        row['verdict'] == 'REVIEW' for row in visible_rows
                    )
                    self.search_summary.setText(
                        f'屏蔽罩识别完成：严格匹配 {strong_count} 个；'
                        f'待确认 {review_count} 个；'
                        f'无法分析 {report["summary"].get("failed", 0)} 个。'
                        '已显示严格匹配和待确认结果，按几何证据排序；'
                        '分数是排序参考，不是识别概率，不自动赋材。'
                    )
                except (OSError, ValueError, KeyError) as exc:
                    self.search_results = {}
                    self._filter_tree('')
                    self.search_summary.setText(f'屏蔽罩识别失败：{exc}')

            def on_output():
                output = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace')
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                if lines:
                    self.search_summary.setText('屏蔽罩识别进度：' + lines[-1][:180])

            process.readyReadStandardOutput.connect(on_output)
            process.finished.connect(finished)
            process.start()
            if not process.waitForStarted(3000):
                self.refine_process = None
                self.search_summary.setText(f'无法启动屏蔽罩识别：{process.errorString()}')

        def _find_spring_candidates(self):
            self._apply_geometry_search(spring_candidates=True)

        def _apply_geometry_search(self, *, reference_id=None, spring_candidates=False):
            if not self.root:
                self.search_summary.setText("请先打开已完成 P5 的导出目录")
                return
            try:
                self.find_similar.setEnabled(False)
                self.spring_only.setEnabled(False)
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                rows = search_geometry(
                    self.root, reference_id=reference_id,
                    spring_candidates=spring_candidates, limit=10000,
                )
                if reference_id is not None:
                    cutoff = self.similarity_cutoff.value()
                    rows = [row for row in rows if row["similarity"] >= cutoff]
                self.search_results = {row["solid_id"]: row for row in rows}
                self._sort_tree_by_search_results(rows)
                for solid_id, item in self.tree_items.items():
                    item.setCheckState(
                        0, Qt.CheckState.Unchecked
                    ) if solid_id in self.search_results else None
                self.search.setText("")
                self._filter_tree("")
                self.search_summary.setText(
                    f"检索结果 {len(rows)} 个；点击左侧条目查看三维。"
                    "相似度仅表示几何接近，不代表零件身份确认。"
                )
                self.statusBar().showMessage(
                    f"已过滤显示 {len(rows)} 个候选；可点击 Solid 查看"
                )
            except Exception as exc:  # noqa: BLE001 - GUI displays search errors
                self.search_summary.setText(f"检索失败：{type(exc).__name__}: {exc}")
            finally:
                QApplication.restoreOverrideCursor()
                self.find_similar.setEnabled(True)
                self.spring_only.setEnabled(True)

        def _save_checked_samples(self):
            if not self.root or self.search_results is None:
                self.search_summary.setText("请先执行相似检索并勾选需要确认的 Solid")
                return
            selected = [
                solid_id for solid_id in self.search_results
                if solid_id in self.tree_items
                and self.tree_items[solid_id].checkState(0) == Qt.CheckState.Checked
            ]
            if not selected:
                self.search_summary.setText("请勾选确认属于同一结构类别的 Solid")
                return
            part_type = self.confirmed_type.text().strip()
            if not part_type:
                self.search_summary.setText("请先在右侧填写真实零件类别")
                return
            choice = QMessageBox.question(
                self, "批量确认结构样本",
                f"确认已逐一核实 {len(selected)} 个 Solid 属于 {part_type}？"
                "此操作仅保存结构样本，不会自动赋材料。",
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
            try:
                import json

                with sqlite3.connect(self.root / "geometry_features.sqlite3") as db:
                    placeholders = ",".join("?" for _ in selected)
                    records = db.execute(
                        "SELECT solid_id,step_sha256,features_json FROM solids "
                        f"WHERE status='PASS' AND solid_id IN ({placeholders})",
                        selected,
                    ).fetchall()
                if len(records) != len(selected):
                    raise ValueError("部分已选 Solid 缺少有效几何特征")
                entries = [
                    {
                        "sample_id": f"{digest[:16]}:{solid_id}",
                        "part_type": part_type,
                        "source_sha256": digest,
                        "metrics": json.loads(features),
                    }
                    for solid_id, digest, features in records
                ]
                result = save_confirmed_samples_batch(
                    self._sample_library_path(), entries
                )
                self.search_summary.setText(
                    f"已保存 {result['added']} 个确认样本；"
                    f"样本库共 {result['total']} 个。"
                )
                self._refresh_shape_matches()
            except Exception as exc:  # noqa: BLE001 - show actionable error
                self.search_summary.setText(
                    f"批量保存失败：{type(exc).__name__}: {exc}"
                )

        def _clear_search_results(self):
            for checkbox in (self.pcb_only, self.shield_only, self.spring_only, self.screw_only):
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
            self.search_results = None
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._apply_pcb_visibility()
            self.search_summary.setText("已恢复全部 Solid，可选择一个 Solid 再搜索相似")

        def _sort_tree_by_search_results(self, rows):
            """Display matching Solids in descending similarity order."""
            if not self.tree_items:
                return
            selected = self.tree.currentItem()
            selected_id = selected.text(0) if selected is not None else None
            self.tree.blockSignals(True)
            try:
                while self.tree.topLevelItemCount():
                    self.tree.takeTopLevelItem(0)
                ranked = [row["solid_id"] for row in rows if row["solid_id"] in self.tree_items]
                remaining = [
                    solid_id for solid_id in self.original_tree_order
                    if solid_id not in self.search_results
                ]
                for solid_id in [*ranked, *remaining]:
                    self.tree.addTopLevelItem(self.tree_items[solid_id])
                if selected_id in self.tree_items:
                    self.tree.setCurrentItem(self.tree_items[selected_id])
            finally:
                self.tree.blockSignals(False)

        def _restore_original_tree_order(self):
            if not self.tree_items:
                return
            selected = self.tree.currentItem()
            selected_id = selected.text(0) if selected is not None else None
            self.tree.blockSignals(True)
            try:
                while self.tree.topLevelItemCount():
                    self.tree.takeTopLevelItem(0)
                for solid_id in self.original_tree_order:
                    self.tree.addTopLevelItem(self.tree_items[solid_id])
                if selected_id in self.tree_items:
                    self.tree.setCurrentItem(self.tree_items[selected_id])
            finally:
                self.tree.blockSignals(False)

        def _filter_tree(self, keyword):
            value = keyword.casefold().strip()
            def filter_node(item):
                child_matches = [filter_node(item.child(i))
                                 for i in range(item.childCount())]
                own_match = (value in item.text(0).casefold()
                             or value in item.text(1).casefold())
                in_results = self.search_results is None or (
                    item.text(0) in self.search_results
                )
                visible = (own_match and in_results) or any(child_matches)
                if item.childCount() == 0 and item.text(0) in self.record_lookup:
                    rec = self.record_lookup[item.text(0)]
                    row = (self.search_results or {}).get(rec["solid_id"])
                    if row:
                        if self.pcb_only.isChecked() and row.get("z_min_mm") is not None:
                            item.setText(1, f'主 PCB 候选 · {row["volume_mm3"]:.1f} mm³')
                            item.setHidden(not visible)
                            return visible
                        refined = self.refinement_results.get(rec["solid_id"])
                        if refined and refined.get("status") == "PASS" and row.get("candidate_type") == "SHIELD_CANDIDATE":
                            label = ('严格匹配' if row.get('verdict') == 'REVIEW_STRONG'
                                     else '待确认')
                            item.setText(1, f"屏蔽罩{label} · 证据分 {row['score']:.1f}/100")
                            item.setHidden(not visible)
                            return visible
                        if row.get("verdict") in (
                            "NAMED_GEOMETRY", "NAMED_REVIEW", "GEOMETRY_REVIEW",
                            "SHIELD_REVIEW_STRONG"
                        ):
                            label = {
                                "NAMED_GEOMETRY": "名称+几何候选",
                                "SHIELD_REVIEW_STRONG": "等厚+围合强候选",
                                "NAMED_REVIEW": "名称命中·待确认",
                                "GEOMETRY_REVIEW": "无名称·几何疑似",
                            }[row["verdict"]]
                            item.setText(
                                1, f"屏蔽罩{label} · {row['side']} · "
                                   f"间隙 {row['gap_mm']:.3f} mm"
                            )
                            item.setHidden(not visible)
                            return visible
                        if row.get("candidate_type") == "SCREW_CANDIDATE":
                            item.setText(1, "螺丝 · 已匹配")
                            item.setHidden(not visible)
                            return visible
                        similarity = row.get("similarity")
                        item.setText(1, (
                            f"相似度 {similarity:.4f}" if similarity is not None
                            else (
                                f"屏蔽罩初筛 {row['score']}/100 · 待确认"
                                if row.get("candidate_type") == "SHIELD_CANDIDATE"
                                else "弹片形态候选 · 待确认"
                            )
                        ))
                    else:
                        item.setText(1, (
                            rec.get("part_type") if rec.get("part_type") != "UNKNOWN"
                            else (rec.get("candidate") or "UNKNOWN")
                        ))
                item.setHidden(not visible)
                if value and any(child_matches):
                    item.setExpanded(True)
                return visible

            for pos in range(self.tree.topLevelItemCount()):
                filter_node(self.tree.topLevelItem(pos))

        def _select_solid(self, item, _previous):
            if item is None or not self.root:
                return
            rec = self.record_lookup.get(item.text(0))
            if rec is None:
                return
            start = time.perf_counter()
            try:
                file_path = (self.root / rec["step"]).resolve()
                if not file_path.is_file():
                    self.active_record = rec
                    self.active_solids = None
                    self.active_features = None
                    self.active_mesh = None
                    self.plotter.clear()
                    self.info.setText(
                        f"Solid ID: {rec['solid_id']}\\n"
                        "STEP 文件不在当前输出目录；三维预览不可用。"
                        "已提取的 SQLite 特征仍可用于检索和批量确认。"
                    )
                    self.answer.setText(
                        "原始 STEP 缺失；可以搜索、勾选并保存结构样本，"
                        "但无法查看三维，请复制完整导出目录以恢复预览。"
                    )
                    return
                if rec.get("diagnostic_export") and "body_index" not in rec:
                    self.active_record = None
                    self.active_solids = None
                    self.active_features = None
                    self.active_mesh = None
                    self.plotter.clear()
                    bodies = read_step(file_path)
                    self.component_bodies[rec["record_id"]] = bodies
                    if item.childCount() == 0:
                        for index in range(len(bodies)):
                            body_rec = {
                                **rec,
                                "record_id": f"{rec['record_id']}:body:{index}",
                                "body_index": index,
                                "solid_id": f"未映射几何体 {index + 1}",
                                "candidate": "未建立 CST Solid 名称映射；禁止赋材料",
                            }
                            self.record_lookup[body_rec["record_id"]] = body_rec
                            item.addChild(QTreeWidgetItem([
                                body_rec["record_id"], body_rec["solid_id"],
                            ]))
                    item.setExpanded(True)
                    expected = len(rec.get("solid_ids", []))
                    mismatch = ("；数量与 CST Solid 不一致，请检查导出"
                                if expected != len(bodies) else "")
                    self.answer.setText(
                        f"组件 STEP 几何实体 {len(bodies)} 个，CST Solid {expected} 个"
                        f"{mismatch}。请展开选择单个几何体；尚未建立名称映射。"
                    )
                    return
                if "body_index" in rec:
                    bodies = self.component_bodies.get(
                        rec["record_id"].split(":body:")[0]
                    )
                    if bodies is None:
                        bodies = read_step(file_path)
                    self._display_step(
                        file_path, rec, solids=[bodies[rec["body_index"]]]
                    )
                else:
                    mesh_path = (None if (rec.get("verified_solid_mapping") or rec.get("direct_solid_export"))
                                 else ensure_preview_mesh(self.root, rec))
                    self._display_step(file_path, rec, mesh_path)
                refined = self.refinement_results.get(rec['solid_id'])
                if refined:
                    if refined.get('status') == 'PASS':
                        thickness = refined.get('thickness', {})
                        evidence = refined.get('brep', {}).get('evidence', [])
                        details = [
                            '二级精筛：' + refined.get('verdict', '未知'),
                            '主厚度线索：' + str(thickness.get('dominant_thickness_mm', '未知')) + ' mm',
                            '平面配对一致性：' + str(thickness.get('pair_consistency', '未知')),
                            '横截面围合线索：' + str(refined.get('cross_section', {}).get('enclosed_section_count', '未知')) + '/3',
                            '真实材料路径：' + str(refined.get('wall_paths', {}).get('material_path_count', '未知')) + '/' + str(refined.get('wall_paths', {}).get('checked_pair_count', '未知')),
                            *evidence,
                            '仅几何证据，不自动确认身份或赋材',
                        ]
                        self.similar_results.setText(chr(10).join(details))
                    else:
                        self.similar_results.setText('二级精筛失败：' + refined.get('error', '未知'))
                self.statusBar().showMessage(
                    f"显示 {rec['solid_id']}：{time.perf_counter() - start:.3f}s"
                )
            except Exception as exc:  # noqa: BLE001 - GUI must show unexpected geometry errors
                self.answer.setText(f"读取失败：{type(exc).__name__}: {exc}")

        def _display_step(self, file_path, rec, mesh_path=None, solids=None):
            start = time.perf_counter()
            if solids is None:
                solids = read_step(file_path)
            if mesh_path is None:
                mesh_path = Path(self.tempdir.name) / "single.stl"
                mesh_path.unlink(missing_ok=True)
                export_preview_stl(solids, mesh_path)
            self.active_mesh = pv.read(str(mesh_path))
            self._draw_active_mesh()
            self.active_solids = solids
            self.active_record = rec
            feats = geometry_metrics(solids)
            self.active_features = feats
            self._refresh_shape_matches()
            b = feats["bbox_mm"]
            for widget, center in zip(
                (self.x, self.y, self.z),
                ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2),
            ):
                widget.setValue(center)
            library_info = ""
            if self.root and (self.root / "geometry_features.sqlite3").is_file():
                try:
                    with sqlite3.connect(self.root / "geometry_features.sqlite3") as db:
                        row = db.execute(
                            "SELECT status,volume_mm3,face_count,body_count "
                            "FROM solids WHERE solid_id=?", (rec["solid_id"],)
                        ).fetchone()
                    if row:
                        library_info = (
                            f"几何特征库: {row[0]} | "
                            f"体积={row[1]} mm³ | 面数={row[2]} | 实体数={row[3]}\\n"
                        )
                except sqlite3.Error:
                    library_info = "几何特征库: 数据库读取失败\\n"
            self.info.setText(
                library_info +
                f"Solid ID: {rec['solid_id']}\n"
                f"识别身份: {rec.get('part_type', 'UNKNOWN')} "
                f"[{rec.get('identity_status', 'UNREVIEWED')}]\n"
                f"形态候选: {rec.get('candidate') or '无'}\n"
                f"识别依据: {'; '.join(rec.get('identity_evidence', []))}\n"
                f"当前材料: {rec.get('material') or '未知'}\n"
                f"体积: {feats['volume_mm3']:.6g} mm³\n"
                f"面积: {feats['surface_mm2']:.6g} mm²\n"
                f"面数: {feats['face_count']}；边数: {feats['edge_count']}\n"
                f"Xmin / Xmax: {b[0]:.9g} / {b[3]:.9g} mm\n"
                f"Ymin / Ymax: {b[1]:.9g} / {b[4]:.9g} mm\n"
                f"Zmin / Zmax: {b[2]:.9g} / {b[5]:.9g} mm\n"
                f"外形尺寸 XYZ: {feats['dimensions_mm']} mm\n"
                f"实体数量: {feats['solid_body_count']}；顶点数: {feats['vertex_count']}\n"
                f"曲面类型: {feats['surface_types']}\n"
                f"读取/显示: {time.perf_counter() - start:.3f} s"
            )
            self.confirmed_type.setText(
                rec.get("part_type") if rec.get("part_type") not in (None, "UNKNOWN")
                else (rec.get("candidate") or "")
            )
            self.confirmed_material.setText("")
            self.approve_material.setChecked(False)
            self.answer.setText("请输入 XYZ，判断是否在精确 B-rep 实体内部。")
            self.scan_status.setText("value: -1 外部 / 0 表面 / 1 内部")

        def _draw_active_mesh(self):
            if self.active_mesh is None:
                return
            if self.picking_enabled:
                self.plotter.disable_picking()
                self.picking_enabled = False
            self.preview_actor, edge_count, description = render_selected_solid(
                self.plotter, self.active_mesh, self.render_mode.currentText()
            )
            self.render_info.setText(description)
            self.plotter.view_isometric()
            self.plotter.reset_camera()
            self.plotter.renderer.ResetCameraClippingRange()
            self.plotter.enable_mesh_picking(
                callback=self._on_picked, show=False, left_clicking=True
            )
            self.picking_enabled = True
            self.statusBar().showMessage(
                f"渲染模式：{self.render_mode.currentText()}；折弯边 {edge_count}"
            )

        def _render_mode_changed(self, _mode):
            if self.active_mesh is not None:
                self._draw_active_mesh()

        def _toggle_depth_cue(self, checked):
            try:
                if checked:
                    self.plotter.enable_eye_dome_lighting()
                else:
                    self.plotter.disable_eye_dome_lighting()
                self.plotter.render()
                self.statusBar().showMessage(
                    "深度层次增强已开启" if checked else "深度层次增强已关闭"
                )
            except (RuntimeError, AttributeError, ValueError) as exc:
                self.depth_cue.blockSignals(True)
                self.depth_cue.setChecked(False)
                self.depth_cue.blockSignals(False)
                self.render_info.setText(
                    f"当前图形驱动不支持 EDL，已关闭：{type(exc).__name__}: {exc}"
                )

        def _on_picked(self, _picked):
            if self.preview_actor is None:
                return
            # Keep neutral CAD shading; recoloring the whole spring removes
            # the face-to-face contrast needed for thin folded components.
            self.plotter.render()
            self.answer.setText(
                f"三维选中 Solid: {self.active_record['solid_id']}"
            )

        def _query_point(self):
            if self.active_solids is None:
                self.answer.setText("请先选择 Solid")
                return
            point = (self.x.value(), self.y.value(), self.z.value())
            try:
                result = classify_point(self.active_solids, point)
            except Exception as exc:  # noqa: BLE001 - reports actual OCC failure
                self.answer.setText(f"精确几何查询失败：{type(exc).__name__}: {exc}")
                return
            labels = {"INSIDE": "实体内部", "ON_BOUNDARY": "实体表面（容差内）", "OUTSIDE": "实体外部"}
            result_text = f"精确实体判断：{labels.get(result, result)}\n点坐标(mm)：{point}"
            self.answer.setText(result_text)
            try:
                self.plotter.add_point_labels(
                    query_point_mesh(point), [labels.get(result, result)],
                    name="query_point_label", always_visible=True,
                    show_points=True, point_size=12, text_color="yellow",
                )
                self.plotter.add_mesh(
                    query_point_mesh(point),
                    style="points", color="#ff7043", point_size=16,
                    render_points_as_spheres=True, lighting=False,
                    pickable=False, name="query_point",
                )
            except Exception as exc:  # noqa: BLE001 - keep geometry answer on draw failure
                self.answer.setText(
                    f"{result_text}\n注意：判断已完成，但三维标记失败："
                    f"{type(exc).__name__}: {exc}"
                )

        def _scan_solid(self):
            if self.active_solids is None or self.active_features is None:
                self.scan_status.setText("请先加载 Solid")
                return
            filename, _ = QFileDialog.getSaveFileName(
                self, "保存扫描 CSV", "solid_scan.csv", "CSV (*.csv)"
            )
            if not filename:
                return
            self.scan_button.setEnabled(False)
            try:
                def progress(done, total):
                    self.scan_status.setText(f"扫描中 {done:,}/{total:,}")
                    QApplication.processEvents()
                result = scan_grid(
                    self.active_solids, self.active_features["bbox_mm"],
                    tuple(spin.value() for spin in self.scan_counts),
                    filename, progress=progress,
                )
                self.scan_status.setText(
                    f"完成 {result['total']:,} 点：外 {result['outside']:,}、"
                    f"表面 {result['boundary']:,}、内 {result['inside']:,}\n"
                    f"保存：{result['output']}"
                )
            except Exception as exc:  # noqa: BLE001 - GUI reports geometry and file errors
                self.scan_status.setText(f"扫描失败：{type(exc).__name__}: {exc}")
            finally:
                self.scan_button.setEnabled(True)

        def _sample_library_path(self):
            return Path.home() / ".cst_material_automation" / "structure_samples_v1.json"

        def _refresh_shape_matches(self):
            try:
                hits = search_similar_samples(
                    self.active_features, load_samples(self._sample_library_path())
                )
                self.similar_results.setText("\n".join(
                    f"{h['part_type']} / {h['sample_id']}: {h['similarity']:.3f}"
                    for h in hits
                ) if hits else "暂无已确认结构样本")
            except Exception as exc:  # noqa: BLE001 - display local library errors
                self.similar_results.setText(f"检索失败：{exc}")

        def _manage_shape_samples(self):
            dialog = QDialog(self)
            dialog.setWindowTitle("已确认结构样本管理")
            dialog.resize(650, 420)
            layout = QVBoxLayout(dialog)
            listing = QListWidget()
            listing.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
            samples = load_samples(self._sample_library_path())["samples"]
            for sample in samples:
                listing.addItem(f"{sample['part_type']} | {sample['sample_id']}")
            layout.addWidget(listing)
            layout.addWidget(QLabel("删除仅影响本机结构样本库，不修改 CST 工程或材料。"))
            delete_button = QPushButton("删除选中的样本")
            layout.addWidget(delete_button)

            def delete_selected():
                indices = sorted(
                    (listing.row(item) for item in listing.selectedItems()),
                    reverse=True,
                )
                if not indices:
                    return
                ids = [samples[i]["sample_id"] for i in indices]
                if QMessageBox.question(
                    dialog, "确认删除", f"确认删除 {len(ids)} 个已确认结构样本？",
                ) != QMessageBox.StandardButton.Yes:
                    return
                try:
                    delete_confirmed_samples(self._sample_library_path(), ids)
                    for i in indices:
                        listing.takeItem(i)
                        samples.pop(i)
                    self._refresh_shape_matches()
                except Exception as exc:  # noqa: BLE001 - show local errors
                    QMessageBox.warning(dialog, "删除失败", str(exc))

            delete_button.clicked.connect(delete_selected)
            dialog.exec()

        def _save_shape_sample(self):
            if self.active_record is None:
                self.similar_results.setText("请先选择一个 Solid")
                return
            if self.active_record.get("diagnostic_export"):
                self.similar_results.setText("未映射几何体不可保存为已确认 Solid 样本")
                return
            try:
                from cst_material.occt_geometry import sha256_file

                source = self.root / self.active_record["step"] if self.root else target
                if self.root and (self.root / "geometry_features.sqlite3").is_file():
                    with sqlite3.connect(self.root / "geometry_features.sqlite3") as db:
                        cached = db.execute(
                            "SELECT step_sha256,features_json FROM solids "
                            "WHERE solid_id=? AND status='PASS'",
                            (self.active_record["solid_id"],),
                        ).fetchone()
                else:
                    cached = None
                if cached is not None:
                    import json

                    digest, metrics_json = cached
                    metrics = json.loads(metrics_json)
                else:
                    if self.active_features is None:
                        raise FileNotFoundError(
                            "没有 STEP 也没有可用的 P5 特征记录，无法保存样本"
                        )
                    digest = sha256_file(source)
                    metrics = self.active_features
                sample_id = f"{digest[:16]}:{self.active_record['solid_id']}"
                save_confirmed_sample(
                    self._sample_library_path(), sample_id,
                    self.confirmed_type.text(), metrics,
                    source_sha256=digest,
                )
                self._refresh_shape_matches()
                self.statusBar().showMessage(f"已保存结构样本：{sample_id}")
            except Exception as exc:  # noqa: BLE001 - display local library errors
                self.similar_results.setText(f"保存失败：{exc}")

        def _confirm_identity(self):
            if not self.root or self.active_record is None:
                self.answer.setText("请先选择来自几何索引的 Solid")
                return
            if self.active_record.get("diagnostic_export"):
                self.answer.setText("组件 STEP 的几何体尚未映射到 CST Solid，禁止确认材料。")
                return
            try:
                target = store_confirmation(
                    self.root,
                    self.active_record["solid_id"],
                    self.confirmed_type.text(),
                    material=self.confirmed_material.text(),
                    approve_material=self.approve_material.isChecked(),
                )
                self.active_record["part_type"] = self.confirmed_type.text().strip()
                self.active_record["identity_status"] = "CONFIRMED"
                current = self.tree.currentItem()
                if current is not None:
                    current.setText(1, self.active_record["part_type"])
                self.answer.setText(
                    f"已保存在本机：{target}。"
                    "再次处理工程时将根据原 CST 指纹复用此确认信息。"
                )
            except Exception as exc:  # noqa: BLE001 - show explicit approval errors
                self.answer.setText(f"无法保存确认：{type(exc).__name__}: {exc}")

        def closeEvent(self, event):
            self.plotter.close()
            self.tempdir.cleanup()
            super().closeEvent(event)

    win = SolidViewer()
    win.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CST Solid 离线三维查看器")
    parser.add_argument("path", help="一个 STEP/STP 文件或一个 .geometry 目录")
    args = parser.parse_args(argv)
    return launch_viewer(args.path)


if __name__ == "__main__":
    raise SystemExit(main())
