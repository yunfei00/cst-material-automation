# P4-A：Solid 几何结构候选（离线只读）

> 本阶段只提供**结构几何线索**，不是实际零件身份或材料推荐。公司 CST 工程、材料、空间数据和本地报告不得上传公共 GitHub。

## 数据输入与保护

- P3-A `geometry` 报告：Solid 标识、Component、已有 Material、体积、表面积、单位。
- P3-C `spatial` 报告：Solid 非紧包围盒；允许经过实际确认的 PASS/PARTIAL。
- 可选 P3-D `spatial-offline` 报告：各 Solid 最近邻的**包围盒间距下界**。

程序会核查 Solid 标识是否一致、单位是否一致、已有材料是否一致、来源路径（如果有）是否相同；若提供 P3-D，还会逐条交叉验证邻近间距与 P3-C 的实际包围盒。不同工程/不同版本的文件**不能混用**。即使来源路径相同，也不代表已经验证工程内容的版本指纹。

## 分类规则：仅用于非紧 AABB 的三轴范围

轴向范围由短到长记为 a、b、c：

| 输出类别 | 规则 | 含义 |
| --- | --- | --- |
| `elongated_box_candidate` | `c/b ≥ 4` | 长条形包围盒候选 |
| `flat_box_candidate` | 未命中上一条且 `a/b ≤ 0.15` | 扁平包围盒候选 |
| `near_equal_axes_box_candidate` | 未命中上两条且 `a/c ≥ 0.70` | 三轴范围较相近 |
| `other_axis_ratio_box_candidate` | 其他有限、非零轴向范围 | 其他 |
| `unknown` | 无包围盒、零范围或无法计算 | 不做分类 |

阈值是首版**工程启发式参考值**，尚未由标注样本集校准。非紧包围盒的尺寸不等于实体实际长宽厚；包围盒重叠不证明真实 Solid 接触。扁平形状不能直接判断为 PCB、金属片或塑胶。

每条结果附带理由 `reasons`、体积、表面积、原材料（仅观察值）、可选最近邻，且一律设置 `material_recommendation=null`、`needs_engineer_review_for_material=true`。

## Windows PowerShell：不启动 CST，一次性验证

在已有代码仓库根目录执行。仅使用本机 `local-data` 里已生成的三个报告；如文件路径不同，请修改对应变量。注意：**不要把 PowerShell 自动变量 `$input` 当普通自定义变量**。

```powershell
$ErrorActionPreference = 'Stop'

git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/cst-p4a-structure-features
if ($LASTEXITCODE -ne 0) { throw '切换分支失败' }
git pull --ff-only origin feat/cst-p4a-structure-features
if ($LASTEXITCODE -ne 0) { throw 'git pull 失败' }

$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$geometryReport  = 'local-data\P3A_Geometry_Report.json'
$spatialReport   = 'local-data\P3C_Lock_Diagnostics.json'
$neighborsReport = 'local-data\P3D_Neighbors_Report.json'
$outputFile      = 'local-data\P4A_Structure_Preview.json'

if (-not (Test-Path -LiteralPath $geometryReport)) { throw '未找到 P3-A 报告' }
if (-not (Test-Path -LiteralPath $spatialReport)) {
    $spatialReport = 'local-data\P3C_Spatial_Report.json'
}
if (-not (Test-Path -LiteralPath $spatialReport)) { throw '未找到 P3-C 报告' }

$argsForP4 = @(
    '-m', 'cst_material.cli', 'structure-preview',
    '--geometry-report', $geometryReport,
    '--spatial-report', $spatialReport,
    '--report', $outputFile
)
if (Test-Path -LiteralPath $neighborsReport) {
    $argsForP4 += @('--neighbors-report', $neighborsReport)
}
python @argsForP4
if ($LASTEXITCODE -ne 0) { Write-Warning 'P4-A 出错，检查 error 字段' }

$r = Get-Content -LiteralPath $outputFile -Raw -Encoding UTF8 | ConvertFrom-Json
[PSCustomObject]@{
    Status = $r.status
    Stage = $r.stage
    SolidCount = $r.solid_count
    ValidBBox = $r.valid_bbox_count
    WithNeighborData = $r.with_neighbor_data_count
    Categories = ($r.shape_hint_counts | ConvertTo-Json -Compress)
    Warnings = @($r.warnings).Count
    Error = $r.error
} | Format-List
```

## 自动验收条件

1. P4-A 只读取本地 JSON，不开启 CST，不修改源报告与 CST 工程。
2. P3-A 与 P3-C 标识集合完全对应；跨工程、已有材料不一致或损坏的数据必须拒绝。
3. 对有效包围盒生成可解释的结构候选；缺少空间数据时返回 `unknown`。
4. 任何记录都不含自动材料推荐，必须留工程师确认。
5. 对 7,311 个合成实体通过批处理与类型统计测试；真实工程的报告仍需本机验证。
6. CLI 不允许输出路径覆盖输入报告；终端只显示汇总，避免打印几十万行 JSON。

下一步 P4-B 再接入**经 CST 或工程师确认的材料属性与规则**，不会因为 MTD 文件名或包围盒形状就认定材料。
