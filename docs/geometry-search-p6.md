# P6 几何相似检索与弹片候选筛选

前提：已按 [P5 操作手册](geometry-feature-library-p5.md) 完成全部 Solid 的 SQLite 特征提取。P6 **不重新打开 CST、不重新导出 STEP、不修改原工程，也不自动赋材料**。

## 推荐：在三维查看器中直接搜索

打开已完成 P5 特征提取的目录，左侧选择任意 Solid，点击 **以当前 Solid 搜索相似**。左侧列表会只保留匹配的 Solid，并在第二列显示几何相似度；点击任意结果，即可在中间三维视图中查看。点击 **筛选弹片候选** 可以直接过滤弹片形态候选；点击 **显示全部** 恢复完整列表。顶部关键字输入框可以在当前结果中进一步过滤。

这三个按钮仅查询本地 SQLite 数据库，**不重新导出 STEP，不自动确认零件身份或赋材料**。相似检索结果按照相似度从高到低排列，数值显示在第二列；弹片候选按体积从小到大排列。点击“显示全部”恢复原始 Solid 列表顺序。

## 使用方法（Windows PowerShell）

在仓库根目录运行，将目录替换为你的实际路径：

```powershell
git switch main
git pull --ff-only origin main
$GeometryDir = "D:\CST\Original.solid_export_diagnostic"

# 方法一：按已知 Solid 的完整原始名称，查找几何最相似的 30 个 Solid
uv run --extra viewer python -m cst_material.geometry_search "$GeometryDir" --reference "Component:Solid" --limit 30 --output "$GeometryDir\similarity_results.json"

# 方法二：直接筛选全部 Solid 中符合薄壁、折弯等几何启发式规则的弹片候选
uv run --extra viewer python -m cst_material.geometry_search "$GeometryDir" --spring-candidates --limit 100 --output "$GeometryDir\spring_candidates.json"

# 查看候选对应的三维形状
uv run --extra viewer python view_solids.py "$GeometryDir"
```

`--reference` 必须填写实际存在于 `manifest.json` 中的完整 `Component:Solid` 名称，不能使用示例字符串。输出 JSON 中包含原始 Solid ID、STEP 路径、形态提示、相似度、尺寸、体积和判断依据。

## 识别规则与限制

- 相似度由尺寸、体积、面积、面数和曲面类型构成，数值越接近 1 表示几何特征越相似；**不是零件身份置信度**。
- 弹片候选规则使用长宽厚比例、非平面曲面数量和面数，能初筛典型薄壁折弯件；会有误报和漏报，尤其是斜置零件和复杂装配体。
- 所有候选都标记 `NEEDS_REVIEW`。必须由工程师结合三维形状及装配关系确认，才能纳入样本库或材料映射。
- P6 当前提供命令行检索与 JSON 报告。查看器可按 Solid ID 搜索并查看候选；批量候选列表的图形化交互属于下一轮增强。

## 验收建议

选取一个人工已知的弹片 Solid，使用 `--reference` 检索，检查前 30 个结果的三维形状；然后使用 `--spring-candidates` 检查规则筛选的误报、漏报。请记录人工确认的结果，后续才能客观评估算法。
