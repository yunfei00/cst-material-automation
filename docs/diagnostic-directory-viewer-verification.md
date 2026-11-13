# CST 导出诊断目录三维加载验收

本次直接打开 `export_solids.py` 生成的 **`Original.solid_export_diagnostic` 根目录**，不是 `steps` 子目录，也不需要生成 `manifest.json`。查看器读取 `report.json`，仅列出导出成功的记录，点击左侧记录时才加载对应 STEP，避免一次性加载全部三维几何。

Windows PowerShell，在仓库根目录执行：

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch 失败' }
git switch feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'switch 失败' }
git pull --ff-only origin feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'pull 失败' }
uv sync --group dev --extra viewer
if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
uv run --group dev --extra viewer python -m pytest tests/test_diagnostic_catalog.py -q
if ($LASTEXITCODE -ne 0) { throw '目录读取测试失败' }
uv run --extra viewer python view_solids.py "D:\CST\Original.solid_export_diagnostic"
```

将最后一行改成实际导出根目录。

| 验收项 | 预期 |
| --- | --- |
| V1 打开根目录 | 显示全部成功导出记录，不要求 `catalog.json` |
| V2 点击左侧任一记录 | 按需加载该记录的 STEP 并显示三维 |
| V3 搜索 Solid ID | 列表正确过滤 |
| V4 组件包含多个 Solid | 提示“组件级导出（可能包含多个 Solid）”；不同记录可能显示相同组件几何 |
| V5 失败记录 | 不作为可加载 STEP 展示；保留在 `report.json` 中 |
| V6 检查原工程 | 不修改源 CST 文件 |

注意：这只是组件级 STEP 的目录浏览，不意味着已建立逐 Solid 精确几何映射，也不用于自动材料赋值。实际 CST 2025 SP1 导出后的 GUI 加载需要本机验收。

反馈：目录记录数量、能否点击显示、是否出现重复组件、异常信息（脱敏）。GitHub CI 必须在本次提交完成后全部通过。
