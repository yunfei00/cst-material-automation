# 最简单的 CST 零体积清理

**只需要两个路径，不用 P1/P3-A 报告、不用规则文件、不用旧的 cleanup-plan。**

在 Windows 已安装 CST 2025 SP1，进入本代码仓库根目录后直接运行：

```powershell
python clean_cst.py "D:\CST\Original.cst" "D:\CST\Original_Cleaned.cst"
```

或在 Python 中调用：

```python
from clean_cst import clean_cst

result = clean_cst(r"D:\CST\Original.cst", r"D:\CST\Original_Cleaned.cst")
print(result["status"], result["deleted_solid_count"])
```

唯一确定规则是 **Solid.GetVolume 返回数值 `0` 就删除**，与名称、材料、表面积无关，所有正体积 Solid 保留。

**处理流程（一个调用内部自动完成）：** 在临时副本上枚举所有 Solid、调用 CST VBA `Solid.GetVolume`；对完整的零体积清单在**新的输出 .cst 副本**上执行 CST 官方 `Solid.Delete`；保存并重新打开，核对剩余 Solid 名称及材料是否严格等于预期。遇到错误不报告 PASS，原始 `.cst` 不会被覆盖。

自动输出两个文件：

- `Original_Cleaned.cst`：实际删除后的 CST 工程。
- `Original_Cleaned.cleanup.json`：完整删除 Solid ID 清单、删除数量、剩余数量、执行状态及错误诊断。

如果输出工程已存在，程序拒绝覆盖；换一个新输出文件名即可。不需调整输入文件。程序按默认 50 个 Solid 一批输出进度；删除数千个 Solid 时无需手动操作。

**限制：** 本代码已加入 Windows CI 的模拟工程回归，真实 CST 软件的删除行为仍须通过本地实际运行验证。即使删除/回读 PASS，也不能单凭 Solid 数量保证 3D 显示会变流畅，建议用新输出工程直接进行对比。公司工程和 JSON 日志不要上传公共 GitHub。
