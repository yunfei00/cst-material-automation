# P8-B3 两级屏蔽罩精筛（实验版）

第一级沿用 P5 数据库快速初筛，第二级仅对候选 STEP 执行 BREP 检查及相对平面配对厚度线索分析。不会重新导出 STEP，不会修改 CST，不会自动赋材。

Windows PowerShell（仓库根目录）：

```powershell
git switch main
git pull --ff-only origin main
$dir = 'D:\CST\Original.solid_export_diagnostic'
uv run --extra viewer python -m cst_material.shield_refinement "$dir" --limit 20
uv run --extra viewer python -m cst_material.shield_refinement "$dir"
```

结果写入导出目录 `shield_refinement.json`，每个候选完成后原子更新，支持按 STEP SHA256 复用。`REVIEW_STRONG` 是几何证据较强，`REVIEW` 是待确认，`LOW_EVIDENCE` 是证据不足，`FAIL` 是分析失败。均不是已确认屏蔽罩。

**图形界面**：使用 `uv run --extra viewer python view_solids.py "$dir"` 打开原有三维查看器，点击“二级精筛屏蔽罩候选”，会先执行一级筛选，再启动独立进程进行精筛；左侧列表显示中文判定，点击 Solid 可在右侧查看厚度线索与 BREP 证据。原始 JSON 仍保存用于追溯。大型项目请等待进度提示，首次运行可能较慢。

**当前限制**：厚度线索只覆盖相对平面，不能保证真实面重叠，也不能证明弯曲壁厚一致；轴向空腔采样不能证明侧壁连续围合。对于连接块误报率是否降低，需要真实工程验收。本版已集成 GUI，但没有对真实客户 STEP 做过识别准确率验收。不要把本版视作最终制造工艺识别器。


## P8-B4 几何复核与验收边界

- STEP 仅解析一次；精筛缓存使用算法版本，算法升级后自动重新计算。
- 使用 STEP 面方向修正平面法向；对候选厚度间隙进行 BREP 内部点检测，区分穿过实体材料与穿过空腔的假厚度。
- 在三个轴向截面检查中心空腔和侧壁围合。无法判断的斜放零件保持待确认，不直接排除。
- 这些是局部几何线索，不是全局壁厚、曲面折弯或 EMI 功能证明；所有结果仍需工程师复核，禁止自动赋材。
- 无客户数据时可运行合成 STEP 回归：`uv run --extra cad pytest -q tests/test_shield_cad_shapes.py`；缺少 CAD 依赖时测试跳过；当前 Windows CI 使用 `--extra viewer` 安装 OCP，实际执行这些合成 STEP 测试，但仍不代表客户真实模型验收。
- 实际验收请记录典型屏蔽罩、连接块、薄板、折弯件的漏检与误检。P5 一级筛选仍可能漏掉低分真阳性，需要依据真实样本调整召回率。
