# 架构设计（精简版）

目标：在授权 Windows + CST Studio Suite 2025 SP1 中完成离线 Solid 读取、材料候选、工程师确认、材料赋值与新工程保存。

## 已知接口条件

CST 2025 SP1 自带 Python Package，可从安装目录 Library 通过 pip 安装；用户已有 `import cst` 调用代码。具体 API 方法需要依据现有示例实现，不凭空编造。

## 当前模块

- `domain.py`：Solid、Material、BoundingBox 基础数据类型。
- `geometry.py`：几何计算纯函数。
- CST 适配器协议：用于隔离业务逻辑和真实 CST 调用。
- `tests/test_geometry.py`：验证几何计算；CI 运行 Ruff 和非真实 CST 测试。

## 后续最小业务链路

CST Python API → 读取 Solid / Component / Material → 几何与相对位置分析 → 材料候选 → 人工确认 → 测试工程副本赋值 → 另存与回读。

不引入环境扫描、静态验收状态机或不必要的框架。真实 CST 验证与 CI 单元测试分开。

## 安全边界

不得上传公司原始设计文件、材料库、几何数据、内部路径或日志；形状本身不足以唯一确定材料，必须有人工确认步骤。
