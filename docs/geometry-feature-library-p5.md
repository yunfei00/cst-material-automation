# P5 几何特征库操作手册

## 功能范围

本阶段直接使用已成功全量导出的 `manifest.json` 和逐 Solid STEP，不需要再次打开 CST 或重新导出。每个 `Component:Solid` 建立一条 SQLite 记录，保存原始名称、材料、STEP 路径与 SHA256、精确 B-rep 体积/表面积、XYZ 尺寸、面/边/顶点数、几何实体数和曲面类型等完整 JSON 特征。

**本阶段是特征提取与查询，不是自动弹片识别，也不修改 CST 原工程。**

## Windows PowerShell 操作

在仓库根目录执行。请把示例目录替换成你已导出成功的 `*.solid_export_diagnostic` 目录。

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
uv sync --extra viewer

# 改为你的实际全量 STEP 导出目录
$GeometryDir = "D:\CST\Original.solid_export_diagnostic"

# 首次建议验证前 20 条（可选）
uv run --extra viewer python -m cst_material.geometry_library "$GeometryDir" --limit 20

# 正式处理全部 Solid；自动复用前 20 条
uv run --extra viewer python -m cst_material.geometry_library "$GeometryDir"

# 查看数据库记录数与状态
uv run python -c "import sqlite3,sys; from pathlib import Path; p=Path(sys.argv[1])/'geometry_features.sqlite3'; c=sqlite3.connect(p); print(c.execute('SELECT status,count(*) FROM solids GROUP BY status').fetchall()); c.close()" "$GeometryDir"

# 查看器中选择 Solid，右侧会显示特征库状态和核心几何指标
uv run --extra viewer python view_solids.py "$GeometryDir"
```

## 输出与断点续算

- `geometry_features.sqlite3`：正式 SQLite 数据库；每个 Solid 一条记录。
- `geometry_features_report.json`：本次处理总数、成功、失败、复用数及失败详情。
- 每个 Solid 完成后提交一次事务。中途退出可重复运行相同命令，成功且哈希未变化的 Solid 自动跳过，失败项重新分析。
- 对于 `manifest.json` 发生变化的情况，程序拒绝复用原库；请先备份并移走旧数据库及可能存在的 `-wal`、`-shm` 文件，再重新建立。
- 若 STEP 文件与清单 SHA256 不一致，该 Solid 标记为 `FAIL`，不会误用旧特征。
- `--limit N` 只用于分批验证；正式运行不加该参数。

## 验收

全量执行后，报告 `status=PASS`、`failed=0`，数据库 `PASS` 数量应等于 `manifest.json` 中 Solid 数量；三维查看器可选择具体 Solid 并看到其特征库信息。

本阶段不需要上传工程文件或 STEP；如有失败，可提供脱敏后的 `geometry_features_report.json` 中错误条目。
