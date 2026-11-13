# CST 2025 SP1 — P1 / P2 真实工程验证说明

> 输入是 **一个已经包含结构和材料定义的 CST 工程文件**。不导入 SAT、不导入材料库。所有操作均在公司授权 Windows 电脑本地执行。

## 开发状态（务必先看）

- 已对照用户上传的 CST Python Libraries HTML 手册和 CST VBA_3D 手册，实现 Python 连接、VBA 实体与材料枚举、保存和重新打开。**由于实际工程触发 `get_tree_items()` 的 UTF-8 解码错误，P1/P2 已绕开该 Python 工程树接口。**
- P1：`inventory` 通过 VBA `Solid.GetNumberOfShapes`、`Solid.GetNameOfShapeFromIndex`、`Solid.GetMaterialNameForShape` 和 `Material.GetNumberOfMaterials`、`Material.GetNameOfMaterialFromIndex` 读取当前工程 Solid、Component 与 Material，不再使用 `get_tree_items()`。
- P2：`assign` 命令根据指定的 Solid → 已有材料名称，复制原工程；**在真正赋值前额外执行 `Solid.GetNameOfShapeFromIndex` 实体清单核对及 `Material.Exists` 材料存在性校验**，校验通过后才修改副本、保存并重新打开检查。
- **官方文档已齐全，且用户已于 2026-11-12 在真实 CST 2025 SP1 工程上确认 P1/P2 通过**。上传的 `Online Help.zip` 中 `mergedProjects/VBA_3D/common_vbasolido/common_vbasolido_solid_object.htm` 正式定义了 `Solid.GetNumberOfShapes`、`GetNameOfShapeFromIndex`、`GetMaterialNameForShape` 和 `ChangeMaterial`；`special_vbalayer/special_vbalayerolayer_object.htm` 定义了 `Material.Exists`。接口名与参数已核实；真实 P1/P2 已通过，但后续每个新增工程类型和新功能仍应进行实机核验，CI 模拟测试不等于完整真实验收。
- 第一次请在**非生产的测试 CST 工程**上运行，以核实树路径形式、VBA 文件查询是否可用、材料名称是否需要完整路径。
- P1 直接从官方 VBA 枚举得到 Solid 与 Material 列表；不会把树叶子节点误认为 Solid。VBA 枚举失败、缺失、数量不符时明确返回 `FAIL`，不写入伪造列表。

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

- P1：`solid_count`、`material_count`、`solids`、`materials`、`vba_solid_count`，并将 `inventory_source` 标为 VBA。Solid 的 `material` 从官方 VBA 获取；枚举失败时记录 `FAIL`、`stage` 和 `traceback`。不再输出 `tree_items`。
- P2：只有成功保存副本、关闭、重新打开，并且对应 Solid 的材料名与期望一致，才标记 `PASS`。
- 任意错误报告为 `FAIL` 并返回非零退出码。**即使失败，目标副本也可能保留部分变化，不应作为生产工程使用；原工程不被修改。**
- P2 拒绝覆盖存在的目标文件，拒绝将原工程作为目标，拒绝找不到的 Solid 或材料名；若官方 VBA 枚举中不存在目标实体、`Material.Exists` 返回 False，也拒绝赋值。
- `--mapping` 可替代 `--solid/--material` 批量赋值，JSON 格式：`{"component1:solid1":"Copper","component1:solid2":"FR4"}`。

## 边界与安全

- P1 使用临时工程副本，以便查询 VBA 不在原工程增加 History；临时副本在结束后清理。
- P2 先在新输出路径的工程副本上执行只读 VBA 预检；检查完成后关闭、**用原工程内容覆盖这个尚未赋材的预检副本并重新打开**，再只写入真实材料赋值命令，避免临时 VBA 文件查询记录进入交付工程。保存后的回读不再次保存查询 History。
- 目前工程复制仅覆盖单个 `.cst` 文件。如果原工程还有依赖的外部文件、同名工程结果目录等，需要在本机另行确认关联文件处理。
- 不上传 CST、几何结构、材料清单、公司路径、日志或任何设计数据到个人 GitHub。
- 用户已确认 P1/P2 在真实 CST 2025 SP1 工程中通过；此结论仅覆盖已测试的工程与操作，不代表所有材料属性和外部依赖都完成验证。


