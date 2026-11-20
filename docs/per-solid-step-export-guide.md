# CST Solid 独立 STEP 全量导出与三维查看器操作手册

> 适用：Windows、CST Studio Suite 2025 SP1、Python 3.11、uv。本流程只操作原工程的临时副本，原始 .cst 不会作为写入目标。

## 1. 原理与当前版本

已在真实 CST 2025 SP1 中验证：STEP.Write 的参数直接使用 CST 原始 `Component:Solid` 名称，**不将冒号改成斜杠**。例如：

```vb
With STEP
    .Reset
    .FileName ("D:\Export\Solid_A.step")
    .ExportFromActiveCoordinateSystem (False)
    .Write ("Main/Leaf:Solid_A")
End With
```

每个 CST Solid 导出一个独立 STEP 文件；一个 CST Solid 内允许包含多个封闭 B-rep 几何体。旧的“每个叶子组件导出一个 STEP”流程已经废弃，**不要再使用旧版组件级报告作为 Solid 名称映射**。

## 2. 环境准备

在项目根目录打开 PowerShell，先修改以下命令中的工程路径。建议原工程目录有充足的可用空间；导出 7000～8000 个 Solid 可能产生大量文件，耗时取决于工程复杂度和硬盘速度。

```powershell
git switch main
git pull --ff-only origin main
uv sync --extra viewer

# 修改为自己的真实工程路径
$Project = "D:\CST\Original.cst"

# 检查工程文件
Test-Path $Project
```

## 3. 首次全量导出

```powershell
uv run --extra viewer python -m cst_material.all_solid_export "D:\CST\Original.cst"
```

默认导出**所有 Solid**，不是只导出 10 个。控制台持续输出进度和成功/失败数量。输出自动创建在原工程同目录：

```text
Original.solid_export_diagnostic/
├── steps/
│   ├── 000001_<hash>.step
│   ├── 000002_<hash>.step
│   └── ...
├── report.json
└── manifest.json  # 仅全部成功后生成
```

- `report.json`：每个 Solid 的原始名称、材料、STEP 文件、VBA 命令、导出结果、错误和进度。
- `manifest.json`：所有 Solid 都成功后生成的正式名称映射清单；查看器直接使用它。
- `steps/`：每个 CST Solid 对应一个独立 STEP 文件。
- `status=PASS`：全部成功；`PARTIAL`：部分失败；`FAIL`：运行中发生全局错误。
- `body_count`：该 STEP 内有效封闭几何实体数；允许大于 1。

**注意：** 原脚本采用同名输出目录 `*.solid_export_diagnostic`。如果其中存在旧版组件级导出数据，会拒绝混用。请先将旧目录**重命名或移走**，不要把旧 STEP 与新 STEP 混在一起；如果没有旧目录，无需处理。

## 4. 中断或部分失败：断点续导

直接重新运行**同一条全量导出命令**。程序读取同一目录中的 `report.json`，对先前成功的 STEP 校验 SHA256，跳过完整且未改变的文件；失败或缺失的文件重新导出。原工程文件哈希改变时，程序会拒绝继续复用旧数据，避免错误关联。

只有全部成功后才生成 `manifest.json`。若导出结果为 `PARTIAL`，先检查 `report.json` 中 `status=FAIL` 的条目及 `error`、`traceback`，排查后再次执行。

## 5. 只导出少量 Solid 进行快速验证（可选）

```powershell
uv run --extra viewer python -m cst_material.all_solid_export "D:\CST\Original.cst" --limit 10
```

**不要在准备全量导出的正式目录里先运行 `--limit 10`**：该命令会生成只包含前 10 个 Solid 的清单，随后全量运行会复用该目录。更推荐使用独立的 `export_ten_solids.py` 快速验证工具，并指定单独输出目录：

```powershell
uv run --extra viewer python export_ten_solids.py "D:\CST\Original.cst" "D:\CST\single_solid_test_10"
```

快速验证输出目录必须不存在。

## 6. 打开三维查看器

确认导出结果 `status=PASS` 且存在 `manifest.json` 后执行：

```powershell
uv run --extra viewer python view_solids.py "D:\CST\Original.solid_export_diagnostic"
```

查看器直接读取 `manifest.json`，显示真实 `Component:Solid` 名称。点击具体 Solid 时按需读取对应 STEP，显示三维几何、体积、包围盒，并可进行点包含判断与扫描。首次打开无需一次性将全部 STEP 渲染到显存。

## 7. 完成验收

1. `report.json` 的 `status` 为 `PASS`，`total == success`，`failed == 0`。
2. `manifest.json` 的 `solid_count` 与 `solids` 数组长度一致。
3. 同一个组件下的多个 Solid 在 `manifest.json` 中有不同的 `solid_id` 和 STEP 路径。
4. 查看器能按 CST 原始名称分别选中、显示这些 Solid。
5. 原始 `.cst` 文件未被修改。

## 8. 故障排查

- **旧数据拒绝复用**：将原来的 `*.solid_export_diagnostic` 目录重命名，重新执行全量导出。
- **STEP 导出失败**：查看 `report.json` 中失败条目的 `solid_id`、`vba` 和 `error`。
- **文件已导出但未通过**：可能是 STEP 不含有效封闭实体、文件不完整或几何解析失败；检查该条目 `traceback`。
- **中途退出**：原目录保留进度；重新执行相同命令继续。
- **无法打开查看器**：先检查是否存在 `manifest.json`；部分失败时不会生成正式清单。

> 限制：虽然 10 个 Solid 的 CST 独立导出已由用户实测成功，数千个 Solid 的长时间批量导出及查看器完整验收仍需在实际工程上进行。请保留 `report.json` 便于定位问题。
