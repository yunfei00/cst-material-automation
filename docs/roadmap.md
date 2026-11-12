# CST 材料自动化研发路线

## 输入约束（已确认）

- 只有**一个已经包含三维结构与材料定义的 CST 工程**（`.cst`）。
- 不导入 SAT / ACIS，不导入外部材料库，不新建空工程。
- Windows 已安装 CST Studio Suite 2025 SP1，Python `import cst` 已验证；不再重复环境探测。

## P1/P2（2026-11-12 真实 CST 工程验收通过）

- **P1 — `inventory`**：绕过 Windows 中文工程树中的 `get_tree_items()` UTF-8 解码问题，使用 CST 2025 VBA 直接枚举 Solid/Material 和当前材料关联。
- **P2 — `assign`**：确认 Solid 和材料存在（兼容 `Material.Exists` 返回 `-1`），对工程副本赋材、保存并重新打开核对。
- **证据：** 用户确认 P1 和 P2 在公司 CST 2025 SP1 实机 PASS；模拟 CI 仅用于回归。
- 运行流程见 [P1/P2 中文说明](p1-p2-verification.md)。

## P3（本分支：只读最小闭环，2026-11-12 用户确认实际执行通过）

- **P3-A `geometry`**：提取体积、表面积、长度单位及 SI 换算和紧致度；只用已在官方帮助中查到的 `Solid.GetVolume` / `Solid.GetArea` / `Units.GetUnit` / `Units.GetGeometryUnitToSI`。
- **限制：** 官方明确 `Solid.GetLooseBoundingBoxOfShape` 不能在 History/结构宏执行；当前 `add_to_history` 不能用于该接口。因此暂不实现包围盒、空间坐标、尺寸和邻接关系，不伪造特征。
- **P3-B `scan-mtd`**：外部 MTD 材料库只读文件级索引（路径、候选名称、大小、时间、SHA256），不解析物性，不批量导入 CST。
- [P3 使用与验收说明](p3-geometry-mtd-verification.md)。**用户报告 P3-A、P3-B 均通过实际运行**；但现有几何输出只覆盖体积/表面积，MTD 扫描只覆盖文件元数据，不代表包围盒、空间特征、MTD 物性读取已通过。

## P3-C（独立实验分支，实机待验证）

- **P3-C `spatial`**：经 CST 官方 OLE `CSTStudio.Application.2025`、`Project.RunScript` 的命令宏路径尝试调用 `Solid.GetLooseBoundingBoxOfShape`，避免 History 限制。提供**非紧包围盒**、轴向范围、包围盒中点与包围盒邻近候选；不表示真实质心、厚度或真实实体接触。
- **状态：** 实现与模拟回归测试已新增，**尚未在 CST 2025 SP1 真实工程通过**；未知的 COM/VBA 返回值必须明确失败，不回退到受限 History 接口。
- [P3-C 空间特征使用与验收](p3-spatial-probe-verification.md)。

## 后续阶段

1. **P3-C 真实验收与扩展**：验证非 History 调用成功、原工程不变、几何数据精度边界，再研究更紧的包围盒与真实物理邻接。
2. **P3-B 扩展**：逐步建立已确认的材料物性知识，不凭文件名猜材料属性。
3. **P4 — 智能材料候选与人工确认**：将真实结构特征与已确认材料知识关联，低置信度交工程师；仍不自动修改原工程。
4. **P5 — Windows 中文界面**：操作、预览、批量处理与离线发布。

外部 MTD 的**批量加载到 CST 工程**当前暂缓；P2 继续仅赋工程里已经存在的材料。公司内部 CST/MTD/报告不上传到 GitHub，P3-C 分支不会自动合并到 main。
