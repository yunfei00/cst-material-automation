# 叶子组件导出与目录查看（P1 + P2）

本版依据 `Solid.GetNameOfShapeFromIndex` 返回的完整 `Component:Solid` 名称，提取组件路径并构建层级树。仅当组件路径中含明确的 `/` 或 `\\` 分隔符时才识别父子关系，不根据名称相似度猜测。此树是 **Solid ID 推导树**，尚不是 CST 独立组件树 API 的完整读取结果。

对每个包含 Solid 且没有子组件的叶子组件，**只执行一次** `STEP.Write("组件路径")`。一个叶子组件可能包含多个 Solid，生成的 STEP 仍是组件级文件，不能将其中任一几何体自动关联到单个 Solid。报告保存 `solid_ids` 供后续精确映射。

输出：`component_tree.json`（组件父子关系、叶子组件、Solid 清单）、`report.json`（每叶子组件一次导出结果）、`steps/`（组件 STEP）。导出只在临时复制的 CST 工程上运行。

## Windows PowerShell 验收

请先将**上次**的 `Original.solid_export_diagnostic` 文件夹重命名保留，避免覆盖；新导出程序不覆盖旧结果。以下只需修改 CST 工程路径：

```powershell
git fetch origin
git switch feat/solid-structure-recognition-v1
git pull --ff-only origin feat/solid-structure-recognition-v1
uv sync --group dev --extra viewer
uv run --group dev python -m pytest tests/test_all_solid_export.py tests/test_diagnostic_catalog.py -q
uv run python export_solids.py "D:\CST\Original.cst"
uv run --extra viewer python view_solids.py "D:\CST\Original.solid_export_diagnostic"
```

验收：
- `report.json` 中 `solid_total` 是原始 Solid 总数，`leaf_component_total` 是叶子组件数；`total` 与 `leaf_component_total` 相等。
- `component_tree.json` 中组件树与 CST 实际层级核对；如实际 CST 路径使用非 `/` 或 `\\` 的层级编码，**不要将推导结果视为已验证**。
- 同一叶子组件下多个 Solid 仅产生一个 STEP，`items[].solid_ids` 包含其所有 Solid ID。
- 左侧组件树可展开，点击叶子下导出记录按需加载组件 STEP。
- 未建立单 Solid 精确几何映射，**禁止自动赋材料**。
- 实机验收只反馈脱敏计数、树层级是否准确和异常文本，不上传保密工程。
