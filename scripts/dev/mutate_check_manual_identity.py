#!/usr/bin/env python3
"""变异验证 —— 手动好友「精确身份」（contacts.json 的 unit_id）的三层测试（09-16）。

**为什么必须有这个脚本**：本仓库吃过恒真断言的亏。所以"新增测试"的验收标准不是"跑绿了"，
而是"**把修复逐个回退，对应的测试真的变红**"。这个脚本就是那条验收线；留着它，下次动
`contacts_store` / `ContactStore` / `ContactDuty` 的这条链时能一条命令重跑。

三层各锁一段，缺一不可：
  · Python 行为（`tests/test_contacts_store.py` / `test_contacts_rpc.py`）—— 落盘往返、
    幂等语义（补空 + 纠错）、老格式兼容、空值/超长边界。
  · C# 真单测（`csharp/tests/ManualIdentityTests`）—— `ContactStore` 的镜像规则。
    它是零 Unity 依赖的纯托管层，所以能在 WSL 里用 dotnet 直接跑。
  · Python 源码契约（`tests/test_manual_identity_contract.py`）—— 协议形状与写侧铁律
    （"这层不许出现 UnitLookup"）。

用法：
    python3 scripts/dev/mutate_check_manual_identity.py          # 全跑
    python3 scripts/dev/mutate_check_manual_identity.py py       # 只跑 Python 行为层
    python3 scripts/dev/mutate_check_manual_identity.py cs       # 只跑 C# 单测层
    python3 scripts/dev/mutate_check_manual_identity.py contract # 只跑源码契约层

注意：会**临时改源码再还原**（改前先把原字节读进内存），跑完会做逐字节还原校验，
末行必须是"全部变异都被捕获"。

⚠ **跑之前确认没有别的会话/编辑器正在改这些源码**（09-17 实发：并发会话在跑的过程中改了
  `csharp/UI/ChatWindow.cs`，脚本末了的"逐字节还原"把**别人的改动一起吃掉了** ——
  末行的"★还原校验失败（已用内存备份覆盖）"就是这件事的告警，看到它必须去确认丢了什么）。
  脚本按设计就是"读原字节 → 变异 → 还原"，它保护的是自己的变异，不是别人的编辑。

★ 脚本先**自检**：注入一个必然被发现的错（把 add_contact 整个变成 no-op）。若这条都没报红，
  说明脚手架本身坏了（路径/命令不对），后面所有"被抓到"的结论都不可信 —— 直接中止。
  随后还有一道**基线自检**：每个要用到的层先在**未变异**树上跑一遍，必须全绿。因为判定规则是
  "这一层红了就算抓到"，任何**与变异无关**的红（环境缺 dotnet / 并发会话正好在改源码 /
  测试本来就挂着）都会被记成 ✅ —— 09-17 吃到过一次假绿（`登记表换存档不重置` 那条在当时的
  断言集下根本抓不到，却被记成抓到；事后单独复现才确认）。
"""

from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）


import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE_PY = ROOT / "storage" / "contacts_store.py"
WS_PY = ROOT / "transport" / "ws_channel.py"
ROOM_PY = ROOT / "room" / "room.py"
ROOM_FLOW_PY = ROOT / "room" / "room_flow.py"
ROOM_HISTORY_PY = ROOT / "room" / "room_history.py"
STORE_CS = ROOT / "csharp/contacts/ContactStore.cs"
DUTY_CS = ROOT / "csharp/contacts/ContactDuty.cs"
LAUNCHER_CS = ROOT / "csharp/npc/ChatLauncher.cs"
CONTACT_PANEL_CS = ROOT / "csharp/UI/ContactPresenter.cs"
ADD_CONTACT_CS = ROOT / "csharp/UI/NpcPanelAddContact.cs"
CHAT_PRESENTER_CS = ROOT / "csharp/UI/ChatPresenter.cs"
CHAT_WINDOW_CS = ROOT / "csharp/UI/ChatWindow.cs"
ROW_AVATARS_CS = ROOT / "csharp/UI/RowAvatars.cs"
TOOL_EXECUTOR_CS = ROOT / "csharp/tools/ToolExecutor.cs"
UNIT_ID_REGISTRY_CS = ROOT / "csharp/game/UnitIdRegistry.cs"
UNIT_ID_REGISTRY_CORE_CS = ROOT / "csharp/game/UnitIdRegistryCore.cs"
UNIT_SNAPSHOT_CS = ROOT / "csharp/game/UnitSnapshot.cs"
UNIT_REFS_PY = ROOT / "tools/unit_refs.py"
UNIT_ID_MAP_PY = ROOT / "storage" / "unit_id_map.py"
AGENT_LOOP_PY = ROOT / "agent_loop.py"
MODMAIN_CS = ROOT / "csharp/app/ModMain.cs"
DIALOGUE_AGENT_PY = ROOT / "agent" / "dialogue_agent.py"
INITIATIVE_CS = ROOT / "csharp/npc/NpcInitiativeMonitor.cs"

