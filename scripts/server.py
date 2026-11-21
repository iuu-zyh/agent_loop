"""server.py — Python 平台常驻入口（C# 端拉起的目标脚本）

分层分权：Python 是大脑（对话/工具决策），C# 是双手（g.world/g.conf）。
C# 端 ModMain.Init 会 Process.Start 拉起本脚本；这里保持长驻，等待 C# 连接与驱动对话。

职责：
  1. 读分块 config（config_loader）：network / concurrency / initiative / compaction / storage / llm
  2. 起 WsServer（host/port/request_timeout 来自 network；端口另可 AGENT_LOOP_WS 覆盖）
  3. 持全局 AgentLoop 单例（bridge=WsGameBridge，llm 走 factory，compactor 走 compaction 块）
  4. 用 ChatHub 承接 C# 的 player_message / npc_initiative → 驱动 DialogueAgent → 推流
  5. 仅作「组合 + 常驻」，不内置对话驱动（驱动在 I/O 端：C# 游戏 UI / chat_cli --ws）

用法：
  python scripts/server.py                 # 长驻，仅初始化
  python scripts/server.py --log debug     # 同前，且把日志总闸开到 debug
  python scripts/server.py --selftest      # 自检依赖与数据根后**立刻退出**（打包后的探针入口）
  python scripts/chat_cli.py --ws --npc 林婉清  # 以模拟 C# 客户端连入验证全链路
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import os
from pathlib import Path
from typing import Any, Dict, Optional

# ---- 导入引导：把「含 agent_loop 包的那一层」放进 sys.path ----
# 两种合法布局都要认（2026-09-13 打包审计：旧实现写死 parent.parent.parent，
# 硬性要求目录必须恰好叫 agent_loop，且 server.py 必须在 <树根>/scripts/ 下——
# 打包后 server.py 与 agent_loop/ 同层，旧写法会插错层、import 直接失败）：
#   开发机：<树根>/agent_loop/  +  <树根>/scripts/server.py   → 要插的是 <树根> 的父目录
#   发行版：<Mod根>/AgentLoop/agent_loop/  +  同层 server.py   → 要插的是 <Mod根>/AgentLoop
# 判据统一为「哪个候选目录下真的有 agent_loop 包」——不猜层级、不看目录名。
# **必须同时认 .pyc**：发行包按 `--protect pyc` 只发字节码，此时包内没有 __init__.py，
# 只认 .py 会让引导静默失败（然后 import 报"没有名为 agent_loop 的模块"，极难定位）。
def _has_agent_loop_pkg(cand: Path) -> bool:
    pkg = cand / "agent_loop"
    if not pkg.is_dir():
        return False
    if (pkg / "__init__.py").is_file() or (pkg / "__init__.pyc").is_file():
        return True
    return any(pkg.glob("__pycache__/__init__.*.pyc"))


def _bootstrap_sys_path() -> None:
    # PyInstaller 设 `sys.frozen`；**Nuitka 不设**（它给每个被编译的模块注入 `__compiled__`）。
    # 只认 frozen 会让本守卫在 Nuitka 产物里静默失效 —— 实测Nuitka onefile 的
    # `--selftest` 打印 `frozen=False`。当前恰好无害（自解压目录里只有 agent_loop/prompts/，
    # 没有 __init__.py，四层候选全部落空），但那是巧合不是保证，两个判据都得认。
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return
    here = Path(__file__).resolve().parent
    for cand in (here, here.parent, here.parent.parent, here.parent.parent.parent):
        if _has_agent_loop_pkg(cand):
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            return


_bootstrap_sys_path()
del _bootstrap_sys_path, _has_agent_loop_pkg

from agent_loop.config.config_loader import load_config  # noqa: E402
from agent_loop.config import config_store
from agent_loop import log_setup
from agent_loop.prompts import prompt_files  # noqa: E402
from agent_loop.storage.contacts_store import ContactsService  # noqa: E402
from agent_loop.agent_loop import AgentLoop  # noqa: E402
from agent_loop.transport.bridge import WsGameBridge  # noqa: E402
from agent_loop.transport.ws_channel import WsServer, ChatHub  # noqa: E402
from agent_loop.llm.factory import create_llm_client  # noqa: E402  （ConfigService 热换内芯也用）
from agent_loop.agent.system_prompt import SystemPrompt  # noqa: E402  （自我人设段：装配期 + 保存热注入）

# 装配期读一次配置（改文件重启生效；运行时不改）
CFG = load_config()

# 日志开关的环境变量名（与 AGENT_LOOP_WS 同规约：env 优先于 config，在「使用处」解释）
LOG_ENV_VAR = "AGENT_LOOP_LOG"

# 父进程（游戏）PID 的环境变量名 —— C# 侧下发，见 csharp/ModPaths.EnvParentPid
PARENT_ENV_VAR = "AGENT_LOOP_PPID"

# 看门狗轮询间隔（秒）。2 秒足够快（Steam 的「正在停止」感知不到），又不值得更密。
WATCHDOG_INTERVAL = 2.0

# 只有入口持有 logger；配置在 main() 里装（模块导入不产生副作用）
log = log_setup.get_logger("agent_loop.server")


def _arg_parser() -> argparse.ArgumentParser:
    """入口参数。--log 是日志总闸：测试时开、正式部署关，一个词搞定。"""
    p = argparse.ArgumentParser(description="agent_loop Python 平台常驻入口")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--log", metavar="SWITCH",
                   help="日志总闸：off / on / debug / info / warning / error"
                        "（优先于环境变量 AGENT_LOOP_LOG 与 config.json 的 logging 块）")
    g.add_argument("--no-log", dest="no_log", action="store_true",
                   help="等价于 --log off（正式部署用：不建文件、不挂 handler、近似零开销）")
    p.add_argument("--log-level", metavar="LEVEL", help="只改日志级别（等价于 --log <LEVEL>）")
    return p


def resolve_log_switch(args: Any = None, env: Optional[Dict[str, str]] = None) -> Any:
    """决定本次运行的日志开关：CLI 参数 > 环境变量 AGENT_LOOP_LOG > None（沿用 config）。

    纯函数（不读全局），便于测试；env 可注入。
    """
    env = os.environ if env is None else env
    if args is not None:
        if getattr(args, "no_log", False):
            return "off"
        if getattr(args, "log", None):
            return args.log
        if getattr(args, "log_level", None):
            return args.log_level
    return env.get(LOG_ENV_VAR) or None

_loop: Optional[AgentLoop] = None
_ws: Optional[WsServer] = None
# ChatHub 引用（供 ConfigService 热改 min_initiative_interval；_idle 建成后填充）
_hub: Optional[ChatHub] = None


def _parent_pid() -> int:
    """C# 下发的游戏进程 PID（0 = 不是游戏拉起的：手跑 `server.py` / pytest）。

    兼作「生产形态」判据（见 `AgentLoop.require_world`）：生产形态下未定档时禁止读写扁平
    目录 —— 堵住"Python 重启后那段窗口把内容写进谁都不属于的目录"（09-18 修）。
    手跑 / 测试保持扁平布局的原有语义（`chat_cli` 与全部单测都靠它）。
    """
    try:
        return int(os.environ.get(PARENT_ENV_VAR) or 0)
    except ValueError:
        return 0


def get_loop() -> AgentLoop:
    """获得（或构建）平台级 AgentLoop 单例。配置来自分块 config，env 优先（AGENT_LOOP_WS）。"""
    global _loop, _ws
    if _loop is None:
        net = CFG.get("network", {})
        conc = CFG.get("concurrency", {})
        storage = CFG.get("storage", {})
        game_launched = bool(_parent_pid())

        # 网络：端口可被环境变量 AGENT_LOOP_WS 覆盖（测试/部署灵活性，优先于 config）
        port = int(os.environ.get("AGENT_LOOP_WS") or net.get("port", 8766))
        _ws = WsServer(
            host=net.get("host", "127.0.0.1"),
            port=port,
            request_timeout=float(net.get("request_timeout", 5.0)),
        )

        # llm 与压缩器：同一 LlmRouter 门面（压缩摘要复用主对话模型；内芯可热换）
        from agent_loop.llm.router import LlmRouter

        router = LlmRouter(create_llm_client())
        compactor = _build_compactor(router)
        cc = CFG.get("compaction", {})

        _loop = AgentLoop(
            bridge=WsGameBridge(_ws),
            llm=router,
            compactor=compactor,
            storage_root=storage.get("storage_root", "~/.sessions"),
            max_parallel_tools=conc.get("max_parallel_tools", 5),
            context_window=cc.get("ctx_window", 32768),
            # 生产形态（游戏拉起）→ 未取得存档身份前不碰扁平目录（见 AgentLoop.require_world）
            require_world=game_launched,
        )
        # 配置 UI 热换编排：set_config 后 llm 块改动经 factory 重建内芯并 swap（Agent 零改动）
        _router_ref["router"] = router
        # 自我人设（config.player.persona）→ SystemPrompt 的全局段。**装配期注入一次**，
        # 之后改配置由 ConfigService.set_config 的热落地分支重新注入（不必重启本进程）。
        install_player_persona()
        log.info("AgentLoop 装配完成：port=%s model=%s ctx_window=%s max_turns=%s max_tools=%s storage=%s",
                 port, getattr(router, "model", None) or "-", cc.get("ctx_window", 32768),
                 conc.get("max_concurrent_turns", 2), conc.get("max_parallel_tools", 5),
                 _loop.storage_root)
    return _loop


def install_player_persona() -> bool:
    """把 config.player.persona 注入 SystemPrompt 的「自我人设」段（幂等；**唯一注入口**）。

    为什么放在装配层：`config_loader` 的约定是**各组件都不 import 它**，配置一律由装配层读出来
    以参数注入（SystemPrompt 同理 —— 它只认 set_player_persona 这一个口）。
    返回 True = 当前有这一段；空串 = 移除（见 SystemPrompt.set_player_persona）。
    """
    try:
        text = (load_config().get("player") or {}).get("persona") or ""
    except Exception:                      # 配置读坏了不该拦住装配：退回"没有人设"
        log.warning("读取 player.persona 失败，本次不注入自我人设", exc_info=True)
        text = ""
    installed = SystemPrompt.instance().set_player_persona(text)
    log.info("自我人设：%s（%d 字）", "已注入" if installed else "未设置（system 里没有这一段）", len(text))
    return installed


# LlmRouter 的装配层引用（供 ConfigService 热换内芯；get_loop 建成后填充）
_router_ref: dict = {"router": None}


def _build_compactor(router):
    """按**当前** config 造一个压缩器（装配与热换共用同一处，防两处参数漂移）。

    cool_down 冷却表从在用的压缩器继承：热换不该把「刚压过」的冷却重置掉，
    否则改一次 ctx_window 就可能立刻触发一次不必要的自动压缩。

    ★ prompts_root 必须显式指向**用户副本**（`<数据根>/prompts/compaction`）。
    不传的话 Compressor 会回落到 `Path(compress.py).parent.parent/"prompts/compaction"`
    —— 开发机上那正好等于源码树这份（看不出问题），发行版里却指向 **exe 内自带的那份**，
    于是玩家在配置面板「提示词」页编辑/重置的 `compaction/*.md` **一个字都不会生效**
    （面板还写着「作为 system 下发」）。本仓库吃过同类亏：两处各自解析路径 ⇒ 静默失效。
    """
    from agent_loop.compaction.compress import Compressor
    from agent_loop.config import paths as _paths

    cc = load_config().get("compaction", {})
    new_c = Compressor(
        llm=router,
        prompts_root=_paths.prompts_root() / "compaction",
        threshold_ratio=cc.get("threshold_ratio", 0.8),
        retain_ratio=cc.get("retain_ratio", 0.16),
        ctx_window=cc.get("ctx_window", 32768),
        compaction_retries=cc.get("compaction_retries", 1),
        cool_down=cc.get("cool_down", 20000.0),
        enabled=cc.get("enabled", True),
    )
    old = getattr(_loop, "compactor", None) if _loop is not None else None
    if old is not None and hasattr(old, "_cool_until") and hasattr(new_c, "_cool_until"):
        new_c._cool_until = dict(old._cool_until)
    return new_c


class ConfigService:
    """装配层配置服务：写回 config.json + 逐键热生效（llm 换芯 / RPC 超时 / 压缩器 / 开口节流）。

    各司其职：文件写回归 config_store，换芯归 LlmRouter.swap，改活归各持有者，
    这里只按 config_store 给出的 `hot_keys` 组合落地；需要重启的键交给 C# 编排重启。
    """

    def get_config(self):
        return config_store.get_config()

    def set_config(self, partial):
        result = config_store.set_config(partial)
        hot = set(result.get("hot_keys") or [])
        # ① llm 块：经 factory 重建内芯并 swap（Agent 零改动）
        if "llm" in {k.split(".", 1)[0] for k in hot} and _router_ref.get("router") is not None:
            try:
                old = _router_ref["router"].swap(create_llm_client())
                # 带上协议名：auto 模式下光看类名是 AutoProtocolLlmClient，看不出实际在说哪条协议
                log.info("LLM 内芯已热切换（旧芯 %s[%s] → 新芯 %s[%s]）",
                         type(old).__name__, getattr(old, "protocol_name", "-"),
                         type(_router_ref["router"].client).__name__,
                         getattr(_router_ref["router"].client, "protocol_name", "-"))
            except Exception as e:  # 热换失败不回滚文件：改动已写盘，重启后同样生效
                log.warning("LLM 热切换失败（重启后生效）: %s", e, exc_info=True)
                result["swap_error"] = str(e) or type(e).__name__
        # ② network.request_timeout：WsServer.request 每次现读 self.request_timeout
        #    （bridge 的 get_context/call_tool 都不传显式 timeout），故改属性即对后续 RPC 生效。
        if "network.request_timeout" in hot and _ws is not None:
            try:
                new_to = float(load_config().get("network", {}).get("request_timeout", _ws.request_timeout))
                old_to = _ws.request_timeout
                _ws.request_timeout = new_to
                log.info("RPC 超时已热生效：request_timeout %ss → %ss", old_to, new_to)
            except Exception as e:  # 兜底：置 swap_error 让 C# 走重启（改动已写盘，重启后必然生效）
                log.warning("RPC 超时热生效失败（重启后生效）: %s", e, exc_info=True)
                result["swap_error"] = str(e) or type(e).__name__
        # ③ compaction 块：压缩器把 ctx_window/阈值比/保留比/自动开关都存成构造参数，
        #    逐键改属性等于把"整块重建"这件事拆散，故任何 compaction 热键都在这里重建一个
        #    压缩器热挂回 AgentLoop（管家持有 + 推给活动与冻结 agent 的 stats 预算）。
        if any(k.startswith("compaction.") for k in hot) and _loop is not None:
            try:
                cc = load_config().get("compaction", {})
                # ★ None 容错（10-21，Gemini 用户报错定位）★
                #   `.get(key, 默认)` 只护"键不存在"；手编 config.json 写 `"ctx_window": null`
                #   （Gemini 心智里 null=自动）时键在、值是 None ⇒ int(None) 抛
                #   `TypeError: int() argument must be a string... not 'NoneType'`，
                #   经 swap_error 原样回给设置面板 = 用户看到的是"**点保存就报类型转换错**"。
                _ctx_raw = cc.get("ctx_window")
                if _ctx_raw is None:
                    _ctx_raw = getattr(_loop, "context_window", 32768) or 32768
                new_ctx = int(_ctx_raw)
                n = _loop.rebind_compactor(_build_compactor(_router_ref.get("router")), context_window=new_ctx)
                log.info("压缩器已热换：ctx_window=%s 阈值比=%s 保留比=%s 自动=%s 冷却=%ss（同步 agent %d 个）",
                         new_ctx, cc.get("threshold_ratio", 0.8), cc.get("retain_ratio", 0.16),
                         cc.get("enabled", True), cc.get("cool_down", 20000.0), n)
            except Exception as e:  # 兜底：置 swap_error 让 C# 走重启（改动已写盘，重启后必然生效）
                log.warning("压缩器热换失败（重启后生效）: %s", e, exc_info=True)
                result["swap_error"] = str(e) or type(e).__name__
        # ④ initiative.min_interval：ChatHub.min_initiative_interval 是普通属性，每次主动开口
        #    现读（ws_channel 的全局节流），故改属性即对后续开口生效。initiative 的其余键由
        #    C# 消费（日概率/冷却/低好感减半），C# 保存成功后会自己重拉 get_config，Python 不插手。
        if "initiative.min_interval" in hot and _hub is not None:
            try:
                new_iv = float(load_config().get("initiative", {}).get("min_interval", _hub.min_initiative_interval))
                old_iv = _hub.min_initiative_interval
                # 与 ChatHub.__init__ 同一夹取口径：负数间隔 = 关闭节流，不是"穿越回过去"
                _hub.min_initiative_interval = max(0.0, new_iv)
                log.info("主动开口最小间隔已热生效：min_interval %ss → %ss", old_iv, _hub.min_initiative_interval)
            except Exception as e:  # 兜底：置 swap_error 让 C# 走重启（改动已写盘，重启后必然生效）
                log.warning("主动开口间隔热生效失败（重启后生效）: %s", e, exc_info=True)
                result["swap_error"] = str(e) or type(e).__name__
        # ⑤ player.persona：自我人设段。SystemPrompt 是进程单例的全局屉，重新注入后
        #    **下个回合**的 system 就是新文本（无需重启 Python，更无需重启游戏）。
        #    为什么不用重启：段是每步 assemble 现读的；「系统组装」折叠块靠段级差分
        #    自动只上行这一次变化（标签「自我人设」）。清空 = 移除该段（不是留一段空文本）。
        if any(k.startswith("player.") for k in hot):
            try:
                old = SystemPrompt.instance().player_persona()
                installed = install_player_persona()
                log.info("自我人设已热生效：%s → %s",
                         ("%d 字" % len(old)) if old else "未设置",
                         "已注入" if installed else "已清空（system 里不再有这一段）")
            except Exception as e:  # 兜底：置 swap_error 让 C# 走重启（改动已写盘，重启后必然生效）
                log.warning("自我人设热生效失败（重启后生效）: %s", e, exc_info=True)
                result["swap_error"] = str(e) or type(e).__name__
        # ⑥ room.max_speakers_per_round：群聊「一轮里最多几个人真的开口」。
        #    RoomFlow 跑轮时**每轮现读** `self.k`，所以改属性即对下一轮生效 ——
        #    正在跑的那一轮保持旧值（半路换名额会让"这一轮已经开口的人"数不清）。
        #    夹取口径收在 RoomFlow.set_max_speakers_per_round 里（≥1，不封顶）。
        if "room.max_speakers_per_round" in hot and _hub is not None:
            try:
                new_k = load_config().get("room", {}).get("max_speakers_per_round", _hub.rooms.k)
                _hub.rooms.set_max_speakers_per_round(new_k)
            except Exception as e:  # 兜底：置 swap_error 让 C# 走重启（改动已写盘，重启后必然生效）
                log.warning("群聊发言人数上限热生效失败（重启后生效）: %s", e, exc_info=True)
                result["swap_error"] = str(e) or type(e).__name__
        # 日志块热生效：logging 块不在 UI 白名单（手改文件），但任何一次写回都顺手重配，
        # 这样「手改 level 想立刻看详细日志」不必重启游戏。
        if result.get("updated"):
            try:
                snap = log_setup.reconfigure(load_config())
                log.info("日志已按最新配置重配：level=%s file=%s slow_ms=%s",
                         snap.get("level"), snap.get("path"),
                         (log_setup.current_block() or {}).get("slow_ms"))
            except Exception:
                log.warning("日志重配失败（沿用原配置）", exc_info=True)
        return result

    # ------------------------------------------------------------------
    # 测试连接：拿**表单当前值**真调一次模型，把失败原因翻译成人话
    # ------------------------------------------------------------------
    async def test_llm(self, payload):
        """`test_llm` RPC 的实现 —— 与 set_config **完全解耦**：不写盘、不换芯、不动 router。

        为什么要有它：配错 LLM 的三种典型故障（key 错、base_url 漏 /v1、网关要求自定义头）
        在游戏里长得**一模一样** —— NPC 只会回「（传音法阵一时没接上你的话…）」，玩家
        根本无从判断该改哪一项。这里把对方的原始错误 + 归类 + 处置建议直接摆到面板上。

        为什么不走 set_config：保存会触发 Python 重启（很重）。用户改完想先试再存。
        真正干活的是 `llm.probe.probe_llm`（那边注释说明了为什么必须复用 factory 构造路径）。
        """
        from agent_loop.llm.probe import probe_llm

        p = payload if isinstance(payload, dict) else {}
        saved = (config_store.get_config() or {}).get("llm") or {}

        def _pick(key):
            """入参优先，空/缺失回落到已保存配置。

            面板回填本来就来自 get_config，正常不会缺；补这层是为了兼容
            「老 C# 只发了部分字段」以及「用户把某项清空了但其余项还想测」之外的场景 ——
            注意清空的那项会被回落填上，这是有意的：清空 base_url 的测试没有意义。
            """
            v = p.get(key)
            if v is None or (isinstance(v, str) and not v.strip()):
                v = saved.get(key)
            return v

        headers = _pick("headers")
        if not isinstance(headers, dict):
            headers = {}
        try:
            timeout = float(p.get("timeout") or 15.0)
        except (TypeError, ValueError):
            timeout = 15.0

        # protocol 也走 `_pick`：面板里**没有**这一行（加行要重打 AB 预制件），
        # 所以正常永远回落到 config.json 里那份；手编过 protocol 的用户能在这里被尊重。
        result = await probe_llm(
            base_url=_pick("base_url") or "",
            api_key=_pick("api_key") or "",
            model=_pick("model") or "",
            headers=headers,
            timeout=max(3.0, min(timeout, 60.0)),
            protocol=_pick("protocol"),
        )
        # ── 能力探测（09-21）──：连通测通才有意义（基准都失败时探测结论只能是 unknown）。
        # 结果随回包上行给 C#：verdict 驱动面板「思考强度」行的显隐；用户点保存时
        # C# 把它一并写进 config.llm.probe —— 本函数**保持不写盘**（与 test_llm 的
        # 「不落盘、不换芯」约定一致，见 docstring）。
        if result.get("ok"):
            result["probe"] = await self._probe_capability(_pick, headers, timeout)
        else:
            result["probe"] = {"verdict": "unknown", "reasoning_chars": 0,
                               "why": "连接未通，跳过能力探测"}
        err = result.get("error") or {}
        probe = result.get("probe") or {}
        log.info("model test: ok=%s kind=%s status=%s latency=%sms model=%s protocol=%s "
                 "headers=%s probe=%s(%s)",
                 result.get("ok"), err.get("kind"), err.get("status"),
                 result.get("latency_ms"), result.get("model"), result.get("protocol"),
                 result.get("header_names"), probe.get("verdict"),
                 probe.get("reasoning_chars"))
        return result

    @staticmethod
    async def _probe_capability(_pick, headers, timeout) -> dict:
        """`reasoning_effort` 能力探测（差分三发并行，见 llm/capability.py）。

        探测失败**绝不能**连累测试连接本身 —— 那发已经成功了，面板上的绿色结论不许
        被附加步骤染红。一切异常按 unknown 收口（面板行为 = 不显示那一行，与旧版相同）。
        """
        from agent_loop.llm.capability import probe_reasoning_effort

        try:
            return await probe_reasoning_effort(
                base_url=_pick("base_url") or "",
                api_key=_pick("api_key") or "",
                model=_pick("model") or "",
                headers=headers,
                timeout=max(5.0, min(timeout, 30.0)),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("能力探测失败（按 unknown 处理）: %s", e, exc_info=True)
            return {"verdict": "unknown", "reasoning_chars": 0,
                    "why": type(e).__name__}


def _parent_alive(pid: int) -> bool:
    """父（游戏）进程还活着吗？**查不到一律当作活着**，宁可多等也不要误杀。

    Windows：OpenProcess + GetExitCodeProcess（STILL_ACTIVE=259）。
    其它平台：os.kill(pid, 0)。任何异常都返回 True —— 这个函数只用来决定「什么时候退」，
    判错方向必须是保守的（多跑一会儿无害，误杀会让 mod 莫名其妙掉线）。
    """
    if pid <= 0:
        return True
    try:
        if os.name == "nt":
            import ctypes

            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False                          # 打不开句柄：进程已不在
            try:
                code = ctypes.c_ulong()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return True                       # 查不出来 → 保守当活着
                return code.value == 259              # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        os.kill(pid, 0)
        return True
    except Exception:
        return True


async def _idle():
    """常驻：启动 WS 通道并保活，等待 C# 客户端连接与消息驱动。"""
    global _hub
    log_setup.install_asyncio_hooks()   # create_task 里抛出的异常不再静默消失
    loop = get_loop()
    conc = CFG.get("concurrency", {})
    init = CFG.get("initiative", {})
    room = CFG.get("room", {})
    # ★ None 容错（10-21，Gemini 用户保存报错同源模式）★
    #   `.get(k, 默认)` 只护"键缺失"；手编 config（或 Gemini 心智里 null=自动）造成
    #   "键在值为 null" 时，float(None)/int(None) 在**装配期**炸穿——服务起不来，
    #   BrainLink 判死重拉三次后放弃，玩家看到的是整个对话功能无声消失。
    #   0 与 null 语义分开：null=没配（回默认）；0/负=配了但非法（原样传下去，
    #   由 ChatHub/RoomFlow 各自的抬升闸处理——三层一致的老规矩，别在装配层抢答）。
    _mc, _mi, _spk = (conc.get("max_concurrent_turns"),
                      init.get("min_interval"),
                      room.get("max_speakers_per_round"))
    hub = ChatHub(
        loop,
        _ws,
        max_concurrent_turns=int(_mc) if _mc is not None else 2,
        min_initiative_interval=float(_mi) if _mi is not None else 300.0,
        max_speakers_per_round=int(_spk) if _spk is not None else 3,
        config_service=ConfigService(),
        prompt_service=prompt_files,
        contact_service=ContactsService(loop.storage_root, require_world=loop.require_world),
    )
    # 留引用给 ConfigService 热改 min_initiative_interval（见 ConfigService ④）：
    # WS 通道在 hub 建好之后才 start，故任何 set_config RPC 到达时这里必然已填充。
    _hub = hub
    _ws.register_message_handler(hub.handle_message)
    _ws.register_initiative_handler(hub.handle_initiative)  # NPC 主动开口（C# 触发检测）
    _ws.register_request_handler(hub.handle_request)  # C#→Python 读请求（get_history 等，游戏 UI 历史）
    _ws.register_compact_handler(hub.handle_compact)  # 手动压缩（玩家输入 /compact，专用事件通道）
    _ws.register_room_message_handler(hub.handle_room_message)  # 群聊（玩家在群里发言 → 接力一轮）
    _ws.register_room_skip_handler(hub.handle_room_skip)  # 群聊跳过（玩家不发言，NPC 按轮转自己接下去）
    await _ws.start()
    log.info("WS 通道已监听 %s:%s，等待 C# 连接（日志：%s）",
             _ws.host, _ws.bound_port, log_setup.current_log_path() or "未落文件")

    # ---- 父进程看门狗：游戏一退，本进程立刻退（否则 Steam 的「正在停止」结束不了）----
    # 为什么是「查父进程」而不是「WS 断线超时」：配置面板保存会重启 Python，那一刻 WS 同样断，
    # 但游戏活得好好的 —— 用断线当判据会把自己误杀（见 csharp/ModPaths.EnvParentPid 注释）。
    # 未下发 PID（手跑 `python scripts/server.py`、pytest）时看门狗自动关闭，行为不变。
    ppid = _parent_pid()
    if ppid:
        log.info("父进程看门狗已启用：游戏 PID=%s，它一退出本进程立即退出", ppid)

    try:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            if ppid and not _parent_alive(ppid):
                log.info("游戏进程（PID=%s）已退出 → 本进程随之退出"
                         "（否则 Steam 会一直卡在「正在停止」，端口也会被占着不放）", ppid)
                break
    except asyncio.CancelledError:
        pass
    finally:
        await _ws.stop()


