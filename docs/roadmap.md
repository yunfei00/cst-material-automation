# 研发路线

## 已完成

- v0.1.0：基础领域模型、几何函数、单元测试与 CI，已合入 main。
- v0.2.0 开发分支精简：删除多余的环境探测、目录扫描、命令行诊断和静态验收状态模块及对应测试。

## 下一步：真实 CST Python API 最小闭环

已知条件：Windows 已安装 **CST Studio Suite 2025 SP1**；CST Library 中提供可通过 pip 安装的 Python Package；用户已有通过 `import cst` 调用的代码。

1. 复用已有接口用法，连接 CST。
2. 读取测试工程的 Solid、Component 和 Material。
3. 在**副本**中赋材料、另存、重新打开并核对。
4. 完成几何与空间关系分析。
5. 生成材料候选，工程师确认后批量赋值。
6. 后续实现中文桌面界面与 Windows 离线发布。

**不要再要求用户重复验证 CST 是否安装或是否支持 Python。**

GitHub CI 只验证纯 Python 代码；真实 CST 行为只在授权 Windows 本机验证。
