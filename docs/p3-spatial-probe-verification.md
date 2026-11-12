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

## 安全机制

- 仅将 `.cst` 复制到 Windows 临时目录，项目对象从复制文件打开。临时脚本、输出放在同一隔离目录。
- 工具只调用临时工程项目对象的 `Quit`，**不**调用全局 CST 应用对象的 `Quit`，避免关掉用户其他 CST 窗口。
- 从不调用 `Save`、`SaveAs`、`AddToHistory` 或材料修改接口；无权限和未知的返回结果均停止。
- 原始几何与材料数据只保存在被 `.gitignore` 忽略的 `local-data/` 报告，不上传 GitHub。

本功能仅为下一步几何空间关系、结构匹配提供候选特征；不能据此直接决定物料或材料赋值。
