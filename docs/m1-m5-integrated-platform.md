# CST Solid 自动识别与赋材平台：M1–M5 集成说明

> 2026-11-13 开发分支。自动化/合成 CAD 测试 ≠ 客户公司真实 CST 2025 验收。公司模型、BOM、材料和本地日志不可上传 GitHub。

## A. 当前产品入口：两个文件路径

运行环境：Windows、Python 3.11、CST Studio Suite 2025 SP1；已具备可导入的 CST Python API。终端进入仓库根目录，**以下命令集中复制运行**，只改实际路径：

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/cst-m1-m5-platform
if ($LASTEXITCODE -ne 0) { throw 'git switch 失败' }
git pull --ff-only origin feat/cst-m1-m5-platform
if ($LASTEXITCODE -ne 0) { throw 'git pull 失败' }

# 安装 OCP 精确几何 + PySide6/VTK 三维 GUI 的可选依赖
uv sync --group dev --extra viewer
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }

# 输入旧 .cst，输出新的 .cst；不需要 P1/P3 报告和人工编排
uv run --extra viewer python run_project.py "D:\CST\Original.cst" "D:\CST\Completed.cst"

# 上一步如返回 NEEDS_REVIEW，可手动查看新工程和离线三维模型
uv run --extra viewer python view_solids.py "D:\CST\Completed.geometry"

# 仅打开一个从 CST 导出的真实弹片 STEP，不启动 CST
uv run --extra viewer python view_solids.py "D:\CST\SpringContact.step"
```

Python 用户也可直接调用：

```python
from cst_material import process_project

result = process_project(r"D:\CST\Original.cst", r"D:\CST\Completed.cst")
```

GUI 与处理引擎互相独立；即使不安装 Qt，后台 Python 流程仍可以运行，只需安装 `cad` 可选依赖。

**安装注意**：运行流程仍需要 CST 官方 `cst.interface` 能被当前 Python 解释器导入；若当前虚拟环境尚无此包，请在公司本机使用 CST 2025 的官方 Python 接口安装方式。不能把 GitHub CI 的模拟 CST 环境当作实际 CST 的替代。

## B. 自动产生的文件

| 输出 | 说明 |
| --- | --- |
| `Completed.cst` | 实际保存的新工程副本；原始文件不会覆盖 |
| `Completed.geometry/manifest.json` | CST Solid ID → 精确 STEP 文件名、原始材料和来源 SHA |
| `Completed.geometry/catalog.json` | OCP 离线几何特征、坐标及原 Solid ID |
| `Completed.geometry/steps/*.step` | 精确 CAD 几何，只保存在本机 |
| `Completed.geometry/meshes/*.stl` | 选中 Solid 时懒生成的渲染网格 |
| `Completed.analysis.json` | 删除明细、识别候选、已批准材料更改、未知项、失败原因 |

当成功生成新工程但存在未确认 Solid/材料时，返回 **NEEDS_REVIEW**，命令行退出码 3；不是全自动赋材成功。当材料都已获批准并回读通过才返回 **PASS**。任何执行错误应返回 FAIL，并保留本机错误报告。

## C. 五阶段落地内容及当前验收边界

| 阶段 | 代码 | 已在 CI 验证的边界 | 仍必须在公司实测 |
| --- | --- | --- | --- |
| M1 精确几何 | `occt_geometry.py` / `spatial_query.py` | 合成 STEP 实体、孔洞点包含、包围盒预筛、网格缓存、重复点查询 | 真实弹片 STEP 的几何有效性、单位、速度 |
| M2 3D 查看 | `solid_viewer.py` / `view_solids.py` | 模块的无 GUI 导入与依赖隔离 | GUI 启动、旋转/缩放/高亮、实际帧率与点击结果 |
| M3 导出/映射 | `cst_step_export.py` / `solid_index.py` | 假 CST 环境导出真实合成 STEP；Solid ID 及 SHA 映射 | **CST 2025 STEP.Write VBA 命令真实运行**；原坐标、单位、7k 规模 |
| M4 身份识别 | `solid_recognition.py` | 薄壁弯折候选、人工批准和参考 SHA 完全一致匹配、冲突阻断 | 真实弹片/非弹片标注数据及识别准确率 |
| M5 自动赋材 | `material_matching.py` / `platform_pipeline.py` / `run_project.py` | 完整合成流程、NEEDS_REVIEW、材料审批规则 | 公司工程 R001 删除、真实 CST 材料赋值并回读、模型有效性 |

### 为什么目前还不能说五阶段已经「真实完成」

M3 的单 Solid STEP VBA 导出仍是**有文档依据的实现候选**，没有通过公司实际 CST 2025 SP1 执行；其调用或路径语法可能需要根据真实接口调整。M2 GUI 未在图形 Windows 桌面上人工验收。M4 识别真实弹片的准确率也没有参考样本验证。M5 虽已串接，但仍依赖上述三个条件。

因此当前分支保留 Draft，不能因为 CI 通过就标注“整机识别赋材完成”。

## D. 审批知识库：不凭几何猜材料

可在原 CST 工程同级目录建立 `approved_knowledge.json`。这是工程师可逐步积累的**可选**本地文件，不是运行前必须准备的 P1/P3 报告。内容示例是虚构 ID / 仅展示格式：

```json
{
  "schema_version": 1,
  "confirmed_solids": {
    "component1:spring_001": {
      "approved": true,
      "part_type": "SPRING_CONTACT",
      "material_approved": true,
      "material": "ApprovedMaterialAlreadyInCST"
    }
  },
  "reference_shapes": []
}
```

- `approved` 代表身份已经人工确认。
- `material_approved` 代表该子结构的**具体材料**也已确认。
- `material` 必须与工程中已有的 CST Material 名称一致；否则赋材失败而不是自行新增/猜测。
- 对于相同组件中的多个 Solid，应分别确认子结构材料。
- 没有知识库或证据不足时，仅产生 `CANDIDATE / UNKNOWN`，**不自动赋材**。

## E. PySide6 三维查看器使用

- 左侧列表可搜索完整 Solid ID/Component；选中某一件才打开对应精确 STEP 并生成显示用 STL。
- 中间为 VTK 三维预览，支持鼠标旋转、缩放、平移、点击高亮；重置视角。
- 右侧查看体积、表面积、面/边和尺寸；输入 XYZ 查询 `INSIDE / ON_BOUNDARY / OUTSIDE`，并在三维场景上标点。
- STEP 是精确几何；显示 STL 只做预览，不用它判断真实物体内部。
- 7,000 Solid 场景采用索引+按需渲染，不一次加载 7,000 个 VTK Actor；但性能仍需公司实机测量。

## F. 必须完成的真实工程验收

1. 选择非敏感/非生产工程，确认 CST 的 STEP.Write 能正确导出所选 Solid，文件来源及 ID 一致。
2. 验证导出坐标与 CST 全局坐标和单位一致，点包含使用已知的内部、表面、外部点对照。
3. 确认 PySide6 窗口在公司 Windows 电脑启动并显示弹片，鼠标点击和 XYZ 点标记没有位置偏差。
4. 用至少两类具有已知身份的结构建立弹片与非弹片对照，统计误判、漏判与 UNKNOWN 覆盖率。
5. 用已确认材料的少量 Solid 跑总流程，核对新 CST 工程可打开、删除数量、材料回读和原文件未变化。
6. 通过后扩大至 50→500→7,000+ 个 Solid，分别测量导出、建索引、三维选中、批量点查询速度和内存。

不要提交公司 STEP、.cst、真实 BOM、材料属性及日志到公开仓库。
