"""PySide6 + PyVistaQt 3D Solid browser; no CST process in the GUI.

Only one selected Solid is sent to VTK at a time, avoiding thousands of actors.
The exact STEP B-rep stays separate from its triangle preview mesh.
Dependencies are imported at launch so batch Python remains headless.
"""
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from cst_material.cad_rendering import (
    RENDER_MODES,
    query_point_mesh,
    render_selected_solid,
)
from cst_material.knowledge_store import store_confirmation
from cst_material.occt_geometry import (
    classify_point,
    export_preview_stl,
    geometry_metrics,
    read_step,
)
from cst_material.solid_index import ensure_preview_mesh, load_catalog
from cst_material.solid_scan import scan_grid
from cst_material.structure_samples import (
    load_samples,
    save_confirmed_sample,
    search_similar_samples,
)


def launch_viewer(path: str | Path) -> int:
    import pyvista as pv
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
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
            self.active_record = None
            self.active_solids = None
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
            self.tree = QTreeWidget()
            self.tree.setHeaderLabels(["Solid ID", "识别结果"])
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
            self.tree.currentItemChanged.connect(self._select_solid)
            self.check_button.clicked.connect(self._query_point)
            self.scan_button.clicked.connect(self._scan_solid)
            self.reset_button.clicked.connect(self.plotter.reset_camera)
            self.render_mode.currentTextChanged.connect(self._render_mode_changed)
            self.depth_cue.toggled.connect(self._toggle_depth_cue)
            self.save_confirmation.clicked.connect(self._confirm_identity)
            self.save_shape_sample.clicked.connect(self._save_shape_sample)

            if self.root:
                catalog = load_catalog(self.root, verify_files=False)
                self.records = catalog["records"]
                self.record_lookup = {rec["solid_id"]: rec for rec in self.records}
                for rec in self.records:
                    item = QTreeWidgetItem([
                        rec["solid_id"],
                        rec.get("part_type") if rec.get("part_type") != "UNKNOWN"
                        else (rec.get("candidate") or "UNKNOWN"),
                    ])
                    self.tree.addTopLevelItem(item)
                self.statusBar().showMessage(
                    f"已加载 {len(self.records)} 条 Solid 索引；仅按需读取三维网格"
                )
                if self.records:
                    self.tree.setCurrentItem(self.tree.topLevelItem(0))
            else:
                self._display_step(target, {"solid_id": target.name, "step": str(target)})

        def _filter_tree(self, keyword):
            value = keyword.casefold().strip()
            for pos in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(pos)
                item.setHidden(
                    value not in item.text(0).casefold()
                    and value not in item.text(1).casefold()
                )

        def _select_solid(self, item, _previous):
            if item is None or not self.root:
                return
            rec = self.record_lookup.get(item.text(0))
            if rec is None:
                return
            start = time.perf_counter()
            try:
                file_path = (self.root / rec["step"]).resolve()
                mesh_path = ensure_preview_mesh(self.root, rec)
                self._display_step(file_path, rec, mesh_path)
                self.statusBar().showMessage(
                    f"显示 {rec['solid_id']}：{time.perf_counter() - start:.3f}s"
                )
            except Exception as exc:  # noqa: BLE001 - GUI must show unexpected geometry errors
                self.answer.setText(f"读取失败：{type(exc).__name__}: {exc}")

        def _display_step(self, file_path, rec, mesh_path=None):
            start = time.perf_counter()
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
            self.info.setText(
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

        def _save_shape_sample(self):
            if self.active_features is None or self.active_record is None:
                return
            try:
                from cst_material.occt_geometry import sha256_file

                source = self.root / self.active_record["step"] if self.root else target
                digest = sha256_file(source)
                sample_id = f"{digest[:16]}:{self.active_record['solid_id']}"
                save_confirmed_sample(
                    self._sample_library_path(), sample_id,
                    self.confirmed_type.text(), self.active_features,
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
