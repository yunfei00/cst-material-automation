# CST 2025 SP1 — P1 / P2 真实工程验证说明

> 输入是 **一个已经包含结构和材料定义的 CST 工程文件**。不导入 SAT、不导入材料库。所有操作均在公司授权 Windows 电脑本地执行。

## 开发状态（务必先看）

- 已对照用户上传的 CST Python Libraries HTML 手册，实现 Python API 入口、工程树读取、保存和重新打开。
- P1：`inventory` 命令读取已有工程的 Component/Solid/Material，并尝试查询每个 Solid 当前的材料。
- P2：`assign` 命令根据指定的 Solid → 已有材料名称，复制原工程、修改副本、保存，再打开检查。
- **尚未在真正 CST 2025 SP1 上运行**。Python 文档没有列出三维实体材料查询与赋值命令；使用的 VBA `Solid.GetMaterialNameForShape` / `Solid.ChangeMaterial` 来自 CST VBA 参考资料，需要实机确认。CI 的模拟测试不能代替实机成功。
- 第一次请在**非生产的测试 CST 工程**上运行，以核实树路径形式、VBA 文件查询是否可用、材料名称是否需要完整路径。
- 本程序从 `model3d.get_tree_items()` 中提取 `Components`、`Materials` 的末端路径。若工程树格式不同，会明确报错，不会猜测目标或材料。

## 准备运行（PowerShell，同一代码块）

在项目根目录执行。**使用此前已经安装且 `import cst` 成功的 Python 解释器**；不要求重复安装 CST 或扫描环境。
本地只需拉取这一开发分支，不用切换 main。

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw '获取分支失败' }
git switch feat/cst-p0-probe
if ($LASTEXITCODE -ne 0) { throw '切换分支失败' }
git pull --ff-only origin feat/cst-p0-probe
if ($LASTEXITCODE -ne 0) { throw '更新代码失败' }
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')

# 修改为本机已经存在、包含结构和材料的测试 CST 工程
$source = 'D:\CST_Test\Existing.cst'

# P1：仅读取，原工程不修改；结果在本地 local-data/P1_Report.json
python -m cst_material.cli inventory --source $source --report 'local-data/P1_Report.json'
Get-Content 'local-data/P1_Report.json' -Encoding UTF8

# P2：先从 P1 报告挑选一个准确的 Solid 和已有材料名称
# 将下面三个变量替换为真实值（示例仅演示命令格式）
$solid = 'component1:solid1'
$material = 'Copper'
$dest = 'D:\CST_Test\Output_Assigned.cst'

# 此命令将创建新工程（目标文件必须不存在），并重新打开验证
python -m cst_material.cli assign --source $source --dest $dest --solid $solid --material $material --report 'local-data/P2_Report.json'
Get-Content 'local-data/P2_Report.json' -Encoding UTF8
```

**注意：** 本工程的 `python -m` 会采用当前终端的 Python；若 `import cst` 是安装在另一个虚拟环境，请切换到已验证过的解释器即可。真实 CST 验证不需要使用 `uv run` 创建新的独立 Python 环境。

## JSON 报告和验收条件

- P1：`solid_count`、`material_count`、`solids`、`materials` 和 `tree_items`，供本机核查。查询实体当前材料成功时，各 Solid 的 `material` 非空。查询失败则状态 `PARTIAL`，保留已读取内容和异常原因。
- P2：只有成功保存副本、关闭、重新打开，并且对应 Solid 的材料名与期望一致，才标记 `PASS`。
- 任意错误报告为 `FAIL` 并返回非零退出码。**即使失败，目标副本也可能保留部分变化，不应作为生产工程使用；原工程不被修改。**
- P2 拒绝覆盖存在的目标文件，拒绝将原工程作为目标，拒绝找不到的 Solid 或材料名。
- `--mapping` 可替代 `--solid/--material` 批量赋值，JSON 格式：`{"component1:solid1":"Copper","component1:solid2":"FR4"}`。

## 边界与安全

- P1 使用临时工程副本，以便查询 VBA 不在原工程增加 History；临时副本在结束后清理。
- P2 只在新输出文件上执行材料修改。保存后的回读在重新打开的工程里执行，不将查询宏再次保存。
- 目前工程复制仅覆盖单个 `.cst` 文件。如果原工程还有依赖的外部文件、同名工程结果目录等，需要在本机另行确认关联文件处理。
- 不上传 CST、几何结构、材料清单、公司路径、日志或任何设计数据到个人 GitHub。
- 真实 CST 验证通过之前，不将 P1/P2 标记为已完成真实业务验收。

## 开发者单元测试（不需要 CST）

```powershell
uv sync --group dev
uv run ruff check .
uv run pytest -m 'not cst_real'
```

这组测试由 GitHub CI 自动执行。工程师无需反复执行，不代表 Windows CST SP1 已通过。
