# P4-C：CST 结构清理——规则 R001（体积为 0 的 Solid 全部删除）

> **已确认规则 R001（2026-11-12）：** 对 P3-A 实测 `Solid.GetVolume = 0` 的**所有 Solid**执行删除，不区分表面积、材质、名字或组件；其余 Solid 不因体积规则被删除。`cleanup-plan` 只读生成完整清单；`cleanup-apply` 仅在明确指定的新 `.cst` 副本执行 `Solid.Delete` 并回读。**源工程不修改；真实 CST 删除执行尚待实机验收。** 公司工程与报告不上传公共 GitHub。

## 1. 从结构导入产生的常见待审查对象

| 类型 | 常见实例 | 操作原则 |
| --- | --- | --- |
| 纯辅助构造 | 定位辅助片、建模用辅助面、仅为几何布尔运算使用的中间体 | 在确认与真实仿真结构、端口、激励、材料接口无关后，按 **完整 Solid ID** 显式选择 |
| 参考对象 | 草图/定位线、基准平面、参考轮廓、坐标定位面 | 可能根本不是 `Solid`；不要强行通过 Solid 删除。若被其他几何或参数引用，保留 |
| 工装治具 | 装配夹具、定位夹具、加工支撑、测试工装 | 如仿真不包含治具，应讨论是否移除；测量夹具/接口可能是仿真边界的一部分 |
| 重复建模变体 | 同一零件不同设计版本、alternate A/B、候选形态 | 由工程师列出完整变体组及 `keep` 指定保留一个，不能按文件名自动挑最新 |
| 真实手机器件 | USB 座、耳机座、马达、前摄、FPC、摄像头连接器、屏蔽、天线 | **默认保留**。是否简化由具体 EMC/EMI、射频、SI/PI、天线目标决定，不按名称删除 |

**仅凭这次上传/导入的几何文件列表，工具无法事先知道哪些一定是辅助件。** 当前做法用名称提示“待审核”，不触发实际删除。对于摄像头连接器或金属外壳，在电磁仿真中可能构成散射体、屏蔽结构、接地或耦合路径。

## 2. 已确定的规则 R001：V = 0 全删

P3-A 返回 `volume_project_unit3`、`area_project_unit2` 和 `zero_volume`。删除判定只依据实测 **`volume_project_unit3 == 0`**（精确等于 0，不设置阈值），具体如下：

| 数据组合 | 可能几何类型 | 输出标签 | 处理 |
| --- | --- | --- | --- |
| `V = 0, A > 0` | 任意对象 | `R001_ZERO_VOLUME` | **删除** |
| `V = 0, A = 0` | 任意对象 | `R001_ZERO_VOLUME` | **删除** |
| `V > 0` | 任意对象 | 不命中 R001 | **保留**，除非有其他显式规则 |

**规则优先级：** R001 已由工程师确认；名称提示仅用于其他规则的审查，不会对零体积删除做豁免。

## 3. 确定规则怎么表达？

`cleanup-plan` 会自动将全部 R001 命中的 Solid 加入 `zero_volume_delete_ids` 和 `delete_ids`。其他删除项仍用本地 JSON 显式指定，格式为：

```json
{
  "schema_version": 1,
  "confirmed_delete_ids": [],
  "protected_ids": [],
  "variant_groups": []
}
```

如果工程师确认需要移除某个辅助件，才将其**完整 Solid ID** 放进 `confirmed_delete_ids`。变体组格式示意：

```json
{
  "schema_version": 1,
  "confirmed_delete_ids": ["auxiliary:confirmed-unused-part"],
  "protected_ids": ["main:USB_solder"],
  "variant_groups": [
    {
      "group_id": "camera_variant_example",
      "members": ["module:cam_revA", "module:cam_revB"],
      "keep": "module:cam_revB"
    }
  ]
}
```

以上名字均为虚构示例，绝不在公司工程中凭空假设。规则检查：所有 ID 必须存在于 P1、不能重复，保护项不能删除，变体组至少两个成员，明确指定 `keep`，同一对象不能同时出现在两个组。

## 4. Windows PowerShell：先只读预览

沿用当前 P1 与 P3-A 实际报告，修改本地文件路径；**不用 PowerShell 自动变量 `$input`**。

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/cst-p4-structure-cleanup
if ($LASTEXITCODE -ne 0) { throw '切换分支失败' }
git pull --ff-only origin feat/cst-p4-structure-cleanup
if ($LASTEXITCODE -ne 0) { throw '更新分支失败' }

