"""GameBridge — 游戏侧抽象（分层分权：DialogueAgent 只认此接口，不认 g.world）

职责边界：
- SystemPrompt 不认识 GameBridge，只存 DialogueAgent 给它的 L1 文本
- DialogueAgent 认识 GameBridge，负责每轮 preStep 取 L1（轮内缓存）、执行 tool_calls 时转调
- AgentLoop 只透传 bridge，不碰屉
- 具体实现：StubGameBridge(内存桩，脱离游戏可测) / WsGameBridge(真机 WS 单连接)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from . import log_setup

log = log_setup.get_logger(__name__)


class GameBridge(Protocol):
    """游戏桥协议：只做两件事——给快照、执行工具"""

    def get_context(self, npc_id: str) -> Dict[str, Any]:
        """取 L1 易变快照。返回 {"text": str, "raw": dict}，text 将写入 runtime:l1 context"""
        ...

    def call_tool(self, name: str, arguments: Dict[str, Any], npc_id: Optional[str] = None) -> Dict[str, Any]:
        """执行工具。返回 {"success": bool, "data": Any, "error": str?}，data 将以 role:tool 回灌模型。

        `npc_id` = **发起这次工具调用的 NPC**（09-13 加）：C# 侧靠它把"动作完成"归属到具体 NPC
        （用于"同格动作完成 → 自动打开对话 UI"，见 ActionWatcher）。缺省 None = 不归属。
        """
        ...


# ---------- Stub：脱离游戏的内存桩 ----------
class StubGameBridge:
    """内存桩：自带最小世界，供 tests 脱离 Game.exe 跑通全链路"""

    # 需同处一地（面对面）才能执行的动作：world_ai_action 仅列出的 op（spar/attack/双修/传功）。
    # 对齐神识传音 §5.3：坐标不一致（异地传音）时此类行动不可用，应改以言语相商。
    _FACE_TO_FACE_OPS: Dict[str, Any] = {
        # combat_duel 已并入 world_ai_action：spar/attack 与 双修/传功 需同格，论道/邀约等言语往来可远程
        "world_ai_action": ("spar", "attack", "shuang_xiu", "chuan_gong"),
    }

    def __init__(self):
        # 每 NPC 一份极简档案：可按需 seed
        self._units: Dict[str, Dict[str, Any]] = {}
        self._world = {
            # sects 概览走 _query_world 的结构化默认值（对齐真机 C# QueryWorldSects 字段形态）
            "realms": ["炼气", "筑基", "结晶", "金丹", "具灵", "元婴", "化神", "悟道"],
        }

    # 默认可去地点表（places/travel 共用同一份，保证查询与传送口径一致）
    _DEFAULT_PLACES = [
        {"name": "白帝城", "cat": "城镇", "region": "永宁州", "point": {"x": 100, "y": 200}},
        {"name": "北斗剑宗", "cat": "宗门", "region": "永宁州", "point": {"x": 300, "y": 400}},
        {"name": "天机阁藏经洞", "cat": "突破材料", "region": "永宁州", "point": {"x": 150, "y": 260}},
        {"name": "器灵残骸", "cat": "器灵材料", "region": "雷泽", "point": {"x": 540, "y": 120}},
    ]

    def seed_unit(self, npc_id: str, profile: Dict[str, Any]):
        self._units[npc_id] = profile

    # -- L1 快照 --
    def get_context(self, npc_id: str) -> Dict[str, Any]:
        # 若无档案，给最小兜底；有档案则拼 200 字内快照
        p = self._units.get(npc_id, {})
        if not p:
            text = f"自身：{npc_id}（档案未预置，Stub 兜底）\n玩家：韩立 筑基 同格 关系：道友 好感120\n近况：无"
            return {"text": text, "raw": {"npc_id": npc_id, "stub": True}}
        realm = p.get("realm", "筑基")
        sect = p.get("sect", "无宗门")
        pos = p.get("pos", "永宁州")
        mood = p.get("mood", 70)
        power = p.get("power", 5000)
        player = p.get("player", {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 120, "same_grid": True})
        recent = p.get("recent", "上月与韩立切磋一次")
        same = "同格" if player.get("same_grid") else "异地"
        text = (
            f"自身：{npc_id} {realm} {sect} {pos} 心情{mood} 战力{power}\n"
            f"玩家：{player.get('name')} {player.get('realm')} {same} 关系：{player.get('relation')} 好感{player.get('intim')}\n"
            f"近况：{recent}"
        )
        return {"text": text, "raw": p}

    # -- 工具执行：按 8 工具分发，均为只读/内存写，无副作用外溢 --
    def call_tool(self, name: str, arguments: Dict[str, Any], npc_id: Optional[str] = None) -> Dict[str, Any]:
        # npc_id 只在真机（WsBridge）有意义：内存桩没有"哪个 NPC 在动手"这层归属
        try:
            if name == "inspect_unit":
                return self._inspect_unit(arguments)
            if name == "search_units":
                return self._search_units(arguments)
            if name == "query_world":
                return self._query_world(arguments)
            if name in ("social_relation", "movement", "world_ai_action", "economy_item", "item_acquire"):
                return self._action_stub(name, arguments)
            return {"success": False, "error": f"unknown tool {name}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _inspect_unit(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = args.get("target", "")
        classes = args.get("classes") or ["brief"]
        if isinstance(classes, str):
            classes = [classes]
        page = int(args.get("log_page", 1))
        # 查档案
        p = self._units.get(target)
        if p is None:
            return {"success": False, "error": f"未找到 {target}，可先 search_units 搜索"}
        data: Dict[str, Any] = {"name": target}
        if "brief" in classes:
            # 气运随 brief（与真机 C# 契约同口径）：born/added 分列，后天气运动态变化
            luck = p.get("luck") or {}
            data.update({"realm": p.get("realm"), "sect": p.get("sect"), "relation": p.get("player", {}).get("relation", "陌生"), "intim": p.get("player", {}).get("intim"),
                         "luck": {"born": luck.get("born", []), "added": luck.get("added", [])}})
        if "stats" in classes:
            data.update({"mood": p.get("mood"), "power": p.get("power")})
        if "abilities" in classes:
            data.update({"abilities": p.get("abilities", [])})
        if "inventory" in classes:
            # 分类背包（与真机 C# 契约同口径）：props=[{cat, items:[{name,count,worth,total}...], misc}]
            raw_items = p.get("items", []) or []
            props = [{"name": s, "count": 1, "worth": 0, "total": 0, "desc": ""} for s in raw_items]
            data.update({
                "props": ([{"cat": "其他", "items": props}] if props else []),
                "equips": [{"name": s, "worth": 0} for s in (p.get("equips", []) or [])],
            })
        if "relationships" in classes:
            data.update({"relations": p.get("relations", {"player": p.get("player")})})
        if "logs" in classes:
            logs = p.get("logs", [])
            since = int(args.get("log_since_month", 0) or 0)
            until = int(args.get("log_until_month", 0) or 0)
            if since > 0 or until > 0:
                logs = [x for x in logs if isinstance(x, dict) and x.get("month")
                        and (since <= 0 or x["month"] >= since) and (until <= 0 or x["month"] <= until)]
            start = (page - 1) * 5
            items = logs[start : start + 5]
            data.update({"items": items, "page": page, "has_more": start + 5 < len(logs)})
        return {"success": True, "data": data}

    def _search_units(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # 全局查询匹配：全图单位按 filters 字典（名字/关系/境界/宗门/地区/种族/性别）过滤，好感优先前 10
        filters = args.get("filters") or {}
        keyword = filters.get("keyword") or ""
        relation = filters.get("relation")
        realm = filters.get("realm")
        sect = filters.get("sect")
        region = filters.get("region")
        race = filters.get("race")
        sex = filters.get("sex")
        out = []
        for nid, p in self._units.items():
            rel = p.get("player", {}).get("relation")
            intim = p.get("player", {}).get("intim", 0)
            if relation:
                if relation == "好感":
                    pass  # 不加过滤，按好感排即可
                elif rel != relation:
                    continue
            if keyword and keyword not in nid and keyword not in str(p):
                continue
            if realm and realm not in (p.get("realm") or ""):
                continue
            if sect and sect not in (p.get("sect") or ""):
                continue
            if region and region not in (p.get("region") or ""):
                continue
            if race and race != p.get("race"):
                continue
            if sex and sex != p.get("sex"):
                continue
            out.append({"name": nid, "relation": rel or "陌生", "intim": intim, "realm": p.get("realm"), "sect": p.get("sect"), "region": p.get("region")})
        # 好感优先排序，取前 10
        try:
            out.sort(key=lambda x: x.get("intim", 0), reverse=True)
        except Exception:
            pass
        return {"success": True, "data": {"items": out[:10], "total": len(out)}}

    def _query_world(self, args: Dict[str, Any]) -> Dict[str, Any]:
        topic = args.get("topic", "sects")
        if topic == "sects":
            # 真机对应 C# QueryWorldSects（MapBuildSchool 名号组成/存亡被占/主宗-分宗层级/类型宗旨气运）
            sects = self._world.get("sects")
            if sects is None:
                sects = [
                    {"name": "北斗剑宗", "region": "永宁州", "point": {"x": 300, "y": 400},
                     "main_name": "北斗", "branch_name": "剑宗", "is_top": True, "is_hold": False,
                     "type": "剑修", "slogans": [{"slogan": "以剑入道，不问长生"}], "fate": "气运亨通",
                     "member_count": 42, "reputation": 860, "leader": "玄阳子"},
                    {"name": "化神殿", "region": "雷泽", "point": {"x": 520, "y": 90},
                     "main_name": "化神", "branch_name": "殿", "is_top": True, "is_hold": False,
                     "type": "魔道", "slogans": [{"slogan": "神魂至上"}], "fate": "气数衰微",
                     "member_count": 27, "reputation": 410, "enemy": "北斗剑宗"},
                    {"name": "七星阁", "region": "白源区", "point": {"x": 80, "y": 610},
                     "main_name": "七星", "branch_name": "阁", "is_top": True, "is_hold": False,
                     "type": "中立", "slogans": [{"slogan": "观星望气，不涉纷争"}], "fate": "气运平顺",
                     "member_count": 19, "reputation": 300},
                ]
            return {"success": True, "data": {"sects": sects, "total": len(sects)}}
        if topic == "region":
            return {"success": True, "data": {"areas": self._world.get("areas", ["白源区", "永宁州", "雷泽"])}}
        if topic == "events":
            # count 由模型决定（默认 12 条）；真机对应 C# QueryWorldEvents(args["count"])
            ev = list(self._world.get("events", []))
            n = max(1, min(120, int(args.get("count", 12) or 12)))
            ev = ev[:n]
            return {"success": True, "data": {"events": ev, "count": len(ev)}}
        if topic == "places":
            # 可去地点（cat 分类+区域+坐标）；真机对应 C# QueryWorldPlaces(region, cat)
            # cat 省略/全部=城镇+宗门（旧语义）；突破材料/器灵材料=建物+事件双链按小地图分类号采集
            cat = (args.get("cat") or "").strip()
            places = self._world.get("places") or StubGameBridge._DEFAULT_PLACES
            if cat and cat != "全部":
                places = [p for p in places if p.get("cat") == cat]
            return {"success": True, "data": {"places": places, "total": len(places),
                                              **({"cat": cat} if cat and cat != "全部" else {})}}
        if topic == "rankings":
            return self._rankings(args)
        return {"success": True, "data": self._world}

    def _rankings(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Stub：按预置档案指标排序；真机对应 C# QueryWorldRankings（全图扫描四榜）
        board = args.get("board", "power")
        top = int(args.get("top", 10))
        if top < 1:
            top = 1
        if top > 200:
            top = 200
        key = {"power": "power", "reputation": "reputation", "beauty": "beauty", "money": "money"}.get(board)
        if key is None:
            return {"success": False, "error": f"未知榜单 {board}，可选 power/reputation/beauty/money"}
        rows = []
        for nid, p in self._units.items():
            rows.append({
                "name": nid,
                "score": p.get(key, 0),
                "realm": p.get("realm"),
                "sect": p.get("sect"),
            })
        rows.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {"success": True, "data": {"board": board, "items": rows[:top], "total": len(rows)}}

    def _action_stub(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        # 动作恒为 NPC→玩家，actor 取 initiator（或兼容旧 target），victim 恒为玩家；tgt 取 actor 档案供校验
        actor_name = args.get("initiator") or ""
        tgt = self._units.get(actor_name, {})
        # 目标档案缺省按同格处理（未 seed 的兜底不误伤）；有档案则按其真实 same_grid 判定
        same = True if not tgt else tgt.get("player", {}).get("same_grid", True)
        need = self._FACE_TO_FACE_OPS.get(name)
        op = args.get("op")
        blocked = need == "all" or (isinstance(need, tuple) and op in need)
        if blocked and not same:
            return {
                "success": False,
                "error": f"{name}({op}) 需与你同处一地才能进行，当前与你异地相隔（神识传音），无法当面实施",
            }
        if name == "social_relation":
            return self._social_relation(args, tgt)
        if name == "economy_item":
            return self._economy_item(args, tgt)
        if name == "movement":
            return self._movement(args, tgt)
        if name == "item_acquire":
            return self._item_acquire(args, tgt)
        return {"success": True, "data": {"tool": name, "args": args, "stub": True}}

    def _item_acquire(self, args: Dict[str, Any], tgt: Dict[str, Any]) -> Dict[str, Any]:
        # NPC→玩家 偷/讨，actor 恒为 initiator(NPC)
        op = args.get("op", "")
        initiator = args.get("initiator") or ""
        item_name = args.get("item_name", "")
        count = max(1, int(args.get("count", 1)))
        if op not in ("steal_item", "ask_for"):
            return {"success": False, "error": f"unsupported item_acquire op: {op}"}
        if not item_name:
            return {"success": False, "error": "缺少 item_name（道具中文名），可先 inspect_unit(classes=inventory)"}
        if op == "steal_item":
            # 官方 UnitActionRoleStealItem(toUnit, propsSoleID) 无数量参数，恒为整栈偷取——count 不参与（与真机 C# 端同口径）
            return {"success": True, "data": {"op": op, "target": initiator, "item_name": item_name,
                                              "stack_count": 1}}
        return {"success": True, "data": {"op": op, "target": initiator, "item_name": item_name,
                                          "count": count, "pending": True}}

    def _movement(self, args: Dict[str, Any], tgt: Dict[str, Any]) -> Dict[str, Any]:
        # NPC 位移，actor=initiator
        op = args.get("op", "")
        initiator = args.get("initiator") or ""
        if op == "travel":
            # NPC 前往指定城镇/宗门；真机对应 C# ResolveBuild（名精确>包含，region 消歧）+ UnitActionMoveNPC(任意点)
            dest = (args.get("destination") or "").strip()
            region = (args.get("region") or "").strip()
            if not dest:
                return {"success": False, "error": "缺少 destination（可先 query_world topic=places 查看可去地点）"}
            places = self._world.get("places") or StubGameBridge._DEFAULT_PLACES
            hit = next((p for p in places if p.get("name") == dest and (not region or region in p.get("region", ""))), None)
            if places and hit is None:
                hit = next((p for p in places if dest in p.get("name", "") or p.get("name", "") in dest), None)
            if places and hit is None:
                return {"success": False, "error": f"未找到地点「{dest}」，可先 query_world topic=places 查看可去的城镇/宗门名"}
            where = dest + (f"（{region}）" if region else "")
            return {"success": True, "data": {"op": op, "target": initiator, "destination": dest,
                                              "region": region or (hit or {}).get("region", ""),
                                              "moved": True}}
        if op not in ("summon", "teleport"):
            return {"success": False, "error": f"unsupported movement op: {op}"}
        same = True if not tgt else tgt.get("player", {}).get("same_grid", True)
        if same:
            return {"success": True, "data": {"op": op, "target": initiator}}
        return {"success": True, "data": {"op": op, "target": initiator, "moved": True}}

    def _economy_item(self, args: Dict[str, Any], tgt: Dict[str, Any]) -> Dict[str, Any]:
        # NPC→玩家 赠送，actor=initiator
        initiator = args.get("initiator") or ""
        via = args.get("via", "direct")
        items = args.get("items") or []
        # 兼容旧 item_name 单参
        if not items and args.get("item_name"):
            items = [{"item_name": args["item_name"], "count": int(args.get("count", 1))}]
        if not items:
            return {"success": False, "error": "缺少 items（[{item_name, count}, ...]），可先 inspect_unit(classes=inventory)"}

        bag = (tgt.get("items") or []) if tgt else []  # 形如 "灵石1000"/"丹药5"

        def bag_have(name: str) -> int:
            have = 0
            for it in bag:
                if it.startswith(name):
                    digits = "".join(ch for ch in it[len(name):] if ch.isdigit())
                    have += int(digits) if digits else 1
            return have

        results: List[Dict[str, Any]] = []
        money_total = 0
        props_gave = 0
        any_gave = False
        for it in items:
            item_name = it.get("item_name", "")
            count = max(1, int(it.get("count", 1)))
            if not item_name:
                continue
            if "灵石" in item_name:
                have = bag_have("灵石")
                actual = min(count, have)
                if actual <= 0:
                    results.append({"item": item_name, "requested": count, "actual": 0, "error": f"灵石不足（持有 {have}）"})
                else:
                    money_total += actual
                    any_gave = True
                    results.append({"item": item_name, "requested": count, "actual": actual, "mode": "money"})
            else:
                have = bag_have(item_name)
                actual = min(count, have)
                if actual <= 0:
                    results.append({"item": item_name, "requested": count, "actual": 0, "error": "背包中没有，可先 inspect_unit(classes=inventory)"})
                else:
                    props_gave += actual
                    any_gave = True
                    results.append({"item": item_name, "requested": count, "actual": actual, "mode": "props"})

        if not any_gave:
            return {"success": False, "error": "所有赠送项均未达成（详见 items.error）", "data": {"items": results}}

        # 纯数据形态（对齐真机 C#）：叙述由 Python 润色层组装
        return {"success": True, "data": {"op": "give_item", "target": initiator, "items": results,
                                          "via": via, "accepted": True, "refused_count": 0,
                                          "same_grid": bool(tgt) and tgt.get("player", {}).get("same_grid", True)}}

    def _social_relation(self, args: Dict[str, Any], tgt: Dict[str, Any]) -> Dict[str, Any]:
        # NPC→玩家 关系，actor=initiator
        op = args.get("op", "")
        initiator = args.get("initiator") or ""
        rel = tgt.get("player", {}) if tgt else {}
        intim = rel.get("intim", 0)
        cur = rel.get("relation", "陌生")

        if op in ("add_intim", "reduce_intim"):
            delta = max(1, min(10, int(args.get("value", 5))))
            rel["intim"] = intim + delta if op == "add_intim" else max(0, intim - delta)
            return {"success": True, "data": {"op": op, "target": initiator, "requested": delta,
                                              "actual_delta": delta, "intim_before": intim,
                                              "current_intim": rel["intim"]}}

        # 建立关系状态机：op -> (前置关系, 好感门槛, 成功后关系)
        establish = {
            "jie_yuan": (None, 180, "道侣"),
            "marry": ("道侣", None, "夫妻"),
            "bai_shi": (None, None, "师徒"),
            "shou_tu": (None, None, "师徒"),
            "ren_yi_fu_mu": (None, None, "义父母"),
            "jie_yi": (None, None, "结义"),
        }
        if op in establish:
            need, intim_th, after = establish[op]
            if cur == after:
                return {"success": False, "error": f"与{initiator}已是{after}，无需重复建立"}
            if need and cur != need:
                return {"success": False, "error": f"需先与{initiator}{need}，当前{cur}"}
            if intim_th and intim < intim_th:
                return {"success": False, "error": f"好感不足，需≥{intim_th}，当前{intim}", "current_intim": intim}
            rel["relation"] = after
            return {"success": True, "data": {"op": op, "target": initiator, "relation": after}}

        # 解除关系状态机：op -> (前置关系, 解除后关系)
        break_ops = {
            "jie_chu_jie_yuan": ("道侣", "前道侣"),
            "divorce": ("夫妻", "前夫妻"),
            "jie_chu_bai_shi": ("师徒", "前师徒"),
            "jie_chu_shou_tu": ("师徒", "前师徒"),
            "jie_chu_jie_yi": ("结义", "前结义"),
        }
        if op in break_ops:
            need, after = break_ops[op]
            if cur != need:
                return {"success": False, "error": f"与{initiator}并非{need}，当前{cur}"}
            rel["relation"] = after
            return {"success": True, "data": {"op": op, "target": initiator, "relation": after}}

        return {"success": False, "error": f"unknown social_relation op: {op}"}


# ---------- Ws：真机单 WebSocket 全双工 ----------
class WsGameBridge:
    """真机桥：经 WsServer 单连接与 C# 全双工互通（替代 HttpGameBridge）。

    GameBridge 协议不变：get_context / call_tool。C# 未连接/失败 → 兜底文本，
    保证对话不断（对齐原 HttpGameBridge 的容错语义）。
    """

    def __init__(self, ws: Any):
        self.ws = ws

    async def get_context(self, npc_id: str) -> Dict[str, Any]:
        res = await self.ws.request("get_context", {"npc_id": npc_id})
        if isinstance(res, dict) and res.get("success"):
            data = res.get("data") or {}
            raw = data.get("raw") or {}
            if not raw:
                # 桥通但快照为空：模型将看不到人物状态（不是错误，但会让回答"变哑"）
                log.warning("L1 快照为空（npc=%s）：模型本回合看不到气运/好感/心情", npc_id)
            return {
                "text": data.get("text") or f"自身：{npc_id}（无快照）",
                "raw": raw,
            }
        err = res.get("error") if isinstance(res, dict) else "unknown"
        log.warning("L1 快照获取失败（npc=%s）：%s —— 本回合按无快照继续", npc_id, err)
        return {
            "text": f"自身：{npc_id}（桥未连接/取快照失败）",
            "raw": {"error": err},
        }

    async def call_tool(self, name: str, arguments: Dict[str, Any], npc_id: Optional[str] = None) -> Dict[str, Any]:
        """`npc_id` 随帧带上（09-13）：C# 用它判"这次动作属于哪个 NPC"——同格动作完成时自动开窗。"""
        return await self.ws.request("call_tool", {"name": name, "arguments": arguments, "npc_id": npc_id})
