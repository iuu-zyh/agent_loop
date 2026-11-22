# 用户数据根（`.agent_loop`）

> 一句话：用户数据住在 `%USERPROFILE%\.agent_loop`，创意工坊整包更新不会覆盖。

## 为什么

创意工坊更新会按作者上传的清单同步整个 MOD 目录。旧布局把 `config.json`、`prompts/`、
`window_layout.json`、`notice_seen.json`、`drama_ledger.jsonl` 都放在 `<Mod根>\ModAssets\`，
每更新一次就全被盖回去：API Key 丢失、改过的提示词回退、自建人设消失。

## 路径与优先级

```
① AGENT_LOOP_DATA（环境变量，显式覆盖）
② %USERPROFILE%\.agent_loop（发行版默认）
③ <Mod根>\ModAssets（旧布局；迁移来源 / 用户目录不可用时兜底）
④ <Mod根>（源码树 / 手工根布局，开发机行为不变）
```

只有「Mod 根下存在 `ModAssets/`」才按发行版处理；源码树没有这个目录，仍用仓库根当数据根。

## 迁移语义（不变量）

- **只补缺失，绝不覆盖。** 目标文件已存在就跳过；`prompts/` 递归补缺失文件。
- 首次启动把旧 `ModAssets` 里的 `config.json`、`prompts/`、三个状态文件搬到用户目录。
- 之后每次启动仍会补 `prompts/` 里缺失的默认文件（作者新增的内置人设能进来），
  用户自建 / 改过的同名文件不动。
- 迁移幂等：第二次跑复制 0 个文件。
- 字节码与 `__pycache__` 不迁。

## 锚数据根的组件

| 组件 | 文件 |
|---|---|
| C# 路径解析 | `csharp/app/ModPaths.cs`（`DataRoot`） |
| 迁移判定核 | `csharp/app/UserDataMigrationCore.cs` |
| 配置端口读取 | `csharp/app/ModConfigFile.cs` |
| 窗口位置 | `csharp/UI/WindowLayoutStore.cs` |
| 公告水位 | `csharp/UI/NoticeStore.cs` |
| 剧情账本 | `csharp/drama/DramaLedger.cs` |
| Python 数据根 | `config/paths.py`（读 `AGENT_LOOP_DATA`） |

Python 侧不自己猜路径：C# 拉起进程时把 `ModPaths.DataRoot` 写进 `AGENT_LOOP_DATA`。

## 不负责的

- 对话记忆仍在 `C:\Users\<用户名>\.sessions\`（本来就不在 MOD 目录里）。
- `portrait_cache/`、`unit_ids.json` 是缓存，留在 Mod 根，丢了会重建。

## 相关测试

- `csharp/tests/UserDataMigrationCoreTests/`：保留 Key / 不覆盖同名 / 补新文件 / 幂等
- `tests/test_user_data_root_contract.py`：优先级顺序、不覆盖判据、C#↔Python 同源
