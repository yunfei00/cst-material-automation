# STEP.Write 组件路径修复：公司 CST 2025 实机复验

## 已确认的调用问题

用户在 CST 中定位到：`STEP.Write` 接收组件路径，旧实现将 `Component:Solid` 转成 `Component/Solid`，导致 `Not an object reference`。现在将 `STEP.Write` 的参数改为 **仅组件路径**，例如：

```vb
With STEP
    .Reset
    .FileName ("D:\CST\sample.step")
    .ExportFromActiveCoordinateSystem (False)
    .Write ("component1")
End With
```

程序仍保留 `component1:solid1` 用于逐条日志及原始实体枚举。**重要：Write 是组件级导出，组件可能含多个 Solid。** 诊断报告的每条记录代表一次针对某个 Solid 所属组件的导出尝试，不能把生成的 STEP 直接当成单个 Solid 的几何文件。若多个 Solid 属于同一组件，可能生成内容重复的 STEP。原 `run_project.py` 的逐 Solid 几何映射尚不能据此视为可靠，本次只验证转换器能否成功导出。

## 执行步骤（Windows PowerShell，仓库根目录）

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'git switch 失败' }
git pull --ff-only origin feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'git pull 失败' }
uv sync --group dev
if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
uv run --group dev python -m pytest tests/test_step_export_vba.py tests/test_all_solid_export.py -q
if ($LASTEXITCODE -ne 0) { throw '回归测试失败' }
uv run python export_solids.py "D:\CST\Original.cst"
```

只替换最后一行工程路径。自动枚举所有 Solid，单条导出失败不影响后续 Solid。输出目录为 `Original.solid_export_diagnostic`，含 `report.json` 和 `steps/`。为避免覆盖，该目录必须事先不存在；如已存在，请先保留或重命名旧目录。

## 验收点

| 编号 | 操作 | 预期 |
| --- | --- | --- |
| W-01 | 执行 pytest | 所有回归测试通过，`STEP.Write` 不含 Solid 名 |
| W-02 | 查看 `report.json` 的 `items[].vba` | `.Write ("组件路径")`，没有追加 `/Solid` |
| W-03 | 检查成功记录 | STEP 文件存在且非空，报告含字节数与耗时 |
| W-04 | 故意包含可能失败的组件 | 失败记录保留原始异常，后续 Solid 仍继续 |
| W-05 | 检查组件有多个 Solid 的情况 | 注意不同尝试可能导出同一组件，暂不宣称单 Solid 对应关系 |
| W-06 | 检查原 CST 文件 | 未修改原始文件 |

## 请反馈

只发脱敏数据：`total/success/failed`、一条 `items[].vba`、一条失败的 `items[].error`、以及成功 STEP 是否能在 CST 或 CAD 查看器中打开。不需要上传保密工程。

## 验收状态

- 2026-11-13：已按公司实机反馈修改组件路径传参，并更新回归测试。
- GitHub Actions：以**最新提交**运行结果为准，须全部通过。
- CST 2025 SP1 实机：**待用户复验**。
