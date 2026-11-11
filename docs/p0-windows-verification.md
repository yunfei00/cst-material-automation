# P0：CST 2025 Windows 本地验证手册

## 目标

在公司授权的 Windows 主机验证 CST 2025 自动化能力。**第一轮只读环境探测，不打开或修改任何工程。**

## 前置条件

- CST Studio Suite 2025 已安装并可正常启动
- Python 3.11 与 uv 已安装；本工具不要求先安装 pywin32
- 使用公司允许的目录；禁止上传工程、几何、材料和日志

## 第一次执行（只读）

在本地克隆仓库后，切换到 `feat/cst-p0-probe` 分支。于仓库根目录运行：

```powershell
uv sync --group dev
uv run python -m cst_material.cli probe
uv run pytest -m "not cst_real"
```

预期：命令输出 JSON，其中 `status` 为 `NOT_RUN`，因为仅检测 Python 模块是否可发现，不等于连接 CST 成功。

`modules.cst` 为 false 不代表 CST 未安装。CST 的 Python 模块可能位于独立安装目录或使用独立解释器。请记录 CST 安装路径和其 Python 相关目录是否存在，但不要上传含内部工程信息的完整路径。

## 请反馈

1. Python 版本、CST 2025 具体补丁版本
2. `modules` 各字段 true/false
3. CST 安装目录内是否有 Python / Library / VBA / Macro 相关文件夹（只描述名称）
4. 测试是否成功，若失败提供去除公司路径和内部信息后的报错

## 第二轮（尚未开放）

待确定官方接口后，再实现受控的打开测试工程副本、列举 Solid/材料、读取几何、赋值、另存与回读。任何修改均需显式指定副本输出路径，原始工程必须保持不变。
