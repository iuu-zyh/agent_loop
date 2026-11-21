"""拉起失败必须**上报到界面**，不许再静默（09-20）。

事故背景
------------------------------------------------------------------------------------
别人反馈："改端口之后没重连"，截图里是**未连接 Python + 配置值全空**。
排查时发现真正致命的不是那一次断连，而是**整套失败通道是静默的**：

  · Python 起不来时，失败原因（缺 exe / 杀软拦截 / server.py 找不到 / 端口被占）
    全部只走 `ModMain.P()` 进 Player.log —— **界面上一个字都没有**；
  · 玩家打开配置面板，看到的是一句"读取配置失败: 未连接 Python"。
    他本来就知道没连上，他想知道的是**为什么**、**该找谁**、**日志在哪**；
  · 更糟：`StatusLabel` 是**复用位**（SetStatus 直接赋值），玩家一慌就会乱点
    （测试连接 / 切 Tab / 按保存），**每一次都把唯一的线索顶掉** → 又回到完全静默。

本文件钉死修复后的四条契约（任缺一条，故障都会重新变得不可诊断）
------------------------------------------------------------------------------------
  ① `Launcher` 的**每条**失败路径都要 `RecordFailure(...)`，不许只 P() 打日志；
  ② 失败摘要要落成 `_diag_last_failure.txt`（用户报障时自带的现场）；
  ③ 横幅必须**等世界就绪**才弹（`g.root` 未建时弹了看不见），且**每次失败都弹**；
  ④ 配置面板的诊断是**粘性第二行**：普通操作反馈不许把它顶掉（方案 B）。

容量约束（实机量 prefab 得到，是设计的一部分而不是随手写的数字）
------------------------------------------------------------------------------------
  BG 760 宽 → Status 736px；字号 14 → 约 50 字/行；高度 32 且 VerticalOverflow=Overflow
  → **恰好两行**。所以"第一行反馈 + 第二行诊断"正好用满，绝不能有第三行（会被静默裁掉）。
  横幅侧更紧：`UnreadBanner.ShowSystem` 的 budget = 34 字，超出直接截断加"…"。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "csharp/bridge/Launcher.cs"
BRAINLINK = ROOT / "csharp/bridge/BrainLink.cs"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"
BANNER = ROOT / "csharp/UI/UnreadBanner.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_every_launcher_failure_path_records_a_reportable_reason():
    """① 四条失败路径（无入口 / 无 server.py / Start 返回 null / Start 抛异常）
    每一条都必须调 RecordFailure —— 只 P() 打日志 = 玩家看不到。"""
    src = _read(LAUNCHER)
    # 失败路径的判据：这些 return 就是"拉起失败"的出口
    failure_returns = [
        'return "未找到可用的 Python 入口（如需对话请手动启动）";',
        'return "未找到 server.py（如需对话请手动启动）";',
        'return "Process.Start 返回 null";',
    ]
    for ret in failure_returns:
        i = src.index(ret)
        # 往上看 12 行内必须有 RecordFailure（各路径的日志行数不同，取一个够宽的窗口）
        window = src[max(0, i - 900):i]
        assert "RecordFailure(" in window, (
            f"这条失败路径没有调 RecordFailure，玩家在界面上看不到原因：\n  {ret}"
        )
    # 异常分支（catch 里 return）单独查
    m = re.search(r'catch \(Exception e\)\s*\{\s*ModMain\._pythonStarted = false;(.*?)\n            \}',
                  src, re.S)
    assert m and "RecordFailure(" in m.group(1), "Process.Start 抛异常时没有 RecordFailure"


def test_failure_summary_reaches_a_diag_file():
    """② 失败摘要要落成 _diag_last_failure.txt（用户打包 mod 目录报障时自带现场）。"""
    src = _read(LAUNCHER)
    assert "RecordFailure" in src and "WriteFailureDiag" in src, "没有写诊断文件的实现"
    assert "_diag_last_failure.txt" in src, "诊断文件名不对（约定 _diag_*.txt，与 DiagSwitches 同款）"
    assert "ModPaths.DiagDir" in src, "诊断文件没有落在 ModPaths.DiagDir（应与 _diag_*.txt 同目录）"
    # 写文件必须整段吞异常：磁盘满 / 只读目录都不该影响拉起主流程
    m = re.search(r'private static void WriteFailureDiag\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert m, "WriteFailureDiag 没找到"
    assert "catch" in m.group(1), "WriteFailureDiag 没有 try/catch —— 写不进去会拖垮主流程"
    # 报告里要带上两份日志的路径：玩家按图索骥，不用猜
    body = m.group(1)
    assert "agent_loop.log" in body, "诊断文件里没给 Python 日志路径"
    assert "Player.log" in body, "诊断文件里没给 Player.log 路径"


def test_banner_waits_for_world_ready_and_fires_every_failure():
    """③ 横幅：世界就绪才弹（否则看不见）+ 每次失败都弹（用户明确要求）。"""
    src = _read(BRAINLINK)
    m = re.search(r'private static void TickFailureBanner\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert m, "TickFailureBanner 没找到"
    body = m.group(1)

    # 世界就绪闸：复用 AbPanelProber.CanCreateNow（查 MapMain 是否已建）
    assert "CanCreateNow()" in body, (
        "横幅没有等「世界就绪」—— g.root 未建时弹了玩家看不见（要求：进世界后才显示）"
    )
    assert "return" in body.split("CanCreateNow()")[1][:200], \
        "世界没就绪时没有 return（会带着未就绪的状态继续往下弹）"

    # 每次失败都弹：用水位线（FailureCount）而不是布尔闩锁
    assert "Launcher.FailureCount" in body, "横幅没有跟随失败计数（要求：每次失败都要弹）"
    assert re.search(r'_failNotifiedCount = fails', body), \
        "没有推进水位线 —— 要么重复弹、要么只弹第一次"

    # 必须挂超时：世界迟迟不就绪时别把陈年旧账攒着，进世界后一次补弹
    assert "FailureBannerMaxWait" in body, "没有超时放弃逻辑（主菜单挂机后进世界会补弹一堆旧账）"

    # OnUpdate 每轮要真的驱动它
    onupd = re.search(r'public static void OnUpdate\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert onupd and "TickFailureBanner()" in onupd.group(1), \
        "OnUpdate 没有调 TickFailureBanner（横幅永远不会弹）"


def test_banner_text_fits_the_34_char_budget():
    """横幅预算实测 34 字（UnreadBanner.ShowSystem 的 const budget）——文案超了会被静默截断。

    这条是"防蠢"而不是防 bug：如果谁往横幅里塞完整路径清单（几百字），
    玩家只会看到前半句 + "…"，却以为那就是全部。
    """
    banner = _read(BANNER)
    m = re.search(r'const int budget = (\d+)', banner)
    assert m, "UnreadBanner.ShowSystem 的 budget 常量没找到（改名了？）"
    budget = int(m.group(1))
    assert budget <= 40, f"横幅预算变成 {budget} 字了，文案得跟着重新审一遍"

    src = _read(LAUNCHER)
    # 每处 RecordFailure 的 reason+hint 拼起来不该超过横幅预算
    for mm in re.finditer(r'RecordFailure\(\s*"([^"]+)"\s*,\s*"([^"]+)"', src):
        reason, hint = mm.group(1), mm.group(2)
        total = len(reason) + 1 + len(hint)      # +1 = 拼接用的"；"
        assert total <= budget, (
            f"RecordFailure 文案 {total} 字 > 横幅预算 {budget}：会被截断成半句\n"
            f"    reason={reason!r}\n    hint={hint!r}\n"
            f"  请精简，或把细节留给 _diag_last_failure.txt"
        )


def test_config_panel_diagnosis_is_sticky_second_line():
    """④ 方案 B：诊断常驻第二行，普通操作反馈（第一行）不许把它顶掉。"""
    src = _read(PRESENTER)

    # 失败分支要真的去查诊断
    assert "ShowConnectionDiagnosis()" in src, "读取配置失败时没有打诊断"
    n_calls = len(re.findall(r'ShowConnectionDiagnosis\(\)', src))
    assert n_calls >= 3, (
        f"ShowConnectionDiagnosis 只有 {n_calls-1} 个调用点（含定义）——"
        "应当覆盖 _ws==null 与 resp.ok!=true 两条失败路径"
    )

    # 粘性机制：SetStatus 必须走 RenderStatus（两行拼接），而不是直接赋值
    m = re.search(r'private void SetStatus\(string msg, Color color\)\s*\{(.*?)\n        \}', src, re.S)
    assert m, "SetStatus 没找到"
    assert "RenderStatus()" in m.group(1), (
        "SetStatus 又直接写 StatusLabel 了 —— 诊断第二行会被每次操作顶掉（回到完全静默）"
    )
    assert "StatusLabel.text =" not in m.group(1), "SetStatus 绕过 RenderStatus 直接改 text"

    # RenderStatus 要拼两行
    m2 = re.search(r'private void RenderStatus\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert m2, "RenderStatus 没找到"
    assert '\\n' in m2.group(1), "RenderStatus 没有拼第二行（换行符都不见了）"
    assert "_stickyDiag" in m2.group(1), "RenderStatus 没用到粘性诊断字段"

    # 连接恢复时要清掉诊断，别让它永远占着第二行
    cfg = re.search(r'private void RequestConfig\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert cfg, "RequestConfig 没找到"
    assert re.search(r'SetStickyDiag\(""\)', cfg.group(1)), \
        "读取成功后没有清除粘性诊断 —— 它会在连接恢复后仍然常驻"


def test_status_label_capacity_matches_two_line_design():
    """两行设计的容量依据要能在源码里复核（防止有人改文案时不查容量）。"""
    src = _read(PRESENTER)
    # 诊断文案有硬截断（超出会静默裁掉，宁可少说也要留省略号）
    m = re.search(r'const int budget = (\d+)', src[src.index("ShowConnectionDiagnosis"):])
    assert m, "诊断没有字数预算（状态栏只放得下两行，超了会被静默裁掉）"
    assert int(m.group(1)) <= 60, f"诊断预算 {m.group(1)} 字超出状态栏两行的容量"
    assert 'Substring(0, budget - 1) + "…"' in src, "超长诊断没有截断加省略号（会被静默裁掉）"


def test_diag_line_uses_warning_color_not_the_first_line_color():
    """两行共用一个 Text 组件 ⇒ 只有一个 color 字段。有诊断时必须走警示色。

    自查发现的真 bug：原先 `RenderStatus` 统一用 `_lastStatusColor`，
    于是"测试连接成功"（绿）会把"⚠ 未找到入口"也染成绿色 ——
    视觉上像一切正常，与文字内容自相矛盾。这正是"看起来最需要看见的信息反而被弱化"。
    """
    src = _read(PRESENTER)
    m = re.search(r'private void RenderStatus\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert m, "RenderStatus 没找到"
    body = m.group(1)
    # 有诊断的分支里必须用 StatusWarn
    diag_branch = body.split("_stickyDiag.Length > 0")[1]
    assert "StatusWarn" in diag_branch.split("else")[0], (
        "带诊断时没有用警示色 —— 第一行的绿/灰会把故障诊断一起染色"
    )
    # 同时确保不是无脑写死（无诊断时仍用第一行的颜色）
    assert "_lastStatusColor" in body, "无诊断时没有回落到第一行的颜色"


def test_panel_refreshes_diag_when_a_new_failure_happens_while_open():
    """面板开着时，新的拉起失败也要能冒出来（否则第二行停在旧状态）。

    自查发现的缺口：诊断原先只在 `RequestConfig()` 失败那一刻打一次。
    面板已开 + 玩家没点任何按钮 + BrainLink 那边又失败一次 ⇒ 第二行不会更新，
    与"每次失败都要弹"的要求不符（横幅那边是独立驱动的，面板这边得自己跟上）。
    """
    src = _read(PRESENTER)
    tick = re.search(r'internal void TickRestart\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert tick, "TickRestart 没找到"
    assert "TickStickyDiagRefresh()" in tick.group(1), \
        "TickRestart 没有驱动诊断刷新 —— 面板开着时的新失败不会显示"

    m = re.search(r'private void TickStickyDiagRefresh\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert m, "TickStickyDiagRefresh 没找到"
    body = m.group(1)
    # 水位线：只在真的又失败了才重画（否则每帧白算）
    assert "Launcher.FailureCount" in body, "刷新没有跟失败计数（会漏掉新失败）"
    # 面板没开就别写，且**不推进水位线**（留到打开时补）
    assert "IsPanelOpen" in body, \
        "没有判断面板是否可见 —— 关闭态会白写，且会把水位线推掉、导致打开时不再补"
    guard = body.split("IsPanelOpen")[1]
    assert "return" in guard.split("_diagRefreshedAt = fails")[0], \
        "IsPanelOpen 的守卫没有 return，水位线仍会被推进（打开面板时就不补了）"
    # 已连上时不该显示故障诊断
    assert "IsConnected" in body, "没有检查连接状态 —— 连上了还挂着故障诊断是误导"