## P2 材料存在性返回 -1：CST VBA Boolean 兼容（2026-11-12）

**实际遇到：** P1 已成功，但 P2 材料预检出现 `材料存在性查询结果异常：'-1'`；在 CLI 捕获异常处显示 `cli.py` 的行号，并不代表异常最初由 CLI 发出。

**原因：** CST VBA `Material.Exists` 返回布尔真值时可能以数字 `-1` 输出。旧版本解析器只接受文本 `true`/`false`，把真实存在的材料误判为异常。

**修复：** 解析器现在兼容 `True`、`False`、`-1`、`0`、`1`。其中 `-1` 和 `1` 表示存在，`0` 表示不存在；其他未知返回值仍阻断赋材，防止误修改。已添加自动测试，但仍需真实工程再次执行 P2 验证完整保存及回读。

**再次执行前：** 上次 P2 失败时可能留下了输出副本文件，程序为保护已有文件，拒绝覆盖任何已存在的 `--dest`。请使用**另一个不存在的新输出文件名**，不要在未检查前删除或覆盖旧副本，也不要修改原始 `.cst` 工程。

## Windows 中文乱码 / UnicodeDecodeError 排查

如果执行 P1 时遇到：

`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb2 in position 154: invalid start byte`

含义是某处字节被当成 UTF-8，但 `0xB2` 不符合该位置的 UTF-8 规则。这**可能**是 GBK/CP936 编码的中文名称（例如汉字“材”的 GBK 编码以 `B2` 开头），不能只凭这一字节就确定是哪一份数据。

**本项目已在本地报告记录失败阶段、traceback 和非法字节。** 打开 `local-data/P1_Report.json`，重点查看：

- `stage`：错误发生在复制工程、连接 CST、打开项目、VBA 枚举 Solid/Material，还是 P2 赋值。
- `encoding_error.invalid_bytes_hex`：精确非法字节（例如 `b2`）；`start` / `end`：错误字节范围。
- `traceback`：确切的 Python 报错调用位置。

**2026-11-12 已修复该调用路径：** P1/P2 改为使用官方 VBA API 枚举 Solid/Material，绕开 `get_tree_items()` 内部的 UTF-8 解码。VBA 输出按 Windows 活动 ANSI 代码页（Python `mbcs`）读取，以兼容常见 GBK/CP936 工程名称。此变更已通过模拟单元测试，仍需用原 CST 工程实测。如果升级后仍出现编码异常，请查看新的 `stage` 判断是 `add_to_history`/VBA 输出，还是其他调用；不要继续改原工程编码。

如果报错在 `读取 Solid→Material 映射`，则 `--mapping` 对应 JSON 文件可能是 GBK/ANSI 编码；请用编辑器将该**JSON 映射文件**另存为 UTF-8，工程文件不需要改动。

**不要**对 `.cst` 工程二进制文件做 `decode('gbk')`、`decode('utf-8')`，也不要使用 `errors='ignore'` 丢弃材料/结构名称。

安全提醒：`traceback` 可能包含公司路径和局部工程信息，只能保存在被 Git 忽略的本地目录。需要共享诊断时只分享脱敏的阶段名、报错类型和非敏感代码行。

## 开发者单元测试（不需要 CST）

```powershell
uv sync --group dev
uv run ruff check .
uv run pytest -m 'not cst_real'
```

这组测试由 GitHub CI 自动执行，只负责模拟回归。P1/P2 的真实 CST SP1 验收已由用户单独完成；P3 等新增能力须重新实机验证。
