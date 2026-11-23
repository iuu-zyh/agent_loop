"""config_loader — 分块配置加载（唯一碰 config.json 的模块）

各司其职：
- 只有本模块负责「文件 → dict」：探测路径、读 json、与代码内默认值深合并、兼容旧扁平 llm 键。
- 各组件（WsServer/ChatHub/DialogueAgent/Compressor）一律不 import 本模块，
  装配层（server.py / llm.factory）读块后以「构造参数」注入——组件保持可测、可注入、无循环依赖。
- env 覆盖规则不在这里做：loader 只管文件；环境变量优先（如 AGENT_LOOP_WS / OPENAI_*）在使用处处理。

约定：用户 config 里只写想改的字段，缺的用代码内默认值（深合并），不是"写全才能跑"。
"""

from __future__ import annotations

import json as _json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths as _paths
from . import textio as _textio

# 项目根 config.json 探测候选：统一由 paths.config_candidates() 给出（单一事实来源）。
# 权威候选 ① 是「用户数据根」——开发机 = 包目录（源码树根即包自身），发行版 = Mod 根
# （由 C# 经 AGENT_LOOP_DATA 下发，见 csharp/ModPaths.cs）。
# 历史坑（保留作教训）：曾用 parent.parent 跳到源码树的**父目录**（那里没有 config.json）+ 依赖 cwd=包根，
# 而 C# Launcher 以 scripts/ 为工作目录拉起 server → config 读不到 → LLM 内芯恒为 Echo
# （实锤：游戏自启服务全是 echo 回显的根因）。
_CONFIG_CANDIDATES = _paths.config_candidates()

# 旧扁平 llm 键（升级前的 config.json 顶层写法，迁移期兼容）
_LEGACY_LLM_KEYS = ("base_url", "api_key", "model", "image")