$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$sourceFile = 'D:\CST_Test\Existing.cst'
$p1Report = 'local-data\P1_Report.json'
$p3aReport = 'local-data\P3A_Geometry_Report.json'
$outputFile = 'local-data\P4C_Cleanup_Preview.json'

python -m cst_material.cli cleanup-plan --source $sourceFile --inventory-report $p1Report --geometry-report $p3aReport --report $outputFile
if ($LASTEXITCODE -ne 0) { Write-Warning '只读预览未通过，查看 error 字段' }

$r = Get-Content -LiteralPath $outputFile -Raw -Encoding UTF8 | ConvertFrom-Json
[PSCustomObject]@{
  Status = $r.status
  OriginalSolidCount = $r.source_solid_count
  RuleR001DeleteCount = $r.zero_volume_delete_count
  PlannedDeleteCount = $r.delete_count
  ReviewSuggestionCount = $r.review_suggestion_count
  ZeroVolumeCount = $r.zero_volume_review_count
  ZeroVolumeWithArea = $r.zero_volume_positive_area_count
  ZeroVolumeWithoutArea = $r.zero_volume_zero_area_count
  Error = $r.error
} | Format-List
```

CLI 只输出摘要，详情留在本机 JSON；**即使没有其他规则文件，`delete_count` 也等于 R001 命中的零体积 Solid 数量**。此步骤不执行修改。

## 5. 执行规则 R001：只修改新 CST 副本

1. 官方 CST 2025 离线帮助 `VBA_3D / Solid Object` 已核实 `Solid.Delete(solidname)` 方法。代码使用精确 Solid ID 调用，而不是删除 Component。
2. `cleanup-apply` 先比对源工程 SHA256、P1 实体与材料、P3-A 几何报告指纹和 R001 覆盖率；不一致拒绝执行。
3. 在**新工程副本**中调用 `Solid.Delete`，保存后重新打开，对所有剩余 Solid ID 与材料逐项核对；原工程绝不覆盖。
4. 如失败，不报告 PASS；副本可能保留供本机排查。**CI 模拟验收不能代替真实 CST 实机验证**。
5. 实机首次验证可先使用非生产工程，核对删除前后视图和端口/仿真设置；这不改变已确认的 R001 判断条件。


## 6. 一键执行真实 CST 删除（推荐）

本机执行一次命令：从 P3-A `geometry` 报告自动获得源工程路径，再使用 P1 `inventory` 和 P3-A 校验并生成删除计划；然后**真实删除**全部 V=0 的 Solid，保存新 CST 副本、重新打开核对所有剩余 Solid 和 Material。该操作并非只生成报告。

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/cst-p4-structure-cleanup
if ($LASTEXITCODE -ne 0) { throw '切换分支失败' }
git pull --ff-only origin feat/cst-p4-structure-cleanup
if ($LASTEXITCODE -ne 0) { throw '更新分支失败' }

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\cleanup_r001.ps1
if ($LASTEXITCODE -ne 0) { throw 'CST 删除或回读验收失败，请检查 local-data 报告' }
```

- 默认读取 `local-data\P1_Report.json` 与 `local-data\P3A_Geometry_Report.json`；如名称不同，可通过 `-InventoryReport`、`-GeometryReport` 参数指定。
- 默认每批执行 50 个 `Solid.Delete`，控制台实时打印批次进度。脚本处理完前不要关闭正在操作该副本的 CST。
- 新工程输出到**源工程同级目录**，命名 `原工程名_R001_Cleaned_时间戳.cst`；不覆盖原工程。`local-data/` 中分别留存清理计划和执行报告。
- 预检使用单独的临时 CST 副本，避免已知 `WinError 32`：CST 已关闭的项目文件句柄释放可能有延迟，不能马上覆盖同一个文件。
- 任意一步失败都不是 PASS，**不要使用未完成的副本进行仿真**；可能保留部分输出用于本机诊断。若预检临时文件清理失败，返回 PARTIAL 并记录诊断，不宣称完全成功。

**真实执行验收：** `Status=PASS`、`DeletedZeroVolumeSolidCount` 等于 P3-A 中所有精确 `V=0` 的 Solid 数、`RemainingSolidCount` 等于原数量减去删除数量、`RemainingMaterialsUnchanged=True`。

**限制：** 即使回读 PASS，也只证明 Solid 与已有 Material 一致；模型端口、激励、边界、网格和求解有效性仍需在新工程确认。是否改善旋转/缩放卡顿，必须实际打开新副本比较。公司工程和本地报告不上传公共 GitHub。
