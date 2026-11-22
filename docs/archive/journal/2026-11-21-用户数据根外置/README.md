# 用户数据根外置（方案 A）

## 要解决的问题

创意工坊整包更新会同步 MOD 目录。当前 `config.json` / `prompts/` / 窗口位置 / 公告水位
/ 剧情账本都在 `<Mod根>\ModAssets\` 里，更新一次就全被盖回去：

- 用户填的 API Key 丢失
- 改过的提示词、自建人设丢失
- 窗口位置、公告已读状态、剧情账本可能被清

对话记忆在 `C:\Users\<用户名>\.sessions\`，不受影响。

## 定案

用户数据根移到：

```
C:\Users\<用户名>\.agent_loop\
  config.json
  prompts\            ← 用户副本，自建人设也在这里
  logs\
  window_layout.json
  notice_seen.json
  drama_ledger.jsonl
```

解析优先级：

```
AGENT_LOOP_DATA（显式覆盖）
  > %USERPROFILE%\.agent_loop（发行版新默认）
  > <Mod根>\ModAssets（旧布局 / 迁移来源）
  > <Mod根>（源码树 / 手工根布局，保持原行为）
```

## 迁移语义（关键）

**只补缺失，绝不覆盖。**

- `config.json`：用户目录没有才从 `ModAssets` 复制 → 保住 Key
- `prompts/`：用户目录没有就整棵复制；已存在则**逐文件补缺失**
  - 新版本新增的内置人设能进来
  - 用户自建 / 改过的同名文件不会被覆盖
- `window_layout.json` / `notice_seen.json` / `drama_ledger.jsonl`：缺才复制
- 迁移可重复执行，第二次复制 0 个文件

## 改动点

| 文件 | 改什么 |
|---|---|
| `csharp/app/UserDataMigrationCore.cs` | 新增：纯 System.IO、零 Unity 的迁移判定核 |
| `csharp/app/ModPaths.cs` | `DataRoot` 增加用户目录优先级 + 迁移接线 + 来源日志 |
| `csharp/tests/UserDataMigrationCoreTests/` | 新增：迁移语义单测 |
| `tests/test_user_data_root_contract.py` | 新增：源码级契约断言 |
| `README.md` / `安装说明.txt` / `config/paths.py` 注释 | 新路径与卸载说明 |

Python 侧不用改：C# 通过 `AGENT_LOOP_DATA` 下发新数据根，`config/paths.py` 已经优先读它。

## 验证

- C#：新单测覆盖「保留 Key / 不覆盖同名 / 补新文件 / 幂等」
- Python：契约测试锁住优先级与"不覆盖"判据
- 实机：旧版填 Key + 改一个提示词 + 新建一个人设 → 升级 → 三个都还在
