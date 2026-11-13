# CST 全部 Solid 独立导出：快速验收

## 唯一输入

仅输入现有 `.cst` 工程路径。程序自动复制工程到临时目录、枚举所有 Solid，并逐个导出 STEP。单件失败只记录错误，**继续处理下一件**。不清理工程、不识别结构、不赋材料、不修改源工程。

在仓库根目录的 Windows PowerShell 中运行：

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch 失败' }
git switch feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'switch 失败' }
git pull --ff-only origin feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'pull 失败' }
uv sync --group dev
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
uv run --group dev python -m pytest tests/test_all_solid_export.py -q
if ($LASTEXITCODE -ne 0) { throw '单元测试失败' }
uv run python export_solids.py "D:\CST\Original.cst"
```

将最后一行路径替换为本机工程路径。**不需要输入 Solid 名称或输出路径。**

## 自动输出

假设输入 `D:\CST\Original.cst`，输出在 `D:\CST\Original.solid_export_diagnostic\`：

- `steps/000001_<hash>.step` 等：每个成功导出的 Solid 单独文件，文件名不会依赖特殊字符。
- `report.json`：完整 JSON 日志。包含工程打开阶段、全部 Solid 名称、逐件 VBA、导出耗时、成功/失败、STEP 文件大小、原始异常和 traceback。
- 终端实时显示 `SOLID_START`、`SOLID_PASS`、`SOLID_FAIL`，最后汇总总数、成功数、失败数。
- 每个 Solid 完成后立即更新报告，意外中断时也可检查已完成记录。

为了避免覆盖原有测试，**输出目录已存在时拒绝运行**。第二次测试可使用另一份命名不同的 CST 副本，或在确认备份后自行重命名上次的诊断目录。

## 验收表

| 项目 | 操作 | 预期 |
| --- | --- | --- |
| A1 | 输入单个 CST 路径 | 自动枚举全部 Solid，不再要求手动名称 |
| A2 | 观察 `CATALOG_OK` | 记录总数与完整 Solid 名称 |
| A3 | 检查导出结果 | 成功的 STEP 文件存在且大于等于 100 字节 |
| A4 | 有 Solid 报 `Not an object reference` | 记录错误、VBA 和 traceback，并继续下一个 Solid |
| A5 | 查看 `report.json` | `total = success + failed`（完成全部循环时） |
| A6 | 检查源 CST | 原工程未被修改 |

## 反馈格式

请仅反馈脱敏内容：`total/success/failed`、一条失败 Solid 的 `solid_id`（可改名）、`vba`、`error`，以及后续 Solid 是否仍继续导出。原始工程和 STEP 不需要上传。

## CI 与实机验收

本文件所在最新提交需通过 GitHub Actions。真实 CST 2025 SP1 逐件导出仍待公司实机验证，不能用模拟 CI 代替。