def bootstrap_logging(argv=None):
    """解析入口参数并装配日志（main 与测试共用；不启动任何服务）。

    总闸优先级：CLI --log/--no-log/--log-level > 环境变量 AGENT_LOOP_LOG > config.json 的 logging 块。
    返回 (args, switch, 应用回执)。
    """
    # 容错解析：C# Launcher 目前不传参数；未来若追加，未知参数不应让进程起不来。
    args, unknown = _arg_parser().parse_known_args(argv)
    if unknown:
        print(f"[server] 忽略未知参数：{unknown}", file=sys.stderr)

    # 日志装配（全进程唯一装配点）：先于一切业务动作执行。此后所有模块只调
    # log_setup.get_logger(__name__) 取 logger，谁都不再 new handler / setLevel
    #（各司其职：装管道归 log_setup，用管道归各模块）。
    switch = resolve_log_switch(args)
    snap = log_setup.setup_logging(CFG, switch=switch)
    log_setup.install_process_hooks()   # 异常钩子紧随其后：装配期出问题也要有落点
    if snap.get("enabled"):
        log.info("日志已开启：level=%s file=%s（%s）",
                 snap.get("level"), snap.get("path") or "仅控制台", log_setup.describe_switch(switch))
    else:
        # 关闭态没有任何 handler，只能直接打 stderr——不写这行的话"到底开没开"要靠猜
        print(f"[server] 日志已关闭：{log_setup.describe_switch(switch)}"
              f"（需要时用 --log debug 或设 {LOG_ENV_VAR}=debug）", file=sys.stderr)
    return args, switch, snap


