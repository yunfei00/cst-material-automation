# CST Solid 导出与识别操作手册

日常流程是逐 Solid 导出 STEP、建立几何索引、打开查看器并人工复核。环境使用 Windows、CST Studio Suite 2025 SP1、Python 3.11 和 uv。识别与查看阶段可以离线运行。

## 1. 准备环境

在仓库根目录打开 PowerShell，修改工程路径。

```powershell
git switch main
git pull --ff-only origin main
uv sync --extra viewer

$project = 'D:\CST\Original.cst'
$geometryDir = Join-Path (Split-Path -Parent $project) (
    [IO.Path]::GetFileNameWithoutExtension($project) + '.solid_export_diagnostic'
)
```

## 2. 全量导出 STEP

```powershell
uv run --extra viewer python -m cst_material.all_solid_export $project
```

导出在工程临时副本上执行。程序按 CST 原始 `Component:Solid` 标识，将文件写入同目录下的 `.solid_export_diagnostic\steps`，并生成逐件结果 `report.json`。全部成功后生成映射清单 `manifest.json`。

中断或部分失败时，在 `report.json` 中查看失败记录，修正后重跑同一命令。程序校验并复用已成功的 STEP。原工程变化或输出目录属于旧版组件级导出时，先备份并移走旧目录。如果 STEP 已存在、失败出在几何回读，可先用以下命令预览修复。确认预览结果后，去掉 `--dry-run` 执行。

```powershell
uv run --extra cad python repair_export.py $geometryDir --dry-run
```

## 3. 建立几何索引

确认目录中已有完整的 `manifest.json` 后运行：

```powershell
uv run --extra viewer python -m cst_material.geometry_library $geometryDir
```

索引存为 `geometry_features.sqlite3`，可用 `geometry_features_report.json` 查看成功、失败和复用计数。重跑会复用 SHA256 一致的成功记录。若清单已变化且程序要求重建，先备份并移走旧数据库。

## 4. 查看与复核

```powershell
uv run --extra viewer python view_solids.py $geometryDir
```

在左侧选择 Solid 查看三维模型。可对选中件运行相似检索，也可使用主 PCB、屏蔽罩、弹片、螺丝、定位销、电池或屏幕筛选。确认结构类别后，可用界面保存本机结构样本。

验证手机屏幕时，勾选“屏幕”并检查候选等级、证据和层叠成员。前后盖几何相近时会显示歧义。首轮实机记录真实屏幕有没有进入候选、多收或漏收了哪些层，以及后盖、电池、PCB、石墨片等误报。可选用命令行生成同一份候选报告：

```powershell
uv run --extra viewer python -m cst_material.display_recognition $geometryDir
```

报告保存为 `display_recognition.json`。屏幕识别目前已有合成测试，真实工程准确率待验证。几何候选由工程师复核，材料身份另行确认。公司工程、STEP、索引及报告在本机保留。
