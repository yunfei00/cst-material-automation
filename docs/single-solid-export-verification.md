# 单 Solid STEP 导出快速验收（2026-11-13）

## 目的

只验证一个指定 Solid 的 STEP 导出。不执行 `run_project.py`，不进行 R001 清理、几何索引、三维渲染、结构识别或材料赋值。程序将源 CST 复制到临时目录，**只在临时副本上**打开 CST，读取实体清单并对指定 Solid 调用一次 `STEP.Write`。成功/失败均写本地日志，原始 CST 不覆盖。

## 执行命令（Windows PowerShell）

在仓库根目录，修改最后一行的 **三个参数**：现有 CST 工程、完整 `Component:Solid` 名称、尚不存在的输出目录。

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch 失败' }
git switch feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw '切换分支失败' }
git pull --ff-only origin feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw '拉取失败' }
uv sync --group dev
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
uv run --group dev python -m pytest tests/test_single_solid_export.py tests/test_step_export_vba.py -q
if ($LASTEXITCODE -ne 0) { throw '回归测试失败' }
uv run python export_one_solid.py "D:\CST\Original.cst" "component1:solid1" "D:\CST\OneSolidTest01"
```

若失败，**不要删除 `OneSolidTest01`**；下一次测试换 `OneSolidTest02` 这样的新目录。不会扫描并导出其他 Solid。

## 日志说明

`OneSolidTest01/diagnostic.json` 记录：

- `COPY_START/COPY_DONE`：复制源工程到临时副本。
- `CST_CONNECT_OK/CST_OPEN_OK`：连接 CST 并打开副本。
- `CATALOG_OK`：读取 Solid 清单，报告总数和指定名称是否精确匹配。
- `SOLID_NOT_FOUND`：未找到指定 Solid；记录大小写近似候选，但不会替换用户输入。
- `EXPORT_START`：完整 VBA 内容、目标 STEP 路径。
- `EXPORT_OK`：文件实际存在、文件大小正常。
- `ERROR`：原始异常类型、错误文本和 Python traceback。
- `CST_PROJECT_CLOSED/CST_ENV_CLOSED`：清理连接。

注意：日志含本地路径和实体名称，只保存在公司电脑；对外反馈需脱敏。

## 验收项目

| 编号 | 操作 | 预期 |
| --- | --- | --- |
| S1 | 运行测试命令 | Python 单元测试全部通过 |
| S2 | 指定一个已知存在的 `Component:Solid` | `CATALOG_OK.exact_match=true` |
| S3 | 运行单件导出 | 成功时 `EXPORT_OK`，生成 `selected_solid.step` |
| S4 | 如果失败 | `diagnostic.json` 保留失败阶段、完整 VBA 和原始异常 |
| S5 | 换另一个 Solid 及新的输出目录重试 | 每次只导出指定的一件，不运行其他业务流程 |
| S6 | 检查原 CST | 原工程文件未被修改 |

## 反馈格式

```text
提交版本：
Solid 名称形式（脱敏，如 main:spring_01）：
CATALOG_OK：solid_count=，exact_match=
最终阶段：
原始错误：
是否生成 selected_solid.step：
STEP 文件大小：
```

## 验收状态

- GitHub CI：本次提交后必须等待全部检查完成且通过；失败继续修复。
- CST 2025 SP1 真实导出：待公司本机实测。**CI 成功不等于 STEP.Write 实机成功。**
