# P8-B3 两级屏蔽罩精筛（实验版）

第一级沿用 P5 数据库快速初筛，第二级仅对候选 STEP 执行 BREP 检查及相对平面配对厚度线索分析。不会重新导出 STEP，不会修改 CST，不会自动赋材。

Windows PowerShell（仓库根目录）：

```powershell
git switch feat/geometry-library
git pull --ff-only origin feat/geometry-library
$dir = 'D:\CST\Original.solid_export_diagnostic'
uv run --extra viewer python -m cst_material.shield_refinement "$dir" --limit 20
uv run --extra viewer python -m cst_material.shield_refinement "$dir"
```

结果写入导出目录 `shield_refinement.json`，每个候选完成后原子更新，支持按 STEP SHA256 复用。`REVIEW_STRONG` 是几何证据较强，`REVIEW` 是待确认，`LOW_EVIDENCE` 是证据不足，`FAIL` 是分析失败。均不是已确认屏蔽罩。

**当前限制**：厚度线索只覆盖相对平面，不能保证真实面重叠，也不能证明弯曲壁厚一致；轴向空腔采样不能证明侧壁连续围合。对于连接块误报率是否降低，需要真实工程验收。本版尚未把批量精筛集成进 GUI；可先通过 CLI 使用结果 JSON。不要把本版视作最终制造工艺识别器。