# 全量配置默认值：代码内真相，用户 config 只覆盖需要改的键
DEFAULT_CONFIG: Dict[str, Any] = {
    "network": {
        "host": "127.0.0.1",
        "port": 8766,               # 端口另可经环境变量 AGENT_LOOP_WS 覆盖（测试/部署灵活）
        "request_timeout": 120.0,   # 回合内同步 RPC（get_context/call_tool）超时秒；120s 容纳模态确认窗（玩家点选后才回 response）
    },
    "concurrency": {
        "max_concurrent_turns": 2,  # 全局同时运行的回合数上限（防 LLM 并发爆炸）
        "max_parallel_tools": 5,    # 单回合内工具并发上限
    },
    "room": {
        # 群聊：一轮里**最多几个人真的开口**（内部叫 K，见 room_flow.RoomFlow.k）。
        # 不是"每轮点到几个人"：被点名的人可以弃权（回【沉默】），弃权与出错都**不占**名额，
        # 候选会继续往下补位；候选名单本身以成员数为上限（plan_round），所以填一个很大的值
        # 就等于"每轮所有成员都可能开口"。默认 3 = 8 人群一轮最多 3 次模型调用，旁听者零成本。
        # 面板里没有这一行（加行要重打 AB 预制件），只能手编 config.json；改完**即时生效**。
        "max_speakers_per_round": 3,
    },
    "initiative": {
        # 总开关：false = 关掉【日节拍】自动触发（NPC 不再自己开口），由 C# NpcInitiativeMonitor 消费；
        # 诊断强制路径（_diag_initiative_force.txt）不受它影响——那是排障入口不是玩法。
        "enabled": True,
        "min_interval": 300.0,       # NPC 主动开口全局节流（秒，Python侧熔断；C#日节拍为主）
        "daily_chance": 15,          # 每游戏日触发概率 0-100，0=关闭（对齐神识传音 0-100滑杆，us_strings:2047）
        "npc_cooldown_days": 3,      # 单NPC游戏日冷却
        "npc_cooldown_real_s": 600,  # 单NPC现实秒冷却（防挂机不推日）
        "low_intim_threshold": 60,   # <阈值视为低好感（2星≈60），减半判定
        "low_intim_halve": True,     # 低好感是否减半（us_strings:2047 "<2星概率减半"）
    },
    "compaction": {
        "enabled": True,            # 自动压缩总开关：false = 不自动压（手动 /compact 与面板按钮照常可用）
        "threshold_ratio": 0.8,     # 超阈才自动压缩：pressure > ctx_window * ratio
        "retain_ratio": 0.16,       # 保留尾占比（最新消息不压）
        "ctx_window": 200000,       # LLM 上下文窗口（token；2026-09-14 由 32768 调高）
        "compaction_retries": 1,    # 摘要失败重试次数
        # 一次压缩后的冷却秒数（防抖）。20 秒足够：压缩成功后上下文压力已降到阈值以下，
        # 冷却只用来挡住「同一轮里反复触发」。旧默认 20000 是毫秒值误写进秒字段
        # （≈5.5 小时），等于一个会话只自动压一次。
        "cool_down": 20.0,
    },
    "storage": {
        "storage_root": "~/.sessions",  # 会话 jsonl 落盘目录（~ 由 AgentLoop 展开）
    },
    # 「自我人设」：玩家为自己写的人物设定，作为 system 的**一段**注入（段名 config:player_persona，
    # 见 system_prompt.SystemPrompt.set_player_persona）。与 L1 上下文里的「玩家」段**分工不同**：
    # 那一段是 C# 从存档读出来的**游戏事实**（姓名/性别/修为/宗门/关系/位置），这一段是**玩家自述**。
    # 默认空 = 这一段根本不存在 ⇒ 不启用本功能时系统提示词与今天**逐字节相同**（零回归）。
    "player": {
        "persona": "",              # 上限 4000 字（超长由 config_store fail-closed 拒收，不静默截断）
    },
    "llm": {
        "base_url": None,
        "api_key": None,
        "model": None,
        "image": {},                # image.enabled 由 factory 读
        "retries": 1,               # 网络/瞬时错误重试次数（总尝试 = retries+1；0 不重试）
        # ── 超时 = 存活看门狗，不是「整趟耗时上限」（09-18 重做，见 docs/reference/brain-liveness.md §4）──
        # 首字看门狗（秒）：请求发出 → **第一个内容块**（含连接/排队/响应头/预填充）。
        # 只问"多久没有新内容"，**不问"已经跑了多久"**：只要模型还在出字，跑十分钟也不算超时。
        #   旧语义（09-17 之前）是"这一趟的挂钟硬顶"，于是"吐字很顺、总时长超了"的长回复
        #   照样被砍断 —— 实机里姜萌的思考流就是这样在第 45 秒被砍、群聊该成员本轮整段丢失。
        # 2026-09-18 定 120：实测首字延迟最高 28.5s（思考型模型 + 大 prompt），留 4 倍余量。
        # 同一个值还兼作 httpx read timeout 的背板（见 openai_client._httpx_read_timeout）。
        # 0/null = 不设看门狗（可无限等；"挂 6 分钟零报错"就是这么来的，别这么填）。
        "timeout": 120.0,
        # 停顿看门狗（秒）：相邻两个**内容块**之间的间隔上限（吐字中途卡住）。
        # 为什么不复用上面那个值：两个阶段量纲差三个数量级 —— 首字要容忍网关排队/预填充
        # （实测最高 28.5s），而正常吐字间隔是毫秒级（实测 decode 4094ms/247 token ≈ 16ms/token）。
        # 2026-09-18 定 60：正常间隔的 ~1000 倍，只在"真死了"时才触发。
        # 心跳免疫：数的是"解析出来的内容块"，对端发 SSE 注释/心跳**不**续命（旧实现靠 httpx
        # read timeout 才需要额外的挂钟兜底）。0/null = 不设看门狗。
        "idle_timeout": 60.0,
        # 非流式等待上限（秒）。**必须与上面分开**：非流式请求在整段生成完之前一个字节都不发，
        # read timeout 量到的是"完整生成时长"（含首 token），量纲和流式的"块间隔"完全不同。
        # 唯一调用方是回合边界的上下文压缩（大 prompt，阈值 = ctx_window×0.8）。
        # 用 45s 去卡它会静默废掉压缩（压缩失败是软失败，只落一条 warning，回合照常继续）。
        "timeout_nonstream": 180.0,
        # 可选**绝对上限**（秒）：一次 generate 连同全部重试在内的挂钟硬顶。**默认 0 = 不设顶**。
        # ★ 它不再是默认判据 ★：默认判据只有上面两个看门狗。整趟耗时不是"卡住"的判据 ——
        #   09-17 事故就是长回复被它按秒砍断的（"长程任务凭什么按总时长判死"）。
        # 显式设成 >0 时才生效，且只做一件事：把两个看门狗收缩进剩余额度，于是
        #   "总耗时 ≤ 本值"重新成为硬保证。只约束流式（聊天）路径；压缩不受它约束。
        # 玩家最坏等待（默认值下）= 首字看门狗 120 × (retries+1=2) + 退避 0.5 ≈ 240s，
        #   且**只有"一直没有任何内容"才会走满**；一直在出字就不会被判超时。
        "total_budget": 0.0,
        # ── 协议与输出上限（09-18 新增，起因见 docs/reference/csharp-iron-laws.md docs/features/brain-liveness.md §4）──
        # 网关不一定只有一条协议路：OpenCode Zen/Go 把模型分挂在 `/chat/completions`
        # （OpenAI 兼容）、`/messages`（Anthropic）、`/responses`（OpenAI Responses）三条上。
        # 对只在 /messages 上的模型（union-alpha、minimax-m*、qwen3.x-max…）打 /chat/completions，
        # 网关只回一个光秃秃的 500 —— 玩家看到的是"配了个新模型，AI 全哑"，且没有任何线索。
        #   auto（默认）：依次试 openai → anthropic → responses，命中即锁定
        #                 （见 llm/auto_client.py）。代价 = 每个内芯实例最多白跑两发
        #                 （只有真换了协议才白跑；改配置才会重建内芯）。
        #   openai     ：只用 /chat/completions（历史行为，不做任何试探）。
        #   anthropic  ：只用 /messages（鉴权走 x-api-key，max_tokens 必填）。
        #   responses  ：只用 /responses（input 扁平条目、system 走 instructions）。
        # 显式写具体协议 = 要确定性，故不做回退。
        "protocol": "auto",
        # **仅 Anthropic 协议**用：该协议的 max_tokens 是必填项，缺了直接 400。
        # 本仓真实产出量级很小（NPC 回复 100~300 token，压缩摘要 ~2k），8192 有三倍余量。
        # 给太大没好处：顶到上限时回复会截断（stop_reason=max_tokens），本层会为此打一条
        # WARNING 并点名这个键 —— 宁可小一点响一声，也别悄悄少半句。
        "max_tokens": 8192,
        # ── 思考强度（09-21，OpenAI 生态请求体字段 reasoning_effort）──
        # None = 不发送（请求体与旧版逐字节相同）。值域 low/high/max；**注入与否由
        # 探测结论门控**（llm.probe，见 factory._gated_reasoning_effort）：
        #   · config 里没有 probe 块 = 手编直通（高级用户自负其责）；
        #   · 有 probe 块则 base_url/model 逐字匹配且 verdict ∈ {supported, no_think} 才注入。
        # 探测跑在「测试连接」里（llm/capability.py，差分三发），结论由 C# 保存时随表单落盘。
        "reasoning_effort": None,
        # 注意：probe 块**刻意不进默认值** —— "没有 probe 块 = 手编直通"这条门控语义
        # 依赖"用户没写它"，给个默认 {} 会模糊这条界线（verdict 空值判断虽然等价，
        # 但配置文件里恒有一个空 probe 块只会让人困惑）。
    },
    # UI 显示开关（不属于任何运行参数，只控制配置面板表单显隐；默认全关）
    "ui": {
        # true = 显示 initiative（自主交互）等高级行，供开发调试；终端用户保持 false/缺省
        "show_advanced": False,
        # 立绘开关：纯 C# 消费（Python 只登记默认值与白名单，不做别的——立绘是游戏内渲染）
        "portraits_enabled": True,
        # 忙标「慢」提示阈值（秒）：回合里等某个阶段超过它，忙标文案就追加已等秒数，
        # 让玩家能区分「模型在慢慢想」和「真的卡住了」。<=0 = 关闭（永远不追加秒数）。
        #
        # ★ 为什么不复用 logging.slow_ms ★：那是**日志**阈值（Python 打 WARNING 用），
        #   语义是"这个阶段该报警了"，而这里是"该让玩家看见了"——两者会想分开调
        #   （排障时把 slow_ms 调到 5s 看全部阶段，不代表玩家想每 5 秒就看见一个数字）。
        #   而且 logging 块刻意不在 UI 白名单里（专业字段，手改文件），拿它当玩家可见
        #   反馈的旋钮，等于把一个"藏起来的键"变成产品行为。
        #
        # 默认 10 秒：首字延迟中位 ≈7s（实测 358 次），10 秒开始报数刚好压在正常波动之上；
        # 报得太早会显得每句话都在卡，太晚（比如 30s）则玩家早就以为死了。
        "busy_hint_after": 10.0,
    },
    # 日志装配块（归 log_setup 解释；本块属「专业字段」，不进 UI 白名单，手改文件）
    # 默认值以 log_setup.DEFAULT_LOGGING 为真相源，此处仅给 UI/诊断一个可读镜像；
    # 缺键时 log_setup 会用自己的默认补齐，故无需两处严格同步。
    "logging": {
        "enabled": True,
        "level": "INFO",                 # 排障时调 DEBUG
        "file": "logs/agent_loop.log",   # 相对路径锚包根（非 cwd）；空串 = 不落文件
        "rotation": "daily",             # daily | size | none
        "backup_days": 7,
        "console": True,
        "console_level": "WARNING",
        "slow_ms": 30000,                # 阶段慢告警阈值（ms）；0 = 关闭
        "slow_escalations": 3,           # 慢告警次数封顶（1x/2x/4x…）
        "trace_frames": False,           # 逐帧记 C#↔Python 线上帧（DEBUG 级，排障时开）
        "capture_root": False,           # true = 连第三方库日志一起收进文件
        "modules": {},                   # 模块级覆盖，如 {"agent_loop.llm": "DEBUG"}
        "quiet_libs": ["httpx", "httpcore", "openai", "websockets", "urllib3", "asyncio"],
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并：override 中 dict 键递归合并，其余覆盖；只改出现的键。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_legacy_llm(data: Dict[str, Any]) -> Dict[str, Any]:
    """旧扁平 llm 键兼容：顶层出现 base_url/api_key/model/image 且无 llm 块时，归入 llm 块。"""
    d = dict(data)
    if "llm" not in d:
        legacy = {k: d.pop(k) for k in _LEGACY_LLM_KEYS if k in d}
        if legacy:
            d["llm"] = legacy
    return d


def load_config(config_path: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    """读 config.json → 深合并进 DEFAULT_CONFIG → 返回完整配置 dict。

    无文件 / 非 dict / 坏 json 一律返回默认全量（永不抛错，装配层安全起跑）。
    """
    paths = [Path(config_path)] if config_path else list(_CONFIG_CANDIDATES)
    for p in paths:
        if p.is_file():
            try:
                # 经 textio 读：玩家用记事本存成「UTF-8 带 BOM」是常见事，
                # 直接 read_text("utf-8") 会被 BOM 顶成 "Expecting value: line 1 column 1"
                data = _json.loads(_textio.read_text(p))
                if isinstance(data, dict):
                    data = _normalize_legacy_llm(data)
                    return _deep_merge(deepcopy(DEFAULT_CONFIG), data)
            except Exception:
                continue
    return deepcopy(DEFAULT_CONFIG)


def get_block(cfg: Dict[str, Any], name: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """取一个分块（load_config 已保证块存在；default 为兜底）。"""
    block = cfg.get(name)
    return block if isinstance(block, dict) else (default if default is not None else {})