PY_BEHAVIOR = ["tests/test_contacts_store.py", "tests/test_contacts_rpc.py"]
PY_CONTRACT = ["tests/test_manual_identity_contract.py"]
# ★ 09-17 新增一条**更宽**的契约层：本轮把身份从"群回执灌全局表"改成"随帧携带"，
#   而新不变量分散在几个既有契约文件里（立绘链 / 工具解析 / 登记表 / 同席取数）。
#   单跑 `test_manual_identity_contract.py` 抓不到它们 —— 那会让下面几条变异**假绿**。
#   所以另开一层：只给"钉新通道"的那几条用例用，其余用例仍走快的 `contract`。
PY_CONTRACT_ALL = ["tests/test_manual_identity_contract.py",
                   "tests/test_realm_and_identity_contract.py",
                   "tests/test_unit_id_contract.py",
                   "tests/test_chat_ui_contract.py",
                   "tests/test_tool_contract_consistency.py",
                   "tests/test_l1_multi_npc.py",
                   # 09-17：人名引用层（唯一化显示名 / 注入 / 映射表落盘）自己一个文件
                   "tests/test_unit_refs.py"]
PY_ROOM = ["tests/test_room_member_identity.py"]
# C# 层跑**两个**工程：`ManualIdentityTests`（ContactStore 镜像）+ `UnitIdRegistryTests`
# （登记表纯逻辑）。09-17 加后者 —— 本轮的身份落点从内存字典搬到了落盘登记表，
# 只跑前者的话，登记表的变异（跨档污染 / 覆盖写 / 自愈误删）会全部**假绿**。
CS_PROJS = ["ManualIdentityTests", "UnitIdRegistryTests"]


def _env() -> dict:
    return {**os.environ, "PYTHONPATH": str(ROOT.parent)}


def run_pytest(files) -> int:
    """跑若干测试文件。**cwd 必须是 `ROOT.parent`、路径必须带 `agent_loop/` 前缀**。

    ★ 09-17 修的第二个（更隐蔽的）假绿：原来这里是 `cwd=ROOT` + `tests/xxx.py`，而
      `python -m pytest` 会把 **cwd 塞进 `sys.path[0]`** ⇒ `import agent_loop` 命中的是
      **同名外壳文件** `agent_loop.py`（`from .room import Room` 当场炸）而不是包目录。
      于是凡是 import 包的那些测试文件（`test_contacts_store` / `test_contacts_rpc` /
      `test_room_member_identity` / `test_l1_multi_npc` / `test_unit_id_contract`）**在收集阶段
      就红**，而判定规则是"这一层红了就算抓到" ⇒ 整层变异被记成 ✅（含脚手架自检，它也被骗过）。
      只读源码、不 import 的 `contract` 层反而不受影响 —— 所以只有它一直是真跑的。
      现在改为在父目录跑（`PYTHONPATH` 本来就是父目录），四层基线全绿才继续。
    """
    return _mutate_lib.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         *[ROOT.name + "/" + f for f in files]],
        cwd=str(ROOT.parent), capture_output=True, text=True, env=_env()).returncode


