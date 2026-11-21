# 2026-11-20 工具层拆分

## 一句话

把 `csharp/tools/ToolExecutor.cs` 从 **6711 行**按 Python 侧的域拆成 10 个文件。

## 结论（现在的状态）

`ToolExecutor.cs` 只剩 289 行（分发 + `IsActionTool` + `DescribeAction`），其余按域拆出：

| 文件 | 行 | 对应域 |
|---|---:|---|
| `QueryTool.cs` | 956 | 查询：inspect_unit / search_units / query_world |
| `SectQueryTool.cs` | 873 | 查询·宗门 |
| `RelationTool.cs` | 797 | 动作·关系 |
| `ActionTool.cs` | 982 | 动作·互动 |
| `EconomyTool.cs` | 887 | 动作·财物 |
| `MovementTool.cs` | 250 | 动作·位移 |
| 其他 | — | 见详文 |

**切分依据**：跟着 Python `tools/schemas.py` 的两级划分走，不自己发明新维度。

## 关键证据

- 拆分前 6711 行、十个工具、参数解析、好感写入、背包转移、原生动作下发全在一份文件里
- 拆分后单文件最大 982 行
- 转换过程中的"红"要区分是不是切之前就红的（详文记录了这一点）

## 详情

`详文-工具层拆分.md`（同目录）
