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

## P3-C（用户已确认空间读取与文件锁诊断通过）

- **P3-C `spatial`**：经 CST 官方 OLE `CSTStudio.Application.2025`、`Project.RunScript` 的命令宏路径尝试调用 `Solid.GetLooseBoundingBoxOfShape`，避免 History 限制。提供**非紧包围盒**、轴向范围、包围盒中点与包围盒邻近候选；不表示真实质心、厚度或真实实体接触。
- **状态：** 用户已确认 P3-C 实际运行通过；曾遇到 `result/dp.parmap` 的 WinError32 临时文件占用，已实现 Windows Restart Manager 只读根因诊断并完成本机回归。仍不能据此证明所有文件锁问题在所有工程中彻底消失。
- [P3-C 空间特征使用与验收](p3-spatial-probe-verification.md)。

## P3-D（离线邻近分析：用户反馈运行已正常）

- **P3-D `spatial-offline`**：读取以前的 P3-C 报告，不启动 CST；NumPy 分批处理非紧 AABB 距离下界，避免构建完整 N×N 距离矩阵，保留最近 5 个候选与稳定重叠排序。
- **测试与反馈：** 已通过 7,311 个合成 Solid 的规模/正确性测试，用户反馈修正 PowerShell `$input`→`$inputFile` 后命令正常执行。具体真实工程计数摘要未提供，保持此验证边界。
- **说明：** [P3-D 7,311 Solid 离线分析](p3d-large-neighbors-verification.md)。

## P4-A（已开发：离线结构候选，待真实报告验收）

- **命令：** `structure-preview`，读取 P3-A 与 P3-C 的 JSON（可附 P3-D 最近邻），按 Solid 标识、源路径、单位和已有材料进行一致性核对。
- **结果：** 体积/表面积、非紧 AABB 三轴范围、邻近候选与启发式形态类别（扁平盒、细长盒、三轴相近盒及其他）；输出判断依据，所有 Solid 的材料推荐均为 `null`，需要人工确认。
- **自动测试：** 7,311 个合成 Solid 完整处理；输入损坏、跨工程混用、邻近距离不一致全部拒绝。公司真实 P4-A 报告尚未验收。
- [P4-A 结构识别中文验收](p4a-structure-preview-verification.md)。

## P4-C：结构导入后的辅助件清理预览（代码和 CI 已完成，真实审查待执行）

- **只读命令：** `cleanup-plan`：P1 inventory + 可选 P3-A geometry，输出名称复核提示、体积为零的对象统计、人工显式选定的删除候选、必须保留对象及变体组规则。不连接 CST，不执行删除。
- **用户确认规则 R001（2026-11-12）：** P3-A 读取的 **`volume_project_unit3 == 0` 全部列入删除列表**，不区分 A>0/A=0、材料与名称。非零体积 Solid 不因该规则删除。与 `protected_ids` / 变体 keep 冲突时拒绝模糊执行。
- **副本实删入口：** `cleanup-apply` 使用官方 CST 2025 `Solid.Delete`，仅对新 `.cst` 副本批量执行，重新打开并核对完整剩余 Solid/材料；CI 只模拟接口，真实 CST 删除尚待验证。
- **后续实删门槛：** 明确仿真目标与工程师认可的完整 Solid ID/变体 keeper；核实 CST 官方删除接口和端口/激励/边界依赖；先在少量非生产 CST 副本实机验证，再做保存/回读与仿真有效性检查。
- **说明：** [P4-C 结构清理复核与零体积分类](p4c-cleanup-review.md)。

## 后续阶段

1. **P4-A 真实报告验收**：核对 Solid 数量、分类分布、异常数据与来源一致性。
2. **P3-B 扩展**：逐步建立已确认的材料物性知识，不凭文件名猜材料属性。
3. **P4 — 智能材料候选与人工确认**：将真实结构特征与已确认材料知识关联，低置信度交工程师；仍不自动修改原工程。
4. **P5 — Windows 中文界面**：操作、预览、批量处理与离线发布。

外部 MTD 的**批量加载到 CST 工程**当前暂缓；P2 继续仅赋工程里已经存在的材料。公司内部 CST/MTD/报告不上传到 GitHub，P4-A 分支不会自动合并到 main。
