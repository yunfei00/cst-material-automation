# P1/P2 验证说明（CST 2025 SP1）

## 先说明当前状态

**P1/P2 的纯 Python 流程已经编写，但尚未接入真实 CST 2025 SP1 的 Solid/Material 读取及材料赋值接口。当前不能进行真实业务验收。**

- P1 目标：打开已有 `.cst` 工程，列出 Component / Solid / Material。
- P2 目标：仅修改工程副本，赋予已有材料，保存、重新打开并核对。
- 已确认的 CST Python 方法：`cst.interface.DesignEnvironment()`、`env.open_project(path)`、`project.save(...)`、`project.close()`。
- 尚缺三个具体方法：枚举 Solid、枚举 Material、给 Solid 赋材料。需要根据 CST 官方手册或本机已验证示例补全 `CSTBindings`。
- **不要直接运行 `assign_and_verify()` 操作公司工程**：尚未完成真实 CST 接口和工程依赖文件验证。

## 现在可以运行的测试（只测试 Python 逻辑）

在仓库目录的 PowerShell 中运行以下整段命令：

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/cst-p0-probe
if ($LASTEXITCODE -ne 0) { throw '切换分支失败，请检查本地改动' }
git pull --ff-only origin feat/cst-p0-probe
if ($LASTEXITCODE -ne 0) { throw '更新分支失败，请检查本地改动' }
uv sync --group dev
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
uv run ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff 检查失败' }
uv run pytest -m 'not cst_real'
if ($LASTEXITCODE -ne 0) { throw '单元测试失败' }
```

预期：Ruff 无错误、pytest 通过。测试使用模拟 CST 对象，不会打开真实 CST 工程。

## 真正 P1/P2 验收时需要交付什么

在三个真实接口补齐之后，开发者必须提供：

1. 简单运行入口：`P1` 指定现有 `.cst` 工程，输出结构及材料清单。
2. `P2` 指定工程副本及明确的 Solid→材料映射，不覆盖原工程。
3. 生成本地中文报告：读取数量、赋值前后对比、失败原因、保存与回读结果。
4. 真实 CST Windows 验收：原始工程保持不变、目标工程能正常打开、材料赋值正确。

在此之前，**不要把 GitHub CI 通过解释成 P1/P2 真实功能通过**。

## 数据安全

仅在授权 Windows 电脑操作公司工程；不得将原始 CST 文件、材料库、结构清单、内部路径或运行日志上传个人 GitHub。
