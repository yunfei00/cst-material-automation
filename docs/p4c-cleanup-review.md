# P4-C：CST 结构清理预览——辅助件、变体与零体积复核

> **只读预览阶段**。不在 CST 中删除任何对象，不修改工程或材料，不能把 `Solid.GetVolume=0` 作为自动删除条件。原始 CST、P1/P3-A 报告仅在本地使用；公司工程和模型特征不上传公共 GitHub。

## 1. 从结构导入产生的常见待审查对象

| 类型 | 常见实例 | 操作原则 |
| --- | --- | --- |
| 纯辅助构造 | 定位辅助片、建模用辅助面、仅为几何布尔运算使用的中间体 | 在确认与真实仿真结构、端口、激励、材料接口无关后，按 **完整 Solid ID** 显式选择 |
| 参考对象 | 草图/定位线、基准平面、参考轮廓、坐标定位面 | 可能根本不是 `Solid`；不要强行通过 Solid 删除。若被其他几何或参数引用，保留 |
| 工装治具 | 装配夹具、定位夹具、加工支撑、测试工装 | 如仿真不包含治具，应讨论是否移除；测量夹具/接口可能是仿真边界的一部分 |
| 重复建模变体 | 同一零件不同设计版本、alternate A/B、候选形态 | 由工程师列出完整变体组及 `keep` 指定保留一个，不能按文件名自动挑最新 |
| 真实手机器件 | USB 座、耳机座、马达、前摄、FPC、摄像头连接器、屏蔽、天线 | **默认保留**。是否简化由具体 EMC/EMI、射频、SI/PI、天线目标决定，不按名称删除 |

**仅凭这次上传/导入的几何文件列表，工具无法事先知道哪些一定是辅助件。** 当前做法用名称提示“待审核”，不触发实际删除。对于摄像头连接器或金属外壳，在电磁仿真中可能构成散射体、屏蔽结构、接地或耦合路径。

## 2. 体积为零意味着什么？

P3-A 返回 `volume_project_unit3`、`area_project_unit2` 和 `zero_volume`。我们将其划为以下**只读复核标签**：

| 数据组合 | 可能几何类型 | 输出标签 | 处理 |
| --- | --- | --- | --- |
| `V = 0, A > 0` | Sheet、面、未封闭壳体、零厚度金属层或有问题的导入模型 | `ZERO_VOLUME_POSITIVE_AREA_KEEP_REVIEW` | 默认保留并审查物理作用 |
| `V = 0, A = 0` | 线/点/退化几何、导入失败或数值异常 | `ZERO_VOLUME_ZERO_AREA_DIAGNOSE` | 优先诊断，但绝不直接删除 |
| `V > 0` | 具有非零体积的结构 | 不生成零体积标签 | 继续按仿真目标审查 |

注意：数值“0”可能与几何退化、导入误差和单位/精度有关，不能从数值反推出唯一的 CST 几何类别。零厚度导电面、屏蔽面或边界结构可能十分重要。非紧包围盒尺寸也不能提供准确厚度。

## 3. 确定规则怎么表达？

本轮 **仅生成候选删除清单，不执行 CST 删除**。规则是本地 JSON：

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
  PlannedDeleteCount = $r.delete_count
  ReviewSuggestionCount = $r.review_suggestion_count
  ZeroVolumeCount = $r.zero_volume_review_count
  ZeroVolumeWithArea = $r.zero_volume_positive_area_count
  ZeroVolumeWithoutArea = $r.zero_volume_zero_area_count
  Error = $r.error
} | Format-List
```

CLI 只输出摘要，详情留在本机 JSON，不能将几千个 Solid 名称/位置上传到 GitHub。默认没有规则输入时，**`delete_count=0`，所有对象保留**。

## 5. 下一阶段的真实删除安全门槛

1. 工程师确认仿真范围与必须保留的电磁关键结构（USB、连接器、屏蔽、接地、天线等）。
2. 人工选出真正无用的完整 Solid ID，并明确每个变体组保留的实体。
3. 基于实际 CST 2025 官方文档验证安全删除接口、CST History/依赖关系和兼容性；先针对少量虚构/非生产模型做真实删除测试。
4. **只操作新工程副本**，不覆盖源文件。对删除前后 Solid ID 集合和剩余材料进行回读验证，数量与预期完全一致才算 PASS。
5. 再检查 CST 的端口、激励、边界条件、网格和仿真结果是否仍有效。**仅 Solid 数量正确不代表仿真正确。**

目前这一步**没有执行删除**，没有把 `V=0` 转换为确定可删条件。
