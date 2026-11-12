# CST Material Automation

面向 **CST Studio Suite 2025 SP1** 的 Windows 离线自动化工具。P1（结构/材料读取）与 P2（工程副本材料赋值并回读）已经由用户在真实 CST 工程上验证通过；当前新分支开发 P3 只读几何特征与 MTD 材料目录。

## 输入和目标

**输入只有一个已经包含结构及材料定义的 `.cst` 工程。** 不做 SAT/ACIS 导入，不导入材料库，不创建空白 CST 工程。

- **P1** 读取工程的 Component、Solid、Material，并获取 Solid 当前材料关系。
- **P2** 对指定 Solid 赋予工程内已经存在的材料，另存一个新工程，重新打开验证。
- **P3-A** `geometry`：在临时工程副本中查询 Solid 体积、表面积及长度单位。**包围盒接口在 History 中受官方限制，暂不提供尺寸/位置。**
- **P3-B** `scan-mtd`：建立外部 MTD 文件目录索引（文件名候选、大小、时间、SHA256），不解析、不修改、不导入材料。
- **P4** 后续才进行有依据的材料匹配推荐与人工确认。

**仅凭几何形状无法唯一确定材料，P1/P2 阶段不自动猜测。**

## 现有代码和接口文档

| 代码 | 用途 |
| --- | --- |
| `src/cst_material/cst_api.py` | CST 连接、读取、材料赋值保存与回读工作流 |
| `src/cst_material/cst_bindings.py` | CST VBA Solid/Material 枚举、材料查询与赋值适配 |
| `src/cst_material/cli.py` | P1 `inventory` / P2 `assign` / P3 `geometry` 与 `scan-mtd` |
| `src/cst_material/domain.py` | Solid、Material、BoundingBox 类型 |
| `src/cst_material/p3_geometry.py` | P3-A CST 官方 Solid 体积/表面积查询、单位与特征校验 |
| `src/cst_material/p3_mtd_catalog.py` | P3-B 只读 MTD 文件扫描与 SHA256 索引 |
| `tests/test_cst_api.py`、`tests/test_cst_bindings.py` | 无需 CST 的单元测试 |
| `.github/workflows/ci.yml` | Windows 下的 Ruff + Pytest |

用户上传的 **CST Python Libraries HTML 官方帮助**已经用于确认：

- `cst.interface.DesignEnvironment()`
- `env.open_project(path)`
- `project.model3d.get_tree_items()`（存在已复现的中文编码兼容问题，当前已停用）
- `project.model3d.add_to_history(header, vba_code)`
- `project.save(path, allow_overwrite=True)`、`project.close()`

CST 2025 完整 Online Help 已确认三维 VBA 官方接口：`Solid.GetNumberOfShapes`、`Solid.GetNameOfShapeFromIndex`、`Solid.GetMaterialNameForShape`、`Solid.ChangeMaterial`、`Material.GetNumberOfMaterials`、`Material.GetNameOfMaterialFromIndex`、`Material.Exists`。针对真实工程中 `get_tree_items()` 触发 `UnicodeDecodeError`（`0xB2`）的问题，**P1/P2 已改为直接使用官方 VBA 枚举 Solid 和 Material**；VBA 文件输出按 Windows 本机 ANSI 代码页读取。P2 查询后恢复干净工程副本，仅把实际材料修改写入最终输出工程。接口签名已有文档依据，且修复后的 P1/P2 已在真实 Windows CST 2025 SP1 通过。

**状态：P1/P2 已完成真实 CST 工程验证；P3-A/P3-B 尚未进行真实场景验收。GitHub CI 仅证明无 CST 的模拟测试通过，不能代替 P3 实机验收。**

## 怎么运行？

完整 Windows PowerShell 命令、P1/P2 JSON 报告和失败诊断详见：

**[P1/P2 已验证命令](docs/p1-p2-verification.md)**、**[P3 几何特征与 MTD 目录验收](docs/p3-geometry-mtd-verification.md)**

## 安全和约束

- 仅修改工程副本，不覆盖输入的原始 `.cst`。
- 默认不会上传任何原始工程、材料、几何数据或本地日志到 GitHub。
- 对于有外部依赖或结果目录的 CST 工程，单个 `.cst` 文件复制是否足够需要在本机确认。
- 已删除旧版冗余环境探测脚本，不再让工程师重复测试 CST/Python 是否安装。
- P3 在独立分支 `feat/cst-p3-geometry-catalog`，不会自动合并到 `main`；也不会修改已验证 P1/P2 的业务逻辑。

见 [研发路线图](docs/roadmap.md) 和 [架构设计](docs/architecture.md)。
