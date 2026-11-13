# 结构识别 V1：Windows 离线验收步骤与反馈记录

> 更新：2026-11-13。仅识别 Solid 的零件类别，不根据形状猜测材料，不执行材料赋值。**本阶段功能需要在公司 Windows 实机验收，CI 通过不等于实机验收通过。**

## 一、当前实现边界

- `src/cst_material/structure_samples.py`：从精确 OCP 几何统计提取尺寸比例、体积、面积、填充率、面/边数、曲面类型，保存人工确认的结构样本，并检索相似的前五个已确认样本。
- `src/cst_material/solid_viewer.py`：右侧显示相似样本，填写零件类别后点击 **确认类别并保存结构样本**。样本库路径为 `%USERPROFILE%\.cst_material_automation\structure_samples_v1.json`。
- 当前相似度为可解释的基础几何特征分数，不是识别正确率，也不代表已经能稳定区分不同形态的弹片。**暂未实现**局部折弯/厚度识别、AI 模型训练、跨结构家族泛化。
- 原有 **保存本机确认结果** 是旧流程的独立按钮；本阶段验收只使用新的结构样本按钮，不勾选任何材料选项。
- 样本只保存几何摘要和源 STEP SHA256，不复制原始 STEP 或扫描 CSV；仅在本机保存，不上传公司数据。

## 二、准备与启动（PowerShell 一次复制）

将 `D:\CST\SpringA.step` 换成真实本机文件路径。请使用两个**不同结构**但都已经人工确认是弹片的 STEP，以及一个容易混淆的非弹片 STEP。先用 A 进行以下操作。

```powershell
$ErrorActionPreference = 'Stop'
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch 失败' }
git switch feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw '切换分支失败' }
git pull --ff-only origin feat/solid-structure-recognition-v1
if ($LASTEXITCODE -ne 0) { throw 'git pull 失败' }
uv sync --group dev --extra viewer
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
uv run --group dev --extra viewer python -m pytest tests/test_structure_samples.py -q
if ($LASTEXITCODE -ne 0) { throw '样本库测试失败' }
uv run --extra viewer python view_solids.py "D:\CST\SpringA.step"
```

也可以把最后一行的路径替换为之前生成的 `Completed.geometry` 目录，按左侧列表选择 Solid。不要用公司原始数据创建公开 issue。

## 三、人工验收步骤与预期结果

| 编号 | 操作 | 预期 |
| --- | --- | --- |
| V1-01 | 打开弹片 A，观察右侧结构相似样本 | 首次没有样本时显示“暂无已确认结构样本” |
| V1-02 | 在零件类别输入框填写 `SPRING_CONTACT`，点击 **确认类别并保存结构样本** | 状态栏显示已保存；本地生成 `structure_samples_v1.json`；无材料赋值 |
| V1-03 | 重新打开弹片 A | 相似列表出现 `SPRING_CONTACT` 样本，匹配分数应接近 1.0 |
| V1-04 | 打开不同结构的弹片 B | 显示候选及相似度；**不要求**分数必然高，也不应自动标记为人工已确认 |
| V1-05 | 人工确认 B 是弹片并保存 | 样本库新增不同 `sample_id` 的 `SPRING_CONTACT` 样本 |
| V1-06 | 打开非弹片 C，填写其真实类别并保存 | 样本库包含不同类别；观察它是否被弹片样本误排到首位 |
| V1-07 | 对同一个 Solid 再次点击保存 | 提示“样本 ID 已存在”，不会覆盖原确认结果 |
| V1-08 | 关闭查看器重新启动 | 结构样本库仍在，能检索到历史样本 |
| V1-09 | 检查原 CST/STEP 文件 | 原始文件未被修改；本阶段不执行材料赋值 |

**重要限制**：目前没有将相似度转换成可靠的类别置信度；不要根据 0.85、0.9 等分数直接自动确认身份。对于相似度不佳的不同弹片，记录结果供下一阶段优化多尺度特征。

## 四、反馈模板（不含保密数据）

直接复制下面内容反馈即可，无需上传 STEP、CST 或完整样本库：

```text
分支/提交：
系统：Windows 版本 / Python 3.11
V1-01~V1-09：通过/失败（逐项）
弹片 A 与自身相似度：
弹片 B 与弹片 A 相似度：
非弹片 C 的最高匹配类别及相似度：
异常提示（可脱敏）：
希望改进的识别情况：
```

## 五、CI 验收记录

- 2026-11-13：`c66c2527` 已查询到 GitHub Actions **success**；此前 `dfa61919` 失败已通过导入格式修复。
- 本文档提交后，必须等待**文档所在最新提交**对应的全部 CI 检查结束并确认成功，失败则继续修复；不得以旧提交的 CI 代替。
- 真实 Windows 结构识别 V1：**待用户按本文件反馈**。

## 六、开发交付规则（后续每次均执行）

1. 每次代码/文档修改后提交到当前功能分支。
2. 检查该提交触发的全部 GitHub Actions 运行；全部完成且成功才算 CI 验收通过。
3. 失败时读取失败作业日志、修复并重新提交，重复上述过程直到通过。
4. 同步更新相应 `docs/*-verification.md`：安装命令、实机操作、预期结果、限制、失败反馈模板、提交 SHA 和 CI 状态。
5. 用户确认真实环境验收后才准备合入 `main`，合并后再检查 `main` CI。