def selftest() -> int:
    """自检：验证本进程（尤其是打包成 exe 后）的依赖与数据根是否就绪，**立刻退出**。

    为什么必须有这个入口（2026-09-13 打包）：
      C# Launcher 解析「Python 可执行」时会跑 `python -c "import websockets; print(1)"` 做探测，
      但打包成 self-contained exe 后**那条命令行根本不成立**——exe 会把 `-c` 当未知参数
      （server.py 的 argparse 用的是 parse_known_args）照常起服务、永不退出，探测方
      WaitForExit 超时 → `p.ExitCode` 抛异常 → 把一个好端端的 exe 判成"不可用"。
      所以给 exe 一个「一句话自检并立刻退出」的入口，让两侧都能问同一个问题。

    退出码：0 = 依赖齐全（进程可拉起）；1 = 缺依赖（stderr 点名缺谁）。
    **不碰任何业务初始化**：不读 config 全文、不起 WS、不建日志文件——它就是个探针。
    """
    from agent_loop.config import paths

    missing = []
    for mod in ("websockets", "openai"):
        try:
            __import__(mod)
        except Exception as e:   # 缺依赖 / 装坏了：两种都要点名，别只说"失败"
            missing.append("%s: %s" % (mod, e))

    data = paths.data_root()
    print("[selftest] frozen=%s" % bool(getattr(sys, "frozen", False)))
    print("[selftest] python=%s" % sys.version.split()[0])
    print("[selftest] data_root=%s" % data)
    # 两个 prompts 根分开打印（2026-09-16）：面板里看到/改的是「用户可编辑」那份；
    # 「内置默认」那份是出厂原文，只作「重置为默认」的来源，谁也改不到。
    # 排障口诀：改动没生效 → 先看第一行指的目录对不对；两行指同一处 = 源码树布局
    # （此时重置是无操作，正确行为）；内置那行标「不存在！」= 这个包没带 prompts（重置不可用）。
    user_prompts = paths.prompts_root()
    bundled_prompts = paths.bundled_prompts_root()
    print("[selftest] prompts(用户可编辑)=%s%s"
          % (user_prompts, "" if user_prompts.is_dir() else "（不存在！）"))
    print("[selftest] prompts(内置默认)  =%s%s"
          % (bundled_prompts, "" if bundled_prompts.is_dir() else "（不存在！）"))
    if missing:
        print("[selftest] 失败：缺少依赖 —— " + "；".join(missing), file=sys.stderr)
        return 1
    print("[selftest] 就绪")
    return 0


def main(argv=None):
    # --selftest 必须在**一切**初始化之前拦下：它是给 C# 探测/玩家排障用的探针，
    # 一旦让它走到 bootstrap_logging/get_loop 就会建日志文件、读配置、占端口。
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()

    bootstrap_logging(argv)
    get_loop()
    # 署名水印：二次打包者若不移除，任何流出的副本都会在日志里打出原作者
    try:
        import agent_loop as _pkg
        log.info("《%s》v%s by %s — %s",
                 _pkg.__mod_name__, _pkg.__version__, _pkg.__author__, _pkg.__repo__)
        log.info("许可：%s（禁止商用 / 禁止二次打包发布）", _pkg.__license__)
    except Exception as e:                       # 署名失败绝不能拖垮启动
        log.warning("署名信息读取失败：%s", e)
    log.info("AgentLoop 已初始化，等待启动 WS 通道")
    try:
        asyncio.run(_idle())
    except KeyboardInterrupt:
        log.info("收到 KeyboardInterrupt，退出")
    finally:
        log.info("进程退出，flush 日志")
        log_setup.shutdown_logging()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())