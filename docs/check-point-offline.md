# 单个 Solid 的离线点包含判断（STEP + Open CASCADE）

目标：**不运行 CST、不查询工程报告**，输入 CST 导出的 **单个 Solid 的 STEP/STP 文件**和三维点坐标，输出 `INSIDE`、`ON_BOUNDARY`、`OUTSIDE`。

## 1. Windows 一次安装

已经安装 `cadquery-ocp` 的 Python 环境可跳过安装。若需独立 Python 3.11 环境：

```powershell
uv venv .venv-occt --python 3.11
uv pip install --python .\.venv-occt\Scripts\python.exe cadquery-ocp==8.0.1.1.0
```

## 2. 运行一次查询

在仓库根目录执行，替换 STEP 文件路径和三个坐标：

```powershell
& .\.venv-occt\Scripts\python.exe .\check_point.py "D:\CAD\spring.step" 10 20 5
```

也可以从其他 Python 代码直接调用：

```python
from check_point import check_point, load_solid, classify_point

# 单次
print(check_point(r"D:\CAD\spring.step", 10, 20, 5))

# 同一实体查询很多坐标时，只加载一次 STEP：
solid = load_solid(r"D:\CAD\spring.step")
for x, y, z in [(10, 20, 5), (15, 20, 5)]:
    print((x, y, z), classify_point(solid, x, y, z))
```

三种状态：
- `INSIDE`：点在实体内部
- `ON_BOUNDARY`：点位于实体表面或容差范围
- `OUTSIDE`：点在实体外部

默认容差 `1e-6`（以 Open CASCADE 导入后的模型长度单位表示；通常为 mm），命令可通过 `--tolerance 1e-5` 调整。

**注意：**
- 只支持 STEP/STP 精确实体，不是 STL、SAB 或原始 `.cst`。必须先在 CST 中导出**目标一个 Solid**；如果 STEP 内含多个 Solid，程序明确报错，不会误认为是单一实体。
- 点坐标和导出的几何必须使用相同的全局坐标系和单位。CST 导出时若改变了位置/坐标原点，需按导出变换转换点坐标。
- 输入应为有效封闭实体。对于破损 CAD、开放面或恰在边界容差附近的点，须进一步验证。
- 完全本地运行；无需上传公司文件。

## 3. 已完成的最小验证

程序在独立 Python 环境使用 Open CASCADE 将 `10 × 20 × 30` 的精确长方体**导出 STEP 后重新导入**：

| 位置 | 点 | 查询 |
| --- | --- | --- |
| 内部 | `(5,10,15)` | `INSIDE` |
| 外部 | `(15,10,15)` | `OUTSIDE` |
| 表面 | `(0,10,15)` | `ON_BOUNDARY` |

这些是生成的测试几何，非公司真实 CST 工程。真实弹片几何仍需本机验证。
