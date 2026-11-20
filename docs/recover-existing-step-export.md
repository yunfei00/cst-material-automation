# 已导出 8000+ Solid 后修复 7 个 STEP 读回失败（无需重新导出）

## 为什么没有 manifest.json？

当前 `all_solid_export.py` 在最后仅当 `failed == 0` 时生成 `manifest.json`。只要 7 个对象被 `read_step()` 判为非封闭实体，整体状态就是 `PARTIAL`，程序会删除 manifest。**其余已成功导出的 STEP 文件不会因此消失。**

## 现在怎么操作（Windows PowerShell）

先关闭查看器，在仓库根目录执行以下命令。把导出目录替换为真实路径，**不是 CST 工程文件路径**。

```powershell
git switch main
git pull --ff-only origin main
$dir = "D:\\CST\\Original.solid_export_diagnostic"
uv run --extra cad python repair_export.py "$dir" --dry-run
uv run --extra cad python repair_export.py "$dir"
```

第一条 `--dry-run` 只分析七个失败条目，输出 `recovered`、`surface`、`failed`；第二条才更新报告。**两条命令都不会启动 CST，不会删除或重导任何 STEP，不需要重新等待两小时。**

脚本在修改前备份 `report.json` 和已有的 `manifest.json`（如果存在），备份文件名包含时间戳。失败文件存在且检查为有效曲面时，报告改为 `PASS_SURFACE`；如果检查为封闭实体，改为 `PASS`；损坏或缺失的 STEP 仍保持 `FAIL`，记录 `repair_error`。

## manifest 与下游兼容性

新生成的 `manifest.json` **仅包含可供现有 `read_step()/analyze_step()` 处理的封闭实体**，避免后续构建特征索引时在曲面处再次失败。有效曲面保留在 `report.json` 中并计入成功数，`manifest.json` 记录 `excluded_surface_count`。如仍有失败项，也生成已验证实体的部分清单，并记录 `excluded_failed_count`。不伪造曲面的实体体积。

若 `dry-run` 显示 recovered 为 0，先检查失败文件是否存在、是否被其他程序占用；**不要直接将 FAIL 文本替换为 PASS，也不要重新执行全量导出脚本**。这项恢复功能不支持自动重导缺失 STEP。

注意：此修复恢复的是**已有文件的报告和清单**；原有全量导出命令的失败重导策略尚未更改。
