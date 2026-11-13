# CST P3-C 非 History 空间特征查询（实验性）

> 更新（2026-11-12）：P1/P2/P3-A/P3-B 与 P3-C 空间查询、WinError32 诊断流程均已由用户确认实机通过。本篇部分命令记录了早期实验分支；**目前 7,311 个 Solid 的邻近分析已转入 P3-D**，详见 [离线大规模邻近分析](p3d-large-neighbors-verification.md)。

## 为什么不能使用 add_to_history

CST 2025 官方离线帮助 `VBA_3D/common_vbasolido/common_vbasolido_solid_object.htm` 的 `Solid.GetLooseBoundingBoxOfShape` 返回**非紧**全局包围盒，禁止在 History 重建或结构宏执行，面向后处理。

同一官方帮助 `VBA_3D/common_vbaapp/common_vbaappapplication_object.htm` 记录 `Project.RunScript(scriptname)`；`advanced/vbaapplicationobject.htm` 记录 `CSTStudio.Application.2025` 的 OLE 接口与 `OpenFile`。`vba/vba_macro_language_overview.htm` 区分命令宏与结构宏，并描述 `Sub Main` 入口。

因此本阶段使用 **Windows PowerShell OLE → 打开临时工程副本 → 调用 Project.RunScript 命令宏**，不安装 pywin32、不调用受限制的 History 方法。这个非 History 调用路径**有官方文档依据，但是否能在目标 CST 2025 SP1 实际执行还需要实机验证**；失败则返回错误，不伪造数据。

## 功能与精度边界

- 查询每个 Solid 的名称、当前材料以及 `xmin, xmax, ymin, ymax, zmin, zmax`。
- 计算 `dimensions_project_unit`（包围盒三轴范围）、`bbox_midpoint_project_unit`（包围盒中点）、`min_bbox_extent_project_unit`（三轴最小范围），并转换为米。
- 计算每个 Solid 最近的包围盒候选（默认 5 个）：`loose_bbox_distance_lower_bound_m` 是**真实实体间距的下界**，**不是**真实表面间距；0 只说明两包围盒重合或相接，不能证明两个 Solid 接触。
- 非紧包围盒的尺寸可能大于实际精确尺寸；包围盒中点不是实体质心，最小轴向范围也不是实际材料厚度。
- 当实体大于 800 时，自动跳过 O(N²) 邻居比较，但保留所有成功读取的实体包围盒。
- 不自动赋材、不加载 MTD、不启动求解器、不修改或保存任何 CST 工程。

## Windows 执行（PowerShell，在项目根目录）

使用已通过 P1/P3-A 的测试工程，首次仍建议非生产文件。需要替换本地工程路径：

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw '获取 P3-C 分支失败' }
git switch feat/cst-p3-spatial-probe
if ($LASTEXITCODE -ne 0) { throw '切换 P3-C 分支失败' }
git pull --ff-only origin feat/cst-p3-spatial-probe
if ($LASTEXITCODE -ne 0) { throw '更新分支失败' }

