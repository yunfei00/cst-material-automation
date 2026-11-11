# CST Material Automation

面向 **CST Studio Suite 2025 SP1** 的 Windows 离线自动化工具。当前重点是已有工程的结构与材料读取（P1）和副本材料赋值（P2）。

## 输入和目标

**输入只有一个已经包含结构及材料定义的 `.cst` 工程。** 不做 SAT/ACIS 导入，不导入材料库，不创建空白 CST 工程。

- **P1** 读取工程的 Component、Solid、Material，并获取 Solid 当前材料关系。
- **P2** 对指定 Solid 赋予工程内已经存在的材料，另存一个新工程，重新打开验证。
- P3 后续研究结构特征、材料匹配推荐、人工确认和批量处理。

**仅凭几何形状无法唯一确定材料，P1/P2 阶段不自动猜测。**

## 现有代码和接口文档

| 代码 | 用途 |
| --- | --- |
| `src/cst_material/cst_api.py` | CST 连接、读取、材料赋值保存与回读工作流 |
| `src/cst_material/cst_bindings.py` | CST 三维模型树读取和 VBA Solid 材料方法适配 |
| `src/cst_material/cli.py` | P1 `inventory` / P2 `assign` 可执行命令 |
| `src/cst_material/domain.py` | Solid、Material、BoundingBox 类型 |
| `tests/test_cst_api.py`、`tests/test_cst_bindings.py` | 无需 CST 的单元测试 |
| `.github/workflows/ci.yml` | Windows 下的 Ruff + Pytest |

用户上传的 **CST Python Libraries HTML 官方帮助**已经用于确认：

- `cst.interface.DesignEnvironment()`
- `env.open_project(path)`
- `project.model3d.get_tree_items()`
- `project.model3d.add_to_history(header, vba_code)`
- `project.save(path, allow_overwrite=True)`、`project.close()`

CST 2025 完整 Online Help 已确认三维 VBA 官方接口：`Solid.GetNumberOfShapes`、`Solid.GetNameOfShapeFromIndex`、`Solid.GetMaterialNameForShape`、`Solid.ChangeMaterial`、`Material.Exists`。P1 在临时工程副本中用官方 VBA 结果交叉验证 Python 工程树；P2 在副本上赋值并回读。接口签名已有文档依据，但完整真实工程行为仍需 Windows CST 2025 SP1 实机验收。

**状态：接口适配代码与 CLI 已提交，尚未完成 CST 2025 SP1 真实工程验收。GitHub CI 仅证明无 CST 的模拟测试通过，不能代替真实验收。**

## 怎么运行？

完整 Windows PowerShell 命令、P1/P2 JSON 报告和失败诊断详见：

**[P1/P2 运行与验证文档](docs/p1-p2-verification.md)**

## 安全和约束

- 仅修改工程副本，不覆盖输入的原始 `.cst`。
- 默认不会上传任何原始工程、材料、几何数据或本地日志到 GitHub。
- 对于有外部依赖或结果目录的 CST 工程，单个 `.cst` 文件复制是否足够需要在本机确认。
- 已删除旧版冗余环境探测脚本，不再让工程师重复测试 CST/Python 是否安装。
- 本分支尚未合并到 `main`。

见 [研发路线图](docs/roadmap.md) 和 [架构设计](docs/architecture.md)。
