# P3-D：7,311 个 Solid 的分批邻近分析（2026-11-12）

> 本功能在 `fix/p3c-win32-lock-diagnosis`（用户确认 P3-C 实机运行通过）基础上新增。优先离线读取之前的 P3-C 报告，**不重新打开 CST**。单元测试使用公开可分享的合成数据；公司工程、空间坐标和本机结果不得上传公共 GitHub。

## 本轮解决的问题

- 旧代码对超过 800 个 Solid 的邻近分析返回 `SKIPPED`；实际工程有 **7,311 个有效 Solid**，因此之前只得到包围盒，没有邻近候选。
- 现在按 32 个 Solid 一批（可设置 1～128）用 NumPy 广播计算 AABB 间距，只保留每个 Solid 最近的前 K 个候选。**不构建完整 N × N 距离矩阵**。
- 全部比较仍然是 O(N²) 时间，但临时距离矩阵是 **O(batch × N)** 大小；默认上限 12,000 个有效 Solid，超出明确 `SKIPPED`，而不是无边界内存增长。
- 距离相同（特别是非紧包围盒大量重叠、间距 0）时，按 Solid `identifier` 稳定排序；有缺失包围盒的 Solid 不参与邻居计算，报告不会伪造坐标。
- 包围盒距离只是**真实实体之间距离的下界**，不等于真实几何间距；包围盒重叠不等于两个实体真的接触。
- 保留 P3-C 文件锁诊断分支的全部代码；P1/P2/P3-A/P3-B 工作流未修改。

## 首选：直接对现有 P3-C JSON 进行离线邻近分析

在 Windows PowerShell 中进入仓库根目录，直接执行（把输入文件名改为上次真实生成的 P3-C 本地报告即可）。**无需启动 CST、无须反复读取工程。**

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/cst-p3d-large-neighbors
if ($LASTEXITCODE -ne 0) { throw '切换 P3-D 分支失败' }
git pull --ff-only origin feat/cst-p3d-large-neighbors
if ($LASTEXITCODE -ne 0) { throw '拉取 P3-D 分支失败' }
$env:PYTHONPATH = Join-Path (Get-Location) 'src'

# 使用已有的 P3-C 本地 JSON；无需再启动 CST
$inputFile = 'local-data\P3C_Lock_Diagnostics.json'
if (-not (Test-Path -LiteralPath $inputFile)) {
    $inputFile = 'local-data\P3C_Spatial_Report.json'
}
if (-not (Test-Path -LiteralPath $inputFile)) {
    throw '未找到 P3-C 本地报告，请将 $inputFile 改为真实路径'
}
$outputFile = 'local-data\P3D_Neighbors_Report.json'

python -m cst_material.cli spatial-offline --report-in $inputFile --report $outputFile --neighbors 5 --batch-size 32
$code = $LASTEXITCODE
if ($code -ne 0) { Write-Warning "P3-D 返回代码 $code，请查看本地报告的 status/error" }

$r = Get-Content $outputFile -Raw -Encoding UTF8 | ConvertFrom-Json
[PSCustomObject]@{
  Status          = $r.status
  Stage           = $r.stage
  Error           = $r.error
  SourceSolids    = $r.input_summary.solid_count
  ValidBBox       = $r.input_summary.valid_bbox_count
  OriginalStatus  = $r.input_summary.source_query_status
  OldNeighborMode = $r.input_summary.source_neighbor_status
  NewNeighborMode = $r.neighbors.status
  Analyzed        = $r.neighbors.analyzed_solid_count
  NeighborsEach   = $r.neighbors.neighbors_per_solid
  Algorithm       = $r.neighbors.method
} | Format-List
```

**PowerShell 变量命名修正（实机反馈，2026-11-12）：** 早期说明误把 `$input` 用作 JSON 文件路径变量；`$input` 是 PowerShell 的**自动变量**，与函数/脚本块的管道输入枚举器有关，容易导致赋值或参数传递异常。用户实际采用 `$inputFile`，确认已解决该变量冲突、当前命令可以正常执行。之前近 25 万行、末尾 `labels.status=SKIPPED` 的描述针对的是旧报告，不是当前新报错。本文统一改用 `$inputFile`、`$outputFile`。后续 PowerShell 示例不再把 `$input` 作为普通自定义变量。原始 JSON 近 25 万行，**不要直接打印全文**，只输出摘要。最后的 `labels.status=SKIPPED` 与几何邻居的 `neighbors.status` 必须根据实际字段分别核对；仅凭后者/前者不能认定整体任务失败。

**注意：** 原来的 `neighbors.status=SKIPPED` 可被新的离线命令重新计算。若原始报告是 `FAIL`，则 P3-D 会拒绝继续；如果原始是 `PARTIAL`，但所有包围盒均有效（例如只是 CST 临时文件清理延迟），可以离线继续分析。离线报告中的 `status` 由本次分析的有效包围盒决定，与旧文件清理异常分开记录。

## 验收

- `input_summary.solid_count` 应等于 P3-C 原始 Solid 数量（如 7311）；`valid_bbox_count` 与 `missing_bbox_count` 对应。
- `neighbors.status` 应为 `PASS`，`analyzed_solid_count` 与有效包围盒数量相同；每个 Solid 最多 5 条候选，绝不将自身列入邻居。
- 选几个简单实体的包围盒，与手算最短 AABB 距离核对，确认非紧盒距离下界的语义。
- 随机几何、完全重叠与大规模合成样本全部由 CI 回归；对 7311 个合成 Solid，验证分批流程不触发 800 的跳过限制。
- 原始 P3-C JSON 字节不变；输入与输出路径相同则拒绝，禁止原始 CST 工程、MTD 数据或私有 JSON 上网。

如果想重新从 CST 工程查询，也可以在 P3-D 分支使用以前的 `spatial --source ... --neighbors 5`，会调用同样的分批算法。但**建议先使用离线模式**，避免 CST 重新加载和临时工程文件锁问题。

后续 P4 会将真实空间候选与 P3-A 体积、表面积和已确认材料知识结合，输出可解释材料推荐，始终需要工程师确认后再调用 P2。