$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$source = 'D:\CST_Test\Existing.cst'
python -m cst_material.cli spatial --source $source --neighbors 5 --timeout 240 --report 'local-data\P3C_Spatial_Report.json'
Get-Content 'local-data\P3C_Spatial_Report.json' -Encoding UTF8
```

## 实机验收

1. `status=PASS`、`spatial.solid_count` 与 P1 数量相同；`identifier`、`material` 与 P1 一致。
2. 核对几个简单 Solid 的坐标范围。注意 loose bounds 是包围盒上界，可能比实际尺寸更大，但不能不包含实体。
3. 确认原工程没有修改或生成新增 History、没有保存原文件。
4. `PARTIAL` 说明有 Solid 的包围盒查询返回假；`FAIL` 说明 COM/脚本/输出/数值校验失败。只反馈脱敏后的 `stage`、`error`；公司工程文件和完整报告不得上传到公开仓库。
5. 发生 COM 超时或宏权限弹窗，不要强杀 CST 进程；确认临时副本无占用后再清理本地临时文件。

## 已知问题：WinError 32 临时工程文件被占用（2026-11-12）

实际运行 P3-C 时出现：

- `stage = CST P3-C 非 History OLE 命令宏空间查询`
- `PermissionError: [WinError 32]`，Windows 提示“另一个程序正在使用此文件，进程无法访问”。

**优先排查方向：** 最初的 P3-C 用 `TemporaryDirectory` 自动清理目录。命令宏通过 CST COM 打开临时 `.cst` 后，`Project.Quit()` 或 COM 引用释放可能不会立即解除 Windows 对副本的文件锁。即使查询结果已经成功写入，自动清理整个目录仍可能抛出 WinError 32，遮蔽真正的宏结果。**未看到完整的脱敏 traceback 前，不能百分之百断言发生于清理阶段**，也可能是源文件复制或输出读取被占用。

**此次修复：**

1. 在 PowerShell 中先尝试关闭**临时 CST 工程**，再显式释放该 PowerShell 进程持有的工程/应用 COM 引用；**不调用全局应用的 Quit**，以免关闭其他工程。
2. 不再在 `with TemporaryDirectory` 离开时强制删除目录；使用显式受控的临时目录和有限次数清理重试。
3. 如果 CST 仍占用副本，**不终止 CST 进程、不删除正在使用的文件**。若查询数据成功，则报告 `status=PARTIAL`、`spatial.cleanup_deferred=true`，并把待清理目录写入**本地** `spatial.cleanup_path`，不能标记为完整 PASS。
4. 如果 COM 宏本来就失败，优先抛出真实的 `CST P3-C OLE/RunScript 失败`；清理时的 WinError 32 只作为额外本地警告，不能覆盖原始异常。
5. 如果复制源工程到临时目录或读取宏输出时被独占，分别给出中文诊断，不把两个环节混成同一种故障。
6. 仅修改 P3-C；P1/P2/P3-A/P3-B 保持已通过的工作流。

**本机处理：** 更新 P3-C 分支并重新运行同一 `spatial` 命令。若报告为 PARTIAL，在确认没有 CST 正在使用该**临时副本**后，再按 `cleanup_path` 仅清理该 P3-C 临时目录；**不要删除源 .cst 文件，不要关闭其他 CST 工程，不要强行终止 CST.exe**。若为 FAIL，提供脱敏的 `error` 和调用栈最后几行即可进一步区分：复制源工程、OLE 打开、VBA 输出还是最终清理。

此修复通过模拟 Windows 文件占用/重试测试后，仍需真实 CST 再次验证；GitHub CI 无法模拟 CST 的实际锁释放时序。


## P3-C 根因诊断：清理 dp.parmap 时提示 WinError 32，但稍后可以手动删除

**实际观测（2026-11-12）**：P3-C 已读取包含 7311 个 Solid 的空间报告；邻近分析因旧版 800 个限制显示 `SKIPPED`（此状态与包围盒是否读取成功无关）。末尾清理临时工程 `result/dp.parmap` 时出现 `WinError 32`，但运行结束后手动删除成功。

**这说明文件占用可能是短暂的，不能直接归因于“没有权限”或“用户忘了关 CST”。** 也不能仅凭手动删除成功判断此前是哪一个进程持锁。需要在报错的瞬间收集证据。

诊断分支 `fix/p3c-win32-lock-diagnosis` 已增加：

1. PowerShell COM 生命周期节点：`application_connected`、`temporary_project_opened`、`macro_returned`、`project_quit_returned` 或 `project_quit_failed`、`powershell_com_finally_completed`。
2. Python 临时目录清理时间线：第一次失败的相对路径、Windows 错误码、重试次数、累计时间、是否最终释放。
3. 发生第一次文件共享冲突时，使用 Windows 自带 **Restart Manager 的只读接口** `RmGetList` 查询当前占用者 PID/进程名称；不需下载第三方程序，也**不会关闭或重启任何进程**。
4. 最多再尝试约 10 秒清理：若最终成功，记录 `lock_observed=true`、`cleanup_ok=true`、`cleanup_elapsed_seconds`；若持续失败，保留临时目录和查询结果，`status=PARTIAL`。
5. **占用者列表为空不证明没有文件锁**：锁可能已在查询前释放，或 Windows 接口无法列出该类占用。

### 只运行一次根因诊断（Windows PowerShell）

从已通过验证的 P3-A 同一个非生产 CST 工程运行。下面仅在终端打印摘要；**不要使用 Get-Content 不带字段过滤直接打印整个 JSON**：

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch fix/p3c-win32-lock-diagnosis
if ($LASTEXITCODE -ne 0) { throw '切换诊断分支失败' }
git pull --ff-only origin fix/p3c-win32-lock-diagnosis
if ($LASTEXITCODE -ne 0) { throw '更新诊断分支失败' }
$env:PYTHONPATH = Join-Path (Get-Location) 'src'

$source = 'D:\CST_Test\Existing.cst'
$report = 'local-data\P3C_Lock_Diagnostics.json'
python -m cst_material.cli spatial --source $source --neighbors 0 --timeout 240 --report $report

$r = Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
$d = $r.spatial.cleanup_diagnostics
[PSCustomObject]@{
  Status = $r.status
  Stage = $r.stage
  SolidCount = $r.spatial.solid_count
  MissingBBox = $r.spatial.missing_bbox_count
  CleanupOK = $d.cleanup_ok
  CleanupAttempts = $d.cleanup_attempts
  CleanupSeconds = $d.cleanup_elapsed_seconds
  FirstBlockedFile = $d.first_cleanup_failure.file
  FirstWinError = $d.first_cleanup_failure.winerror
  FirstOwnerStatus = $d.first_owner_snapshot.status
  FirstOwnerProcesses = ($d.first_owner_snapshot.processes | ForEach-Object { "$($_.application) PID=$($_.pid)" }) -join '; '
  ComEvents = $d.com_lifecycle_events -join ', '
} | Format-List
```

若本次清理直接成功且没有 `lock_observed`，只能说明这次没捕捉到共享冲突，**不能回溯证明上一轮占用者身份**。请勿为复现而主动锁定或删除生产工程。诊断记录仍留在本机 `local-data/`；可以分享脱敏摘要，不可上传整个 JSON 或公司工程。

**另外**：7311 个 Solid 的邻近 `SKIPPED` 是单独的性能限制；本诊断分支暂时将邻近设置为 0，以免与根因排查混淆。后续再单独开发和验收大规模邻近候选分析。


## 安全机制

- 仅将 `.cst` 复制到 Windows 临时目录，项目对象从复制文件打开。临时脚本、输出放在同一隔离目录。
- 工具只调用临时工程项目对象的 `Quit`，**不**调用全局 CST 应用对象的 `Quit`，避免关掉用户其他 CST 窗口。
- 从不调用 `Save`、`SaveAs`、`AddToHistory` 或材料修改接口；无权限和未知的返回结果均停止。
- 原始几何与材料数据只保存在被 `.gitignore` 忽略的 `local-data/` 报告，不上传 GitHub。

本功能仅为下一步几何空间关系、结构匹配提供候选特征；不能据此直接决定物料或材料赋值。
