# CST Solid 真实名称映射：先做单叶子组件验证

## 为什么旧 STEP 不能直接重命名

旧导出使用 `STEP.Write("组件路径")`，一个 STEP 包含多个 Solid。报告中的 `solid_ids` 是 CST 真实名称，但 STEP 几何体顺序与 CST 名称没有经过验证。即使数量一致，也不能按序号配对。

## 新增隔离导出试验（只跑一个叶子组件）

对目标叶子组件中的每个 Solid，重新复制原始 CST 工程到临时目录；只在临时工程中调用 `Solid.Delete` 删除同组件其他 Solid，再 `STEP.Write` 导出该组件。读取 STEP 确认恰好一个封闭实体，同时再次枚举临时 CST 工程，确认目标 Solid 仍存在且同组件其他 Solid 已不存在。只有全部验证通过，才输出 `verified_solid_mapping.json`，其中使用 **CST 返回的完整原名**。任何失败都记录在 `pilot_report.json`，不生成完整映射。

此方案使用的 `Solid.Delete` 在 CST 2025 SP1 当前工程上尚未实机验收。**先选只有 2～3 个 Solid 的叶子组件进行试验，不要全量运行。** 原始 CST 工程只复制读取，绝不执行删除或保存；不要上传保密工程。

PowerShell（修改工程路径、完整组件路径和新输出目录）：

```powershell
git fetch origin
git switch feat/solid-structure-recognition-v1
git pull --ff-only origin feat/solid-structure-recognition-v1
uv sync --group dev --extra viewer
uv run --group dev python -m pytest tests/test_solid_name_mapping.py -q
uv run python pilot_solid_name_mapping.py "D:\CST\Original.cst" "main/sub/leaf" "D:\CST\solid_name_pilot"
uv run --extra viewer python view_solids.py "D:\CST\solid_name_pilot"
```

`main/sub/leaf` 必须替换成 `component_tree.json` 中的完整叶子组件路径。输出目录必须不存在。成功时查看器列出真实 CST Solid ID，并可分别加载对应单实体 STEP；失败时只检查 `pilot_report.json`，不要将旧的几何体序号强行套上真实名称。

此版本仅解决**名称与几何一对一的实验验证**，不表示 7000+ Solid 已完成映射。下一步在真实 CST 2025 SP1 验证通过后，再设计增量、缓存和批量隔离方案。
