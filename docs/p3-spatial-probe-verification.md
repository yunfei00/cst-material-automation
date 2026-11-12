# CST P3-C 非 History 空间特征查询（实验性）

> 2026-11-12：P1/P2/P3-A/P3-B 已由用户确认真实运行通过；**P3-C 尚未进行 CST 2025 SP1 真实验证**。本分支仅开发新空间查询，不改变已经验证的接口。

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

1. 不再在 `with TemporaryDirectory` 离开时强制删除目录；使用显式受控的临时目录和有限次数清理重试。
2. 如果 CST 仍占用副本，**不终止 CST 进程、不删除正在使用的文件**。若查询数据成功，则报告 `status=PARTIAL`、`spatial.cleanup_deferred=true`，并把待清理目录写入**本地** `spatial.cleanup_path`，不能标记为完整 PASS。
3. 如果 COM 宏本来就失败，优先抛出真实的 `CST P3-C OLE/RunScript 失败`；清理时的 WinError 32 只作为额外本地警告，不能覆盖原始异常。
4. 如果复制源工程到临时目录或读取宏输出时被独占，分别给出中文诊断，不把两个环节混成同一种故障。
5. 仅修改 P3-C；P1/P2/P3-A/P3-B 保持已通过的工作流。

**本机处理：** 更新 P3-C 分支并重新运行同一 `spatial` 命令。若报告为 PARTIAL，在确认没有 CST 正在使用该**临时副本**后，再按 `cleanup_path` 仅清理该 P3-C 临时目录；**不要删除源 .cst 文件，不要关闭其他 CST 工程，不要强行终止 CST.exe**。若为 FAIL，提供脱敏的 `error` 和调用栈最后几行即可进一步区分：复制源工程、OLE 打开、VBA 输出还是最终清理。

此修复通过模拟 Windows 文件占用/重试测试后，仍需真实 CST 再次验证；GitHub CI 无法模拟 CST 的实际锁释放时序。

## 安全机制

- 仅将 `.cst` 复制到 Windows 临时目录，项目对象从复制文件打开。临时脚本、输出放在同一隔离目录。
- 工具只调用临时工程项目对象的 `Quit`，**不**调用全局 CST 应用对象的 `Quit`，避免关掉用户其他 CST 窗口。
- 从不调用 `Save`、`SaveAs`、`AddToHistory` 或材料修改接口；无权限和未知的返回结果均停止。
- 原始几何与材料数据只保存在被 `.gitignore` 忽略的 `local-data/` 报告，不上传 GitHub。

本功能仅为下一步几何空间关系、结构匹配提供候选特征；不能据此直接决定物料或材料赋值。
