# 研发路线

- v0.1.0：项目框架、纯 Python 领域模型、几何测试、CI（已合入 main）
- v0.2.0 P0-A：CST 环境只读探测、验证状态与操作手册（本分支）
- v0.2.0 P0-B：在本机确认真实 CST Python/COM/VBA 接口，枚举 Solid/材料
- v0.2.0 P0-C：安全副本赋值、保存、回读与 SHA256 验收
- v0.3.0：几何特征与空间关系计算
- v0.4.0：材料候选排序、人工确认与可审计规则
- v0.5.0：PySide6 中文桌面界面
- v1.0.0：完整离线闭环与 Windows 打包

原则：所有真实 CST 结果必须来自授权 Windows 本机验证。GitHub Actions 仅验证纯 Python 部分。
