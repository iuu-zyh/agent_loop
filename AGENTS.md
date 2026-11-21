# AGENTS.md

> 给**接手这个仓库的人（或 AI）**看的入口页。三分钟读完，知道该去哪。

---

## 先读哪个

| 你想干什么 | 去读 |
|---|---|
| **改代码 / 加功能** | **[`docs/DEV_RULES.md`](docs/DEV_RULES.md)** ← 开发规范唯一来源 |
| 这项目是干什么的 | [`README.md`](README.md) |
| 文档怎么分的 | [`docs/README.md`](docs/README.md) |
| 某个功能现在怎么工作 | [`docs/features/`](docs/features/) |
| 某个字段 / 表 / 函数是什么 | [`docs/reference/`](docs/reference/) |
| 某个结论是怎么来的 | [`docs/archive/`](docs/archive/) |
| 跑测试、挑子集 | [`tests/README.md`](tests/README.md) |

---

## 六条不能忘的

1. **开工先建 `docs/journal/YYYY-MM-DD-功能名/`**，边做边记。
   做完详文归档，子文件夹只留 `README.md`。规范见 [`DEV_RULES.md`](docs/DEV_RULES.md) §一。

2. **全量测试只在收尾时跑**（约 2.8 分钟）。平时只跑受影响的部分：
   `python3 scripts/dev/run_tests_for.py --changed`

3. **收尾有一张 16 项的强制清单**，见 [`DEV_RULES.md`](docs/DEV_RULES.md) §十二。
   缺一项不算做完了 —— 尤其是「没验证的必须说明」那一条。

4. **改了 C# 要拷到三处并重启游戏**，否则改了没反应：
   游戏 `ModCode/dll/`、编辑器 `ModCode/dll/`、编辑器 `ModMain/bin/Release/`。
   三处 md5 必须一致。见 [`DEV_RULES.md`](docs/DEV_RULES.md) §四。

   **部署命令跑完就结束，不要再验证。** 不要解包 exe 查 code object、不要
   `strings` 查 DLL 符号、不要重跑 `--check` 确认、不要抽查时间戳。
   部署的判据就是 `deploy_runtime.py` 的退出码 + md5 输出；生效的判据是实机跑一次。
   "部署后再查一遍文件"没有信息量，只烧 token。见 [`DEV_RULES.md`](docs/DEV_RULES.md) §四
   「部署完就结束」。

5. **新写的注释只写「为什么」，不写「发生了什么」**。真机日志、被推翻的假设、
   改动沿革 —— 进 `docs/archive/`，代码里留一行指针。**存量注释不清**，
   也别顺手整理旁边的。见 [`DEV_RULES.md`](docs/DEV_RULES.md) §五。

6. **不要碰未跟踪的 `csharp/**` 新文件** —— 可能是别人正在并行开发。

---

## 一句话记住项目分工

**Python 是大脑**（决定说什么、做什么），**C# 是双手**（真正碰游戏 API）。
任何触及游戏数据的调用必须在主线程。

---

## 文档命名一句话

`archive/` 和 `journal/` 的子条目**一律日期开头**。
一件事写一份 → `<日期>-<事>.md`；写多份 → `<日期>-<事>/` 目录。
不用 `01-` `02-` 编号，除非文件之间真有阅读顺序。

细节、代码约定、禁止出现的形状、排查顺序、收尾清单 —— 全在
**[`docs/DEV_RULES.md`](docs/DEV_RULES.md)**。
