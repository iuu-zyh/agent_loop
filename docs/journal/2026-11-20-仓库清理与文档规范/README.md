# 2026-11-20 仓库清理与文档规范

## 一句话

删掉与项目无关的目录（含一份泄漏的课程账号密码）、建立 `docs/journal/` 开发日志层、
写出根目录 `AGENTS.md` 开发须知。

## 结论（现在的状态）

- **`AGENTS.md` 在仓库根**：构建/部署/测试纪律/代码约定/禁止形状/排查顺序
- **`docs/journal/` 三层结构**：`journal/`（过程）→ `archive/`（证据）→ `features/`（现状），
  用法见 `docs/journal/README.md`
- **已删**：`hw1_jupyterhub/`（17M，**含明文密码**）、`backup/`（639M）、
  `generated-images/`（1.9M）、`.workbuddy/`（340K，日志已迁出）、`NUL`、`_unity_probe.log`
- **已归档**：9 篇 2026-09 逐日开发记录 → `docs/archive/journal/`
- **已改名移走**：`tmp_decomp_tool.txt` / `tmp_tools_schema_dump.md` → `reference/`
- **已删死代码**：`pack_release.py` 的 `NEVER_SHIP`（定义了从未被引用）

## 关键证据

**泄漏确认**：`hw1_jupyterhub/creds.json` 里有一组明文课程账号密码，另有 `token.json`。101 个文件已入 git 历史（提交 `d0cb0d6`）。

已验证**不会进发行包**（`pack_release.py` 的 `iter_package_files()` 是白名单；
`export_public.py` 是白名单）—— 但它躺在源码仓库里，仓库一分享就泄漏。

**死代码确认**：`grep -c NEVER_SHIP scripts/pack_release.py` → `1`（只有定义处）。
一份标着"仓库里永不进包的东西（显式列出，不靠 glob 排除——漏一个就是泄漏）"的清单，
一个字节都没拦过。

**日志有救**：删 `.workbuddy/` 前发现 `docs/reference/csharp-iron-laws.md`、`docs/plan-sect.md`、
`scripts/dev/README.md` 三处引用了其中的开发日志，且无其他副本 ——
在 `/mnt/f/_backup/agent_loop_20260915/.workbuddy/memory/` 找到完整副本（9 篇，比原仓库多 2 篇），
先恢复再迁移，引用同步改指向 `docs/archive/journal/`。

## 详情

已归档 → `docs/archive/journal/2026-11-20-仓库清理与文档规范/`
