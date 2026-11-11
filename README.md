# CST Material Automation

面向 **CST Studio Suite 2025** 的离线结构识别与材料自动化赋值工具（开发中）。

> **当前状态：v0.2.0 P0-A 环境探测阶段。** 已有 Python 项目框架、几何基础模型、单元测试和只读接口探测；尚未完成真实 CST 连接、材料赋值、保存回读或桌面界面。不要将本仓库视为已可用于生产工程。

## 目标与边界

已有 Creo ASM 装配导入 CST 后，工程中存在大量未赋材料的 Solid。项目目标是通过几何特征与空间关系给出材料候选，由工程师确认后批量赋值，并保存为新的 CST 工程。**形状不能唯一决定材料**，不自动猜测真实材料。

- 运行环境：公司授权的 Windows + CST 2025，完全离线处理工程数据
- Creo View 11.0：仅辅助人工查看，暂不依赖其 API 或导出功能
- 输入：本地 CST 工程及现有材料定义；不要求 BOM/CSV/历史标注
- 输出：材料候选、确认记录、经验证的新 CST 工程（后续阶段实现）
- 严禁向个人 GitHub 上传公司 ASM、PRT、SAT、CST、材料、几何提取数据、截图和日志

## 技术栈

Python 3.11、uv、pytest、Ruff；后续逐步引入 PySide6、NumPy/SciPy、SQLite。CST Python/COM/VBA 真实接口以本机验证为准，不预设未证实的方法。

## 当前开发进度

| 阶段 | 状态 | 交付 |
| --- | --- | --- |
| v0.1.0 项目基础 | 已合入 main | 领域模型、CST 适配器协议、几何纯函数、Windows CI |
| v0.2.0 P0-A | 开发分支，待本机验证 | 只读 CST 环境探测、验收状态模型、测试与文档 |
| v0.2.0 P0-B | 未开始 | 真实 CST 连接、枚举 Solid 和材料 |
| v0.2.0 P0-C | 未开始 | 测试副本赋材料、另存、回读、原件 SHA256 校验 |
| v0.3.0 及以后 | 规划中 | 几何关系、材料匹配、中文 UI、Windows 发布 |

详细路线见 [研发路线图](docs/roadmap.md)，架构见 [架构设计](docs/architecture.md)。

## 需要你做的验证（统一入口）

**所有需要工程师在公司电脑上执行的操作，都维护在文档里，不再依赖聊天中的零散命令。**

**[打开《P0 Windows 验证手册》](docs/p0-windows-verification.md)**

该文档包含：
1. 首次克隆与已有仓库两种执行方式；
2. 一整段可复制的 PowerShell 命令；
3. 预期结果和常见问题；
4. 可安全反馈的字段与后续开发依赖。

目前只需执行 **P0-A 只读探测**。无需准备真实工程，也不会启动或修改 CST。

## 开发者快速检查

```powershell
uv sync --group dev
uv run ruff check .
uv run pytest -m "not cst_real"
uv run python -m cst_material.cli probe
```

GitHub Actions 只检查通用 Python 代码，不代表真实 CST 功能通过。

## 安全

阅读 [安全边界](docs/security.md)。即使仓库为 Private，也不得上传公司专有工程资料。真实 CST 测试只在授权 Windows 本地进行。

## 协作约定

- 开发代码、文档和 CI 由开发侧维护；本地 CST 验证由工程师执行
- 每次需要人工验证时，先更新 `docs/p0-windows-verification.md`，再在 PR 中给出文档链接
- 未经过真实环境验证的功能必须标记为 `NOT_RUN` 或 `BLOCKED`，不得宣称 PASS
- 功能通过 CI 并达到相应阶段验收后，再合入 `main`
