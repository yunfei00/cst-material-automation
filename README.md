# CST Material Automation

面向 **CST Studio Suite 2025 SP1** 的离线结构识别与材料赋值工具（开发中）。

## 目标

Creo 装配导入 CST 后可能产生大量未赋材料的 Solid。工具计划读取 Solid、Component 和已有材料，结合几何与空间关系提供**材料候选**，经工程师确认后批量赋值并另存为新的 CST 工程。

**几何形状不能唯一确定材料，不能未经确认自动猜测材料。**

## 已确认的技术路线

- 公司 Windows 已安装 CST 2025 SP1，用户确认可以通过 Python 调用。
- CST 安装目录 Library 中提供可用 pip 安装的 Python Package；调用入口为 `import cst`。
- 此前 `uv` 项目环境探测结果四项模块均为 false，只说明该虚拟环境当时未安装对应包；**不再重复做环境扫描**。
- 真实 CST API 的具体方法与参数，下一步以用户已有的 Python 调用代码为准，不猜测接口。

## 当前代码有什么

| 内容 | 作用 |
| --- | --- |
| `src/cst_material/domain.py` | Solid、Material、BoundingBox 等基础数据类型 |
| `src/cst_material/geometry.py` | 包围盒尺寸、体积、中心点计算 |
| `src/cst_material/` 中其他基础文件 | CST 适配器接口约定与包初始化 |
| `tests/test_geometry.py` | 几何计算的自动单元测试 |
| `pyproject.toml` | Python 项目依赖和测试配置 |
| `.github/workflows/ci.yml` | Windows 上自动运行 Ruff 和非真实 CST 单元测试 |

已删除无业务必要的 `probe.py`、`discovery.py`、`cli.py`、`verification.py` 及其对应测试。

**当前尚未实现真实 CST 工程读取、材料赋值或保存回读。**

## 开发者测试

```powershell
uv sync --group dev
uv run ruff check .
uv run pytest -m "not cst_real"
```

这些命令是开发者和 CI 用于检查 Python 代码的，**不要求工程师反复执行**，也不证明 CST 真实功能已通过。

## 后续开发

1. 根据已验证的 `import cst` 代码实现最小 CST 适配器。
2. 读取测试工程中的 Solid、Component 和 Material。
3. 只在工程副本上赋值，另存并重新打开核对。
4. 再逐步实现几何分析、材料候选、人工确认和中文 UI。

见 [研发路线图](docs/roadmap.md)、[架构设计](docs/architecture.md)、[真实 CST 验证说明](docs/p0-windows-verification.md)。

## 安全与协作

- 公司 ASM、PRT、SAT、CST、材料库、几何数据、截图、原始日志与内部路径不得提交到个人 GitHub。
- 开发者维护代码、测试和 CI；工程师只需要验证确实依赖本机 CST 的业务功能。
- 新增文件应有明确业务目的，不再为了环境探测增加层层框架。
- **本分支仍是开发分支，未经授权不合并 main。**