def run_cs() -> int:
    """跑**全部** C# 身份相关工程；任一失败即算"抓到"。"""
    for proj in CS_PROJS:
        rc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/dev/run_csharp_tests.py"), "--only", proj, "--quiet"],
            cwd=str(ROOT), capture_output=True, text=True, env=_env()).returncode
        if rc != 0:
            return rc
    return 0


# (名字, 文件, old, new, 跑哪一层)
MUTATIONS = [
    # ---------------- Python 行为层 ----------------
    ("补写时顺手刷了 added_at",
     STORE_PY,
     '                c["unit_id"] = uid\n                self._save(data)',
     '                c["unit_id"] = uid\n                c["added_at"] = int(time.time())\n                self._save(data)',
     "py"),
    ("无条件写 unit_id（不知道也写空串）",
     STORE_PY,
     '        rec = {"npc_id": npc_id, "added_at": int(time.time()), "source": "manual"}\n'
     '        if uid:\n'
     '            # 没有就**不写这个键** —— 保持老记录的字面形状，读侧据此判断"没有精确身份"\n'
     '            rec["unit_id"] = uid\n',
     '        rec = {"npc_id": npc_id, "added_at": int(time.time()), "source": "manual"}\n'
     '        rec["unit_id"] = uid or ""\n',
     "py"),
    ("只补空、不纠错（旧 id 永不更新）",
     STORE_PY,
     '            if uid and c.get("unit_id") != uid:',
     '            if uid and not c.get("unit_id"):',
     "py"),
    ("去掉 unit_id 长度校验",
     STORE_PY,
     '    if len(s) > 64:\n        raise ValueError("unit_id 过长")\n    return s',
     '    return s',
     "py"),
    ("空 unit_id 当成错误抛异常（老 DLL 会当场炸）",
     STORE_PY,
     '    s = str(unit_id or "").strip()\n    if not s:\n        return None',
     '    s = str(unit_id or "").strip()\n    if not s:\n        raise ValueError("unit_id 不能为空")',
     "py"),
    ("add_contact 完全忽略 unit_id（改回老实现）",
     STORE_PY,
     '        uid = _clean_unit_id(unit_id)',
     '        uid = None',
     "py"),
    ("ws 路由丢掉 unit_id（不透传）",
     WS_PY,
     '            unit_id = str(p.get("unit_id", "") or "")\n'
     '            return self._contact_service.add_contact(npc_id, unit_id or None)',
     '            return self._contact_service.add_contact(npc_id)',
     "py"),

    # ---------------- C# 真单测层 ----------------
    ("整体替换时只清名字、不清 unit_id（幽灵身份）",
     STORE_CS,
     '            _manualNames.Clear();\n            _manualUnitIds.Clear();',
     '            _manualNames.Clear();',
     "cs"),
    ("移除好友时不清 unit_id",
     STORE_CS,
     '            else if (_manualUnitIds.Remove(name)) changed = true;   // 移除好友 → 精确表同步清',
     '            else { }',
     "cs"),
    ("不带 unitId 也强行赋值（抹掉已有的精确身份）",
     STORE_CS,
     '                if (!string.IsNullOrEmpty(unitId) && ManualUnitId(name) != unitId)',
     '                if (ManualUnitId(name) != unitId)',
     "cs"),
    ("ManualUnitId 查不到时返回空串而非 null",
     STORE_CS,
     '            return _manualUnitIds.TryGetValue(name, out id) ? id : null;',
     '            return _manualUnitIds.TryGetValue(name, out id) ? id : "";',
     "cs"),

    # ---------------- Python 源码契约层 ----------------
    ("无条件把 unit_id 塞进请求体",
     DUTY_CS,
     '            if (!string.IsNullOrEmpty(unitId)) req["unit_id"] = unitId;',
     '            req["unit_id"] = unitId ?? "";',
     "contract"),
    ("解析老回包时直接索引 unit_id（会崩整条名单）",
     DUTY_CS,
     '                        string uid = t?["unit_id"]?.ToString() ?? "";   // 老记录没有此键 → ""',
     '                        string uid = t["unit_id"].ToString();',
     "contract"),
    ("回填通道被删掉",
     DUTY_CS,
     '        public static void BackfillManualUnitId(string name, string unitId)',
     '        public static void BackfillManualUnitId_DISABLED(string name, string unitId)',
     "contract"),
    ("SetManualNames 签名退回纯名字",
     STORE_CS,
     'public static void SetManualNames(IEnumerable<KeyValuePair<string, string>> items)',
     'public static void SetManualNames(IEnumerable<string> items)',
     "contract"),
    ("ContactStore 自己按名解析（把猜测洗成权威）",
     STORE_CS,
     '            if (string.IsNullOrEmpty(name)) return null;\n'
     '            string id;\n'
     '            return _manualUnitIds.TryGetValue(name, out id) ? id : null;',
     '            if (string.IsNullOrEmpty(name)) return null;\n'
     '            string id;\n'
     '            if (_manualUnitIds.TryGetValue(name, out id)) return id;\n'
     '            try { return UnitLookup.Resolve(name).data.unitData.unitID; } catch { return null; }',
     "contract"),

    # ================= 第 2 步：消费层（把存下来的身份用起来）=================
    ("通讯录条目改回只按名解析（精确身份存了没人读）",
     CONTACT_PANEL_CS,
     '                var byId = UnitLookup.ResolveEx(e.Name, id, out _);',
     '                var byId = UnitLookup.Resolve(e.Name);',
     "contract"),
    ("点开单聊时不告诉 ChatLauncher「这是猜的」",
     CONTACT_PANEL_CS,
     'ChatLauncher.OpenForUnit(unit, guessed: !exact);',
     'ChatLauncher.OpenForUnit(unit);',
     "contract"),
    ("去掉 !guessed 守卫（猜的结果照进登记表）",
     LAUNCHER_CS,
     '            if (!guessed)\n            {',
     '            if (true)\n            {',
     "contract"),
    ("加好友按钮只传名字、丢掉真身的 unitID",
     ADD_CONTACT_CS,
     '                ContactDuty.SetManualContact(name, adding, unitId);',
     '                ContactDuty.SetManualContact(name, adding);',
     "contract"),
    ("回填不检查「是不是手动好友」（对所有人发 RPC）",
     LAUNCHER_CS,
     '            if (!ContactStore.IsManualContact(name)) return;',
     '            if (false) return;',
     "contract"),
    ("建群/拉人时把**猜来的**身份也交上去",
     CONTACT_PANEL_CS,
     '                if (e == null || !e.Exact || string.IsNullOrEmpty(e.UnitId)) continue;',
     '                if (e == null || string.IsNullOrEmpty(e.UnitId)) continue;',
     "contract"),
    ("主动开口候选集改回只按名解析",
     INITIATIVE_CS,
     '                            u = UnitLookup.ResolveEx(nm, storedId, out _);',
     '                            u = UnitLookup.Resolve(nm);',
     "contract"),

    # ================= 第 3 步：群成员精确身份 =================
    ("Room 落盘时不带成员身份（读档即丢）",
     ROOM_PY,
     '                "member_unit_ids": {m: self.member_unit_ids[m]\n'
     '                                    for m in self.members if m in self.member_unit_ids},\n',
     '',
     "room"),
    ("Room 读档时不读成员身份",
     ROOM_PY,
     '                   member_unit_ids={str(k): str(v) for k, v in uids.items()\n'
     '                                    if str(k) in members and str(v or "").strip()}\n'
     '                          if isinstance(uids, dict) else {},',
     '                   member_unit_ids={},',
     "room"),
    ("add_member 把空身份也写进去（「不知道」变成「空」）",
     ROOM_PY,
     '        if uid:\n            self.member_unit_ids[who] = uid\n',
     '        self.member_unit_ids[who] = uid\n',
     "room"),
    ("踢人时不清身份（重入群沿用旧身份）",
     ROOM_PY,
     '        self.member_unit_ids.pop(who, None)\n',
     '',
     "room"),
    ("群窗历史不带成员身份（C# 永远拿不到）",
     ROOM_HISTORY_PY,
     '        "member_unit_ids": {m: room.member_unit_ids[m]\n'
     '                            for m in room.members if m in room.member_unit_ids},\n',
     '',
     "room"),
    ("open_room 路由丢掉 member_unit_ids",
     WS_PY,
     '                                        member_unit_ids=_unit_id_map(p.get("member_unit_ids")))',
     '                                        member_unit_ids=None)',
     "room"),
    ("add_room_members 路由丢掉 member_unit_ids",
     WS_PY,
     '            uid_map = _unit_id_map(p.get("member_unit_ids"))',
     '            uid_map = {}',
     "room"),
    ("身份表接受数组（错位事故的温床）",
     WS_PY,
     '    if not isinstance(raw, dict):\n        return {}',
     '    if isinstance(raw, list):\n        return {str(i): str(v) for i, v in enumerate(raw)}\n'
     '    if not isinstance(raw, dict):\n        return {}',
     "room"),
    ("重开老群时不补写成员身份",
     ROOM_FLOW_PY,
     '                if fresh and room.member_unit_ids.get(m) != fresh:',
     '                if False:',
     "room"),
    # ★ 09-17：登记从"群回执灌全局表"改为**随帧携带**，下面两条跟着换成新锚点。
    #   它们钉的仍是同一件事 —— **群成员身份必须到达逐行取图那一步**（缺了就是画错人/不画脸）。
    ("room_say 不带说话人精确 id（逐行头像退回按名解析）",
     CHAT_PRESENTER_CS,
     '                            ApplyRoomSay(f.Uid, f.Speaker, f.Text, f.SpeakerId);',
     '                            ApplyRoomSay(f.Uid, f.Speaker, f.Text);',
     "wide"),
    ("room_state 不带说话人精确 id（名字与 id 分两次写 ⇒ 名字对、脸是别人的）",
     CHAT_PRESENTER_CS,
     '                _window.SetRoomSpeaker(who, whoId);',
     '                _window.SetRoomSpeaker(who);',
     "wide"),
    ("回放不读 speaker_id（重开窗那一刻 C# 手上未必还有群身份）",
     CHAT_WINDOW_CS,
     '                            string whoId = (string)o["speaker_id"] ?? "";',
     '                            string whoId = "";',
     "wide"),
    ("RowAvatars 给了精确 id 却仍去按名解析（保留了重名画错人的机会）",
     ROW_AVATARS_CS,
     '            string id = !string.IsNullOrEmpty(speakerId) ? speakerId : PortraitCache.IdByName(speakerName);',
     '            string id = PortraitCache.IdByName(speakerName);',
     "wide"),
    ("发起方不走精确解析（5 个动作工具的 actor 全按名猜）",
     TOOL_EXECUTOR_CS,
     '            return ResolveArgExact(args, "initiator", initiator);',
     '            return UnitLookup.Resolve(initiator);',
     "wide"),
    ("对手方不走精确解析（target_id 读了不用）",
     TOOL_EXECUTOR_CS,
     '            var u = ResolveArgExact(args, "target", targetName);',
     '            var u = UnitLookup.Resolve(targetName);',
     "wide"),
    # ★ 09-17：边界翻译从群闸门搬到了 `DialogueAgent._inject_person_ids`（群/单聊同一入口），
    #   锚点跟着搬 —— 它钉的仍是同一件事：**模型写的名字必须变成 C# 能用的精确身份**。
    ("指人参数不做身份注入（模型的名字到不了 C# 的精确路径）",
     DIALOGUE_AGENT_PY,
     '            self._inject_person_ids(name, args)',
     '            pass',
     "wide"),
    ("查询结果不收集人名引用（模型查到的那些人指不准）",
     DIALOGUE_AGENT_PY,
     '            self._harvest_ids(name, res)',
     '            pass',
     "wide"),
    ("收集时不写唯一化显示名（渲染与映射表的键对不上）",
     UNIT_REFS_PY,
     '            for i in holders:\n                refs[i][0]["display"] = disp              # ★ 渲染侧优先读它',
     '            for i in holders:\n                pass',
     "wide"),
    ("注入时不拆显示名信封（C# 拿到带括号的假名字）",
     UNIT_REFS_PY,
     '            args[key] = bare                      # 信封拆掉，真名留给下游',
     '            pass',
     "wide"),
    # ★ 09-17 复核补的三条：都是"看起来能用、实际静默错人"的形态，各自的测试单跑能红。
    ("唯一名编号按出现顺序发（换个批次就把编号指到别人身上）",
     UNIT_REFS_PY,
     '            if not bits or disp in used:\n'
     '                disp = _hash_display(bare, ent[1], n, used, reuse, known)',
     '            if not bits or disp in used:\n'
     '                disp = f"{bare}#{n}"',
     "wide"),
    ("模型自带的 <键>_id / initiator 不抹掉（模型可自选精确身份 ⇒ 静默落错人）",
     DIALOGUE_AGENT_PY,
     '                _stripped = unit_refs.strip_reserved_args(args)',
     '                _stripped = []',
     "wide"),
    ("冲突时把显示名记进「已知重名」（该表口径是裸名，显示名永远匹配不上）",
     DIALOGUE_AGENT_PY,
     '                self._map_mark_ambiguous(unit_refs.bare_name(key))',
     '                self._map_mark_ambiguous(key)',
     "wide"),
    ("关系簿取不到名字时拿 unitID 顶替（模型把 id 当人名念）",
     UNIT_SNAPSHOT_CS,
     '                        ["name"] = nm ?? "",',
     '                        ["name"] = string.IsNullOrEmpty(nm) ? idStr : nm,',
     "wide"),
    ("同名多值也登记（重名者被钉成其中一个）",
     UNIT_ID_REGISTRY_CS,
     '                if (dup.Contains(kv.Key)) continue;',
     '',
     "wide"),
    ("查询结果不登记（模型查到的人下一次仍要全图按名扫）",
     TOOL_EXECUTOR_CS,
     '                    try { UnitIdRegistry.RememberObservedFromPayload(res["data"], name); }',
     '                    try { }',
     "wide"),
    ("落盘时不带「已知重名」（下一次又把裸名登记回去）",
     UNIT_ID_MAP_PY,
     '            "ambiguous": sorted(self._ambiguous),',
     '            "ambiguous": [],',
     "wide"),
    ("存档不落人名映射表（重进游戏后显示名解不开）",
     AGENT_LOOP_PY,
     '            nid = self.identity.save()',
     '            nid = 0',
     "wide"),
    ("换档不清人脸映射表（旧档 unitID 当新档权威）",
     UNIT_ID_MAP_PY,
     '        if changed:\n            self._units = {}\n            self._ambiguous = set()',
     '        if changed:\n            pass',
     "wide"),
    ("好感数字按名重解析（同名时报「未变化」、动作其实生效了）",
     TOOL_EXECUTOR_CS,
     '            int intim = GetIntimBetween(wub, player);',
     '            int intim = GetIntimBetween(UnitLookup.Resolve(((string)args["initiator"] ?? "")), player);',
     "wide"),
    ("trade 不校验发起方（替两个第三人做主）",
     TOOL_EXECUTOR_CS,
     '            if (!string.IsNullOrEmpty(actorUid)\n                && actorUid != sellerUid && actorUid != buyerUid)',
     '            if (false)',
     "wide"),
    ("L1 取数不带同席者精确身份（BuildOthers 只能全图按名扫）",
     DIALOGUE_AGENT_PY,
     '                    ctx = self.bridge.get_context(self.id, roster=self.roster,\n'
     '                                                  roster_ids=self.roster_ids,\n'
     '                                                  npc_unit_id=self._unit_id())',
     '                    ctx = self.bridge.get_context(self.id, roster=self.roster)',
     "wide"),
    ("roster 与身份分两次写（换名单后身份残留 = 静默错人）",
     DIALOGUE_AGENT_PY,
     '        src = unit_ids if isinstance(unit_ids, dict) else {}\n'
     '        self.roster_ids = {}',
     '        src = unit_ids if isinstance(unit_ids, dict) else {}\n'
     '        if not src:\n'
     '            return',
     "wide"),
    # ★ 09-17 第二轮：落盘时机从「登记即写」改成「只跟游戏存档」（`_pending` + `Flush`）。
    #   下面四条钉的是这条纪律的四个破口 —— 全部挂 `wide`（钉它们的是
    #   `test_realm_and_identity_contract.py::test_registry_flushes_only_on_game_save`）。
    ("登记表换存档不重置（旧档 unitID 被当成本档的权威）",
     UNIT_ID_REGISTRY_CS,
     '            _loaded = false;\n            _foreign = false;\n            _pending = false;',
     '            _foreign = false;\n            _pending = false;',
     "wide"),
    ("换档不清待写标志（上一个存档的登记落进新档）",
     UNIT_ID_REGISTRY_CS,
     '            _foreign = false;\n            _pending = false;',
     '            _foreign = false;',
     "wide"),
    ("登记时立刻写盘（与对话增量的存档语义脱钩）",
     UNIT_ID_REGISTRY_CS,
     '            if (!_core.Remember(name, unitId)) return;     // 幂等 / 空参：一个字节都不动\n'
     '            _pending = true;',
     '            if (!_core.Remember(name, unitId)) return;\n'
     '            Save();',
     "wide"),
    ("存档时不落盘（登记永远停在内存，重开游戏全丢）",
     MODMAIN_CS,
     '            try { UnitIdRegistry.Flush(); }',
     '            try { }',
     "wide"),
    # ★ 09-17：跨档校验搬进了纯核心（`UnitIdRegistryCore.Load`），所以这一条的锚点从
    #   `UnitIdRegistry.cs` 换到了核心文件、并改挂 `cs` 层（那里能真跑断言）——
    #   见下面「★登记表跨存档不校验」。
    # ---------------- 登记表纯逻辑层（UnitIdRegistryCore，cs 层跑） ----------------
    ("★登记表跨存档不校验（旧档 unitID 被当成本档的权威）",
     UNIT_ID_REGISTRY_CORE_CS,
     '            if (fw.Length == 0 || nw.Length == 0 || fw != nw) return false;',
     '            if (false) return false;',
     "cs"),
    ("★顺带登记覆盖已有值（把真正的握手挤掉）",
     UNIT_ID_REGISTRY_CORE_CS,
     '            if (_byName.ContainsKey(nm)) return false;      // ★ 已有（无论同值与否）一律不动',
     '',
     "cs"),
    ("★登记表拒绝时不清表（上一个存档的条目残留）",
     UNIT_ID_REGISTRY_CORE_CS,
     '            Reset();\n            string fw = (fileWorld ?? "").Trim();',
     '            string fw = (fileWorld ?? "").Trim();',
     "cs"),
    ("登记表 Remember 不幂等（每次开窗都写盘）",
     UNIT_ID_REGISTRY_CORE_CS,
     '            if (hadOld && old == id) return false;          // 幂等',
     '            if (false) return false;',
     "cs"),
    ("登记表换 id 时不摘旧反查（提示指向第一个人）",
     UNIT_ID_REGISTRY_CORE_CS,
     '                if (_byId.TryGetValue(old, out owner) && owner == nm) _byId.Remove(old);',
     '                if (false) _byId.Remove(old);',
     "cs"),
    ("登记表 Forget 不校验旧值（自愈误删刚登记的正确值）",
     UNIT_ID_REGISTRY_CORE_CS,
     '            if (want.Length > 0 && cur != want) return false;   // 已经不是那条了，别误删',
     '            if (false) return false;',
     "cs"),
    ("登记表 Snapshot 返回原表（外部一改就污染登记表）",
     UNIT_ID_REGISTRY_CORE_CS,
     '            return new Dictionary<string, string>(_byName);',
     '            return _byName;',
     "cs"),
    ("登记表接受空 id（「不知道」变成「空」）",
     UNIT_ID_REGISTRY_CORE_CS,
     '            if (nm.Length == 0 || id.Length == 0) return false;\n'
     '            if (_byName.ContainsKey(nm)) return false;      // ★ 已有（无论同值与否）一律不动',
     '            if (nm.Length == 0) return false;\n'
     '            if (_byName.ContainsKey(nm)) return false;      // ★ 已有（无论同值与否）一律不动',
     "cs"),
]

