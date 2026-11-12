# CST 2025 SP1 — P3-A / P3-B 验收说明

> P1/P2 已于 2026-11-12 在真实 CST 2025 SP1 工程中通过。**2026-11-12 用户反馈 P3-A 和 P3-B 全部运行通过**，已完成当前最小闭环的实际环境验证；新增几何/材料能力仍需另行验收。公司结构、材料名称、文件清单、几何数据和本机报告 **禁止上传 GitHub**。

## 范围与重要限制

- **P3-A `geometry`**：从已有 CST 工程的**临时副本**中读取 Solid 体积、表面积及长度单位；不保存原工程。
- **P3-B `scan-mtd`**：只索引外部 MTD 文件元数据（文件名候选、目录、大小、时间、SHA256），不导入 CST、不修改文件、不尝试解析 MTD 内部物性。
- **不包含**：MTD 批量加载、自动材料匹配、批量赋值、图形界面。

官方 CST 2025 VBA Solid Object 明确规定：`GetLooseBoundingBoxOfShape` **不能在 History 重建或结构宏执行时使用**。现用 Python `Model3D.add_to_history` 会执行 History，因此 P3-A **没有调用包围盒接口，也不声称获取了位置、尺寸、厚度和空间关系**。此部分需研究可用的非 History 执行入口后再做。

## Windows 运行步骤

在项目根目录运行，使用已经通过 P1/P2 的 CST Python 环境，无需重复安装。

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw '获取分支失败' }
git switch feat/cst-p3-geometry-catalog
if ($LASTEXITCODE -ne 0) { throw '切换分支失败' }
git pull --ff-only origin feat/cst-p3-geometry-catalog
if ($LASTEXITCODE -ne 0) { throw '更新分支失败' }
$env:PYTHONPATH = Join-Path (Get-Location) 'src'

# 改为本机真实测试工程与外部材料库位置
$source = 'D:\CST_Test\Existing.cst'
$library = 'D:\CST_Custom_Library'

# P3-A：读取几何数据；不会保存工程
python -m cst_material.cli geometry --source $source --report 'local-data\P3A_Geometry_Report.json'
if ($LASTEXITCODE -ne 0) { Write-Warning 'P3-A 执行失败，查看本地报告' }
Get-Content 'local-data\P3A_Geometry_Report.json' -Encoding UTF8

# P3-B：只读索引 MTD，运行时不需要 CST
python -m cst_material.cli scan-mtd --library-root $library --report 'local-data\P3B_MTD_Catalog.json'
if ($LASTEXITCODE -ne 0) { Write-Warning 'P3-B 执行失败，查看本地报告' }
Get-Content 'local-data\P3B_MTD_Catalog.json' -Encoding UTF8
```

默认本地报告在 Git 忽略的 `local-data/`。为保护材料库，程序会拒绝将 P3-B 报告写在材料库目录内部。

## P3-A 输出与验收

- `geometry.length_unit`：工程长度单位（例如 mm），`length_unit_to_m`：每一个工程长度单位对应的米数。
- `features[].identifier`、`component`、`material`：保持 CST 官方 VBA 查询到的实体标识和材料。
- `volume_project_unit3`、`area_project_unit2`：体积与表面积，单位分别为工程长度单位³/²。
- `volume_m3`、`area_m2`：换算为 m³ 和 m² 的结果。
- `compactness`：`36πV²/A³`，无量纲，仅为几何统计特征，**不是材料识别结论**。体积或面积为零时置空。
- `zero_volume`：体积为零的提醒，不应据此推断材料。
- `geometry_scope`：明确没有包围盒、空间坐标与相互关系。

**实机验收：** P3-A PASS，Solid 数量与 P1 一致；抽查几个简单几何体的体积、表面积、单位及换算数值；确认原始 CST 工程没有被修改。`Solid.GetVolume` / `Solid.GetArea` 已经在用户机器上完成 P3-A 命令实测（用户确认通过）。具体几何数值的抽样比对记录尚未提供，不能将所有模型的数值精度和特殊实体兼容性标记为全面验证。

## P3-B 输出与验收

- `files[]`：递归扫描到的 .mtd 文件、相对路径、大小、UTC 修改时间和 SHA256。
- `candidate_name`：仅由**文件名去掉扩展名**推测，`name_verified=false` 和 `properties_verified=false`。不等于 CST 真正的 Material.Name。
- `duplicate_candidate_names`：大小写不敏感的候选重名报告；`skipped_symlinks`：跳过符号链接的数量。
- 对没有 MTD 文件、无权访问的路径或写入库内报告，清晰返回 FAIL。

**验收：** 与本机外部目录的 MTD 文件数核对，抽查 SHA256，确认原始 MTD 文件完全不变。材料数据不应上传到个人仓库。

## 维护与后续

- 本次仅新增独立 CLI 命令和本地数据结构，不修改已经实机验证通过的 P1/P2 业务逻辑。
- 无 CST 的 GitHub CI 只验证模拟操作、数据校验和安全边界；P3-A/P3-B 已经由用户确认实际运行通过。未获取可公开的详细样本核对记录，因此不将全部材料内容、物性或几何精度当作已验证。
- 后续先单独验证**非 History 环境**的几何包围盒调用，再增加位置、尺寸、厚度、邻接等 P3-A 特征；P3-B 后续经人工确认或官方 API 读取材料物性后，再进入 P4 可解释匹配。

**参考：** CST 2025 Online Help 中 `VBA_3D/common_vbasolido/common_vbasolido_solid_object.htm`、`VBA_3D/common_vbaunitso/common_vbaunitso_units_object.htm`，以及 Python Libraries `cst.interface.Model3D.add_to_history`。
