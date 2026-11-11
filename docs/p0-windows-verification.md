# P0：CST 2025 Windows 本地验证手册

> 当前只开放 **P0-A：只读环境探测**。不会启动 CST、打开工程、读取公司设计数据或修改文件。请不要将任何公司原始工程、内部路径、截图、材料或日志提交到 GitHub。

## 1. 验证环境

- Windows 10/11，已安装 CST Studio Suite 2025
- Python 3.11 与 uv 可使用（若未安装 uv，先由公司许可的软件源安装）
- 公司网络策略允许安装开源 Python 依赖；如果不允许，请先反馈“依赖下载受限”，不要绕过安全策略
- 只需在普通 PowerShell 执行，无需管理员权限

## 2. 执行命令（整段复制）

以下脚本同时适用于首次下载仓库和已有本地仓库。建议在 **个人允许的本地开发目录** 打开 PowerShell 后执行，不要在含公司工程的目录运行。

```powershell
$ErrorActionPreference = 'Stop'
$repo = 'cst-material-automation'
if (-not (Test-Path $repo)) {
    git clone https://github.com/yunfei00/cst-material-automation.git $repo
    if ($LASTEXITCODE -ne 0) { throw 'git clone 失败' }
}
Set-Location $repo
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/cst-p0-probe
if ($LASTEXITCODE -ne 0) { throw 'git switch 失败；请确认没有未提交的本地改动' }
uv sync --group dev
if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
uv run python -m cst_material.cli probe
if ($LASTEXITCODE -ne 0) { throw '探测程序运行失败' }
uv run ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff 检查失败' }
uv run pytest -m 'not cst_real'
if ($LASTEXITCODE -ne 0) { throw 'pytest 失败' }
```

注意：如果你本地已有同名但不是本项目的目录，请先换到空目录执行。脚本不会清理或覆盖本地改动。

## 3. 预期结果

- `probe` 输出 JSON，`status` 应为 `NOT_RUN`：**这是预期值，不代表脚本失败**，因为真实 CST 连接尚未执行。
- `modules.cst`、`modules.cst.interface`、`modules.win32com`、`modules.win32com.client` 为布尔值。
- `ruff check` 应报告 `All checks passed!`。
- `pytest` 应显示所有已选单元测试通过。

即使 `modules.cst=false` 也不能断定 CST 未安装：CST 的 Python 模块可能位于安装目录或独立 Python 环境。

## 4. 反馈模板（只需填写非敏感信息）

| 项目 | 结果 |
| --- | --- |
| Windows 版本 |  |
| CST 2025 补丁版本 |  |
| Python 版本 |  |
| modules.cst | true / false |
| modules.cst.interface | true / false |
| modules.win32com | true / false |
| modules.win32com.client | true / false |
| Ruff | PASS / FAIL |
| pytest | PASS / FAIL |
| 是否发现 Python、VBA、Macro 相关目录 | 仅填写目录名称，不含公司完整路径 |
| 报错摘要 | 仅脱敏错误类型和信息 |

请在聊天中反馈上述**非敏感结果**即可，不需要粘贴原始工程、完整路径或原始日志。

## 5. 常见问题

**找不到 git 或 uv：** 先确认公司环境允许安装。不要自行绕过公司软件与网络策略。

**依赖下载超时：** 先反馈网络/镜像限制；可以后续增加离线依赖安装方案。

**找不到 CST Python 模块：** 暂时属于待调查现象，不需要手动复制未知 DLL，也不要尝试猜测 COM ProgID。

**git switch 提示本地修改冲突：** 停止执行，保留现场；不要用 `reset --hard` 或 `clean -fd`。

## 6. 后续阶段（当前不要执行）

P0-B：根据本机接口证据，实现 CST 连接、Solid/材料只读枚举。

P0-C：在明确指定的测试工程副本上赋值、另存、关闭回读，并验证原始工程 SHA256 不变。

所有写入功能必须先验证真实接口与失败回滚策略。