LAYER_RUNNER = {"py": lambda: run_pytest(PY_BEHAVIOR),
                "cs": run_cs,
                "room": lambda: run_pytest(PY_ROOM),
                "contract": lambda: run_pytest(PY_CONTRACT),
                "wide": lambda: run_pytest(PY_CONTRACT_ALL)}


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _apply(path: Path, old: str, new: str) -> bool:
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print("      [跳过] 锚点出现 %d 次（期望 1）—— 源码变了，锚点失效" % n)
        return False
    path.write_text(src.replace(old, new), encoding="utf-8")
    return True


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    cases = [c for c in MUTATIONS if not only or c[4] == only]
    if not cases:
        print("没有匹配的变异（可用层：py / cs / room / contract）")
        return 2

    # ★ 备份集合**必须包含自检注入的目标**（STORE_PY）：自检不挑层、每层都注入，
    #   而 `files` 只由"本次选中的变异"决定 —— 于是 `... contract`（只跑契约层）那次
    #   注入完 STORE_PY 就没人还原它，`add_contact` 的 no-op 直接**泄漏进源码树**，
    #   15 个 contacts 测试跟着红（09-17 实际发生过：`contacts_store.py` 里躺着一行
    #   `return {"added": True, "count": 0}`）。自检目标一律纳入备份/还原与一致性校验。
    files = sorted({c[1] for c in cases} | {STORE_PY})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    backup = {p: p.read_bytes() for p in files}
    before = {p: _md5(p) for p in files}

    def restore_all():
        for p in files:
            p.write_bytes(backup[p])

    print("=== 脚手架自检（注入一个必然被发现的错：add_contact 变 no-op） ===")
    if not _apply(STORE_PY,
                  '        npc_id = _clean(npc_id)\n        uid = _clean_unit_id(unit_id)',
                  '        npc_id = _clean(npc_id)\n        uid = _clean_unit_id(unit_id)\n'
                  '        return {"added": True, "count": 0}'):
        print("自检失败：锚点失效，脚本已过期")
        return 2
    rc = run_pytest(PY_BEHAVIOR)
    restore_all()
    if rc == 0:
        print("★ 自检失败：no-op 注入竟然没报红 → 脚手架不可信，中止")
        return 2
    print("自检通过（no-op 注入被抓到）\n")

    # ── 基线自检：**每个要用到的层，先在未变异树上跑一遍，必须是绿的** ──────────────
    # 为什么必须有：判定规则是"这一层红了就算抓到"，所以**任何与变异无关的红**
    # （环境缺 dotnet、并发会话正好在改文件、测试本来就挂着）都会被记成 ✅ —— 09-17
    # 就吃到过一次假绿：`登记表换存档不重置` 那条在当时的断言集下根本抓不到，却被记成抓到。
    # 基线不绿就直接中止，别让后面几十条"抓到"建立在一个本来就红的层上。
    print("=== 基线自检（未变异树上每层必须先绿） ===")
    for layer in sorted({c[4] for c in cases}):
        rc = LAYER_RUNNER[layer]()
        if rc != 0:
            print("★ 基线不绿 [%s] → 后面所有『抓到』都不可信，中止" % layer)
            return 2
        print("  基线绿 [%s]" % layer)
    print()

    caught = skipped = missed = 0
    for name, path, old, new, layer in cases:
        if not _apply(path, old, new):
            skipped += 1
            continue
        rc = LAYER_RUNNER[layer]()
        path.write_bytes(backup[path])
        if rc != 0:
            caught += 1
            print("  ✅ 抓到 [%s] %s" % (layer, name))
        else:
            missed += 1
            print("  ❌ 漏掉 [%s] %s  ← 这个测试是假绿" % (layer, name))

    bad = [p for p in files if _md5(p) != before[p]]
    restore_all()
    print()
    print("汇总：抓到 %d / 跳过 %d / 漏掉 %d（共 %d 条）" % (caught, skipped, missed, len(cases)))
    if bad:
        print("★ 还原校验失败（已用内存备份覆盖）：" + str(bad))
        return 1
    print("还原校验：全部逐字节一致 ✓")
    if missed or skipped:
        return 1
    print("全部变异都被捕获 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
