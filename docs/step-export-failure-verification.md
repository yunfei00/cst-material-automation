# CST 2025 STEP.Write 导出失败：排查与实机验收

## 现象和原因范围

`run_project.py` 在 `CST_STEP_EXPORT` 阶段报 `RuntimeError: an error occurred while trying to execute add to history. Not an object reference`。**不能直接断言是 Solid 名称无效。**

代码从 `Solid.GetNameOfShapeFromIndex` 获取 `Component:Solid`；CST STEP 导出 VBA 文档的 `STEP.Write` 使用 `Component/Solid`，因此冒号转斜杠是有文档依据的。2026-11-13 修改导出 VBA 为括号调用形式：

```vb
With STEP
    .Reset
    .FileName ("C:\Temp\sample.step")
    .ExportFromActiveCoordinateSystem (False)
    .Write ("component1/solid1")
End With
```

**注意：这是语法规范化，不是已证实的故障根因。** 真实 CST 2025 SP1 的 `add_to_history`、`STEP.Write` 及导出权限仍需要实机测试。不会自动换成 `:`、跳过失败 Solid 或用 `WriteAll` 替代单件导出，否则会破坏 Solid-ID 对应关系。

## 一、Windows 更新与自动化检查

在仓库根目录执行，替换最后一行两个路径。**输出路径必须不存在**，避免覆盖工程；原始 CST 不修改。建议先用包含少量 Solid 的非生产副本。

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'git switch 失败' }
git pull --ff-only origin feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'git pull 失败' }
uv sync --group dev --extra viewer
if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
uv run --group dev --extra viewer python -m pytest tests/test_step_export_vba.py -q
if ($LASTEXITCODE -ne 0) { throw 'STEP VBA 回归测试失败' }
uv run --extra viewer python run_project.py "D:\CST\Original.cst" "D:\CST\ExportFixTest.cst"
```

## 二、验收与反馈

| 编号 | 操作 | 预期 |
| --- | --- | --- |
| EXP-01 | 运行 `tests/test_step_export_vba.py` | 全部通过；STEP.Write 采用斜杠及括号格式 |
| EXP-02 | 运行 `run_project.py`，查看导出阶段 | 如果正常，持续显示 STEP 导出进度；失败则明确报出失败 Solid、目标文件和 History 命令 |
| EXP-03 | 查看 `ExportFixTest.geometry/export_error.json`（失败时） | 能找到 `failed_solid` 和原始错误；不可把残留 STEP 当作完整导出 |
| EXP-04 | 如果导出成功 | 检查 `manifest.json` 的 Solid 数量与真实工程一致，且抽查 STEP 几何在查看器中正确 |
| EXP-05 | 检查源文件 | 原始 CST 文件未被覆盖 |

**请反馈（仅脱敏文本）**：EXP-01～EXP-05 的结果、`failed_solid` 的名称结构（可替换成 `component1:solid1`）、错误是否仍是 `Not an object reference`、错误出现在第几个 Solid。不要上传原始 CST、STEP、完整路径或材料信息。

## 三、如果仍然失败

需要用公司安装的 **CST 2025 SP1 Online Help → VBA_3D → STEP Object** 核对本机 `Write` 的准确签名，并在**工程副本**上单独执行同一条 `With STEP ... End With` 宏，确认是否能创建 STEP。若手工宏也失败，优先检查 STEP 导出对象、当前工程上下文、路径及权限；若手工成功而 Python 失败，再检查 `add_to_history` 执行上下文和历史树重放限制。**在获得该实机反馈前，不声称导出问题已经彻底解决。**

## 四、CI 与实机验收记录

- 2026-11-13：修复分支 `feat/solid-structure-recognition-v1`，新增 `build_step_export_vba` 和导出语法测试。
- 此文档所在最新提交必须在 GitHub Actions 全部通过后交付。
- Windows CST 2025 SP1 实机：**待用户复验**。
