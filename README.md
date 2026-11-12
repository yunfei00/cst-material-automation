# CST Material Automation

面向 **CST Studio Suite 2025 SP1** 的 Windows 离线自动化工具。P1（结构/材料读取）与 P2（工程副本材料赋值并回读）已经由用户在真实 CST 工程上验证通过；当前新分支开发 P3 只读几何特征与 MTD 材料目录。

## 输入和目标

**输入只有一个已经包含结构及材料定义的 `.cst` 工程。** 不做 SAT/ACIS 导入，不导入材料库，不创建空白 CST 工程。

- **P1** 读取工程的 Component、Solid、Material，并获取 Solid 当前材料关系。
- **P2** 对指定 Solid 赋予工程内已经存在的材料，另存一个新工程，重新打开验证。
- **P3-A** `geometry`：在临时工程副本中查询 Solid 体积、表面积及长度单位。**包围盒接口在 History 中受官方限制，暂不提供尺寸/位置。**
- **P3-B** `scan-mtd`：建立外部 MTD 文件目录索引（文件名候选、大小、时间、SHA256），不解析、不修改、不导入材料。
- **P3-C** `spatial`：使用 CST OLE `Project.RunScript` 非 History 宏读取 Solid 非紧包围盒、轴向范围和包围盒中心；**用户已确认实际运行通过**。附带的 Windows 临时文件锁诊断保留。非紧包围盒不等于实际厚度或接触。
- **P3-D** `spatial-offline`：直接读取本地 P3-C JSON，在不运行 CST 的情况下，通过 NumPy 分批计算 7,311 个 Solid 的邻近候选；合成数据 CI 已通过，真实公司报告验收待完成。
- **P4-A** `structure-preview`：离线融合 P3-A 的体积/表面积、P3-C 的非紧包围盒和可选 P3-D 的邻近候选，生成有依据的形态**候选**，不推断材料、不赋材。
- **P4-B（后续）**：基于已核验的材料属性与业务规则，推荐有来源的候选材料，须由工程师确认。

**仅凭几何形状无法唯一确定材料，P1/P2 阶段不自动猜测。**

## 现有代码和接口文档

| 代码 | 用途 |
| --- | --- |
| `src/cst_material/cst_api.py` | CST 连接、读取、材料赋值保存与回读工作流 |
| `src/cst_material/cst_bindings.py` | CST VBA Solid/Material 枚举、材料查询与赋值适配 |
| `src/cst_material/cli.py` | `inventory`、`assign`、`geometry`、`scan-mtd`、`spatial`、`spatial-offline`、`structure-preview` |
| `src/cst_material/domain.py` | Solid、Material、BoundingBox 类型 |
| `src/cst_material/p3_geometry.py` | P3-A CST 官方 Solid 体积/表面积查询、单位与特征校验 |
| `src/cst_material/p3_mtd_catalog.py` | P3-B 只读 MTD 文件扫描与 SHA256 索引 |
| `src/cst_material/p3_spatial.py` | P3-C 非 History RunScript、包围盒、COM/WinError32 诊断与 P3-D 计算入口 |
| `src/cst_material/p3_neighbors.py` | P3-D 分批最近非紧 AABB 候选和离线 JSON 校验（不连接 CST） |
| `src/cst_material/p3_lock_diagnostics.py` | Windows Restart Manager 只读文件锁占用进程查询 |
| `src/cst_material/p4_structure.py` | P4-A 多报告一致性检查与可解释形态候选（不进行材料推荐） |
| `tests/test_cst_api.py`、`tests/test_cst_bindings.py` | 无需 CST 的单元测试 |
| `.github/workflows/ci.yml` | Windows 下的 Ruff + Pytest |

用户上传的 **CST Python Libraries HTML 官方帮助**已经用于确认：

- `cst.interface.DesignEnvironment()`
- `env.open_project(path)`
- `project.model3d.get_tree_items()`（存在已复现的中文编码兼容问题，当前已停用）
- `project.model3d.add_to_history(header, vba_code)`
- `project.save(path, allow_overwrite=True)`、`project.close()`

CST 2025 完整 Online Help 已确认三维 VBA 官方接口：`Solid.GetNumberOfShapes`、`Solid.GetNameOfShapeFromIndex`、`Solid.GetMaterialNameForShape`、`Solid.ChangeMaterial`、`Material.GetNumberOfMaterials`、`Material.GetNameOfMaterialFromIndex`、`Material.Exists`。针对真实工程中 `get_tree_items()` 触发 `UnicodeDecodeError`（`0xB2`）的问题，**P1/P2 已改为直接使用官方 VBA 枚举 Solid 和 Material**；VBA 文件输出按 Windows 本机 ANSI 代码页读取。P2 查询后恢复干净工程副本，仅把实际材料修改写入最终输出工程。接口签名已有文档依据，且修复后的 P1/P2 已在真实 Windows CST 2025 SP1 通过。

**状态（2026-11-12）：用户已确认 P1/P2、P3-A（CST 几何特征读取）和 P3-B（本地 MTD 文件目录扫描）均在实际环境中执行通过。** 这表明当前已实现的命令闭环可用；P3-C 的包围盒查询和临时文件锁诊断已由用户确认真实运行通过；用户反馈 P3-D 离线运行已正常（具体脱敏计数未提供）；P4-A 的启发式结构候选已完成 CI 自动测试，真实本地报告尚待验收。材料内部物性提取和智能匹配尚未开发。GitHub CI 负责模拟回归，不能代替后续新增能力的真实 CST 验证。

## 怎么运行？

完整 Windows PowerShell 命令、P1/P2 JSON 报告和失败诊断详见：

**[P1/P2 已验证命令](docs/p1-p2-verification.md)**、**[P3-A/B 已验证说明](docs/p3-geometry-mtd-verification.md)**、**[P3-C 空间查询及锁诊断](docs/p3-spatial-probe-verification.md)**、**[P3-D 大规模邻近验收](docs/p3d-large-neighbors-verification.md)**、**[P4-A 结构候选验收](docs/p4a-structure-preview-verification.md)**

## 安全和约束

- 仅修改工程副本，不覆盖输入的原始 `.cst`。
- 默认不会上传任何原始工程、材料、几何数据或本地日志到 GitHub。
- 对于有外部依赖或结果目录的 CST 工程，单个 `.cst` 文件复制是否足够需要在本机确认。
- 已删除旧版冗余环境探测脚本，不再让工程师重复测试 CST/Python 是否安装。
- P4-A 使用独立分支 `feat/cst-p4a-structure-features`，基于 P3-D 已验证的代码；不自动合并 `main`，不会修改 P1/P2/P3-A/B 与 P3-C 的工程操作流程。

见 [研发路线图](docs/roadmap.md) 和 [架构设计](docs/architecture.md)。
