"""手动好友「精确身份」（unit_id）的**跨语言契约**。

## 这条链在解决什么

Python 全链路 `npc_id` 是**中文名**，而这游戏「重名是常态不是例外」。C# 侧要拿人名换
`WorldUnitBase` 时，只有在**真的握着一个对象**的时刻才是零歧义的 —— 而每一个手动好友
都必然经过那个时刻：NPC 面板「加好友」按钮的回调里 `panel.unit` 就是本人
（`NpcPanelAddContact.UnitNameOf`）。把那一刻读到的 `unitID` 随 `add_contact` 上行、
落进 `contacts.json`，这个好友从此就有精确身份，不必每次读名单都按名全图猜一遍。

猜的代价不是"慢"，是**静默出错**：`UnitLookup` 文件头记的真机事故里，
`inspect_unit(target="益婉容")` 返回了**另一个益婉容**，不报错、不缺字段。

## ★ 写侧铁律（本文件存在的首要理由）

`unit_id` **只许来自"真实握在手里的对象"**，绝不许把按名解析（`UnitLookup.Resolve`）
的结果回写 —— 那是把一次猜测固化到磁盘、跨会话生效，而且再也看不出它是猜的
（`UnitLookup.Pin` 注释原话："把'猜'的结果固化下来，比不登记更糟"）。
本文件能钉住的是**协议形状**（下面四条）；"接线处有没有违反铁律"要等消费层接上后
在那边钉（回填的两个调用点都必须由 `panel.unit` / 传进来的 `WorldUnitBase` 驱动）。

## 本文件钉的四条（都是"改一行就静默坏掉"的地方）

  ① `ToggleManualContact` 只在**知道**时才把 `unit_id` 放进请求体 —— 无条件塞空串会让
     抓包/日志看起来像"我们知道但值是空的"，是误导。
  ② `RequestManualContacts` 解析必须 `t?["unit_id"]` 容错 —— 本功能上线前的回包
     **根本没有这个键**，假设它一定在会让整条名单解析失败（名字全丢）。
  ③ `BackfillManualUnitId` 这条通道存在、且就挂在 `add_contact` 上。
  ④ `ContactStore` 的镜像里 unit_id 与名字**同生共死**（整体替换时一起清）。

⚠ 诚实边界：镜像自身的规则由真单元测试钉（`csharp/tests/ManualIdentityTests`，33 项断言，
   直接链接 `ContactStore.cs` 编译运行）；这里只做**源码级**契约，防的是"有人把接线改回去"。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "csharp" / "contacts" / "ContactStore.cs"
DUTY = ROOT / "csharp" / "contacts" / "ContactDuty.cs"
LAUNCHER = ROOT / "csharp" / "npc" / "ChatLauncher.cs"
CHAT_PANEL = ROOT / "csharp" / "UI" / "AbChatPanel.cs"
CONTACT_PANEL = ROOT / "csharp" / "UI" / "ContactPresenter.cs"
ADD_CONTACT = ROOT / "csharp" / "UI" / "NpcPanelAddContact.cs"
CHAT_PRESENTER = ROOT / "csharp" / "UI" / "ChatPresenter.cs"
CHAT_WINDOW = ROOT / "csharp" / "UI" / "ChatWindow.cs"
INITIATIVE = ROOT / "csharp" / "npc" / "NpcInitiativeMonitor.cs"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_cs_comments(src: str) -> str:
    """剥 C# 注释。**判位移之前必须剥** —— 注释里会引用"以前是这么写的"，
    拿它当代码会把旧写法误判成还在用（本文件已经踩过一次：
    `NpcInitiativeMonitor` 那句「这一处以前只有 `UnitLookup.Resolve(nm)`」）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _body(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员正文（签名必须唯一）。"""
    assert src.count(signature) == 1, "签名不唯一（%d 次）：%s" % (src.count(signature), signature)
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + signature)


# ---------------------------------------------------------------------------
# ① 只在知道时才上行
# ---------------------------------------------------------------------------

def test_contact_duty_only_sends_unit_id_when_known():
    src = _src(DUTY)
    # 条件式赋值必须在
    assert re.search(r'if\s*\(!string\.IsNullOrEmpty\(unitId\)\)\s*req\["unit_id"\]\s*=\s*unitId\s*;',
                     src), "ToggleManualContact 必须【条件】加 unit_id（不知道就不发这个键）"
    # 初始 JObject 里不许出现 unit_id（那就是无条件上行）
    m = re.search(r"var req = new JObject \{ \[\"npc_id\"\] = name([^}]*)\}", src)
    assert m, "找不到 ToggleManualContact 的请求体构造"
    assert "unit_id" not in m.group(1), "请求体不许无条件塞 unit_id（空串会被当成『知道但值为空』）"


# ---------------------------------------------------------------------------
# ② 老回包没有这个键 —— 解析必须容错
# ---------------------------------------------------------------------------

def test_list_contacts_parsing_tolerates_missing_unit_id():
    src = _src(DUTY)
    assert re.search(r't\?\["unit_id"\]\?\.ToString\(\)\s*\?\?\s*""', src), (
        "RequestManualContacts 必须用 t?[\"unit_id\"]?.ToString() ?? \"\" —— "
        "本功能上线前的回包没有这个键，直接索引会让整条名单解析失败"
    )
    # 名字那一侧同理（老代码就是对的，一起钉住防回退）
    assert re.search(r't\?\["npc_id"\]\?\.ToString\(\)', src)


# ---------------------------------------------------------------------------
# ③ 回填通道
# ---------------------------------------------------------------------------

def test_backfill_channel_exists_and_goes_through_add_contact():
    src = _src(DUTY)
    m = re.search(r"public static void BackfillManualUnitId\(string name, string unitId\)(.*?)\n        \}",
                  src, re.S)
    assert m, "回填通道 BackfillManualUnitId 不存在（消费层的自愈路径全靠它）"
    body = m.group(1)
    assert '"add_contact"' in body, "回填必须走 add_contact（它就是幂等语义里的『补空 + 纠错』）"
    assert re.search(r'\["unit_id"\]\s*=\s*unitId', body), "回填必须把 unitId 放进请求体"
    assert re.search(r'string\.IsNullOrEmpty\(name\)\s*\|\|\s*string\.IsNullOrEmpty\(unitId\)', body), (
        "回填必须对空参直接返回 —— 空 unitId 发上去只会变成一次无意义的往返"
    )


# ---------------------------------------------------------------------------
# ④ 镜像里 unit_id 与名字同生共死
# ---------------------------------------------------------------------------

def test_store_mirrors_unit_id_together_with_names():
    src = _src(STORE)
    assert re.search(r"public static string ManualUnitId\(string name\)", src)
    assert re.search(r"_manualNames\.Clear\(\);\s*\n\s*_manualUnitIds\.Clear\(\);", src), (
        "SetManualNames 必须同时清两个集合 —— 只清名字会让上一位好友的 unitID 以"
        "『幽灵身份』留在表里，名字若被复用就会挂错人"
    )
    assert re.search(r"_manualUnitIds\.Remove\(name\)", src), (
        "移除好友时必须同步清 unit_id"
    )
    # 签名：整体替换收的是 (name, unitId) 键值对，不是纯名字
    assert re.search(r"SetManualNames\(IEnumerable<KeyValuePair<string, string>> items\)", src), (
        "SetManualNames 的签名必须带 unitID —— 退回 IEnumerable<string> 会让镜像永远没有精确身份，"
        "而且不报错（静默退化成按名猜）"
    )


def _code_only(src: str) -> str:
    """去掉注释与 XML 文档行 —— 只留可执行代码。

    必须这么做：`ContactStore` 的类注释里**本来就有**「单位引用一律在使用现场经
    `UnitLookup.Resolve` 现查」这句话（那是它在说"本层不存引用"的理由）。
    直接整文件搜字符串会把这句正话当成违规。
    """
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("//") or s.startswith("///") or s.startswith("*"):
            continue
        out.append(line)
    return "\n".join(out)


def test_store_unit_id_is_never_written_from_a_name_lookup():
    """写侧铁律的**可机检部分**：`ContactStore` 的**代码**里不许出现 `UnitLookup`。

    这一层是"身份的权威镜像"，它只接受调用方交上来的事实。一旦它在内部自己去
    `Resolve(name)` 把结果写进 `_manualUnitIds`，那就是把猜测洗成权威
    （`UnitLookup.Pin` 注释："比不登记更糟"）。目前它是纯托管层，连 UnitLookup 都不该引用。
    """
    code = _code_only(_src(STORE))
    assert "UnitLookup" not in code, (
        "ContactStore 的代码不许引用 UnitLookup —— 它只存『别人告诉我的事实』，绝不自己解析"
    )
    assert "UnityEngine" not in code and "using Unity" not in code, (
        "ContactStore 必须保持零 Unity 依赖（否则 csharp/tests/ManualIdentityTests 编不出来）"
    )


# ===========================================================================
# 以下为「消费层」与「群成员身份」（第 2、3 步）的契约。
#
# 第 1 步只做到"能存"。这一步把存下来的身份**用起来**，共 3 个读点 + 2 个写点，
# 外加让群成员名单也带上身份。每一条都配了变异检查，别只看着像对的。
# ===========================================================================


def test_consumers_resolve_exact_before_name():
    """三个读点都必须**先精确、后按名**。

    这一条是整件事的落点：`contacts.json` 里存了 unitID 却没人读，等于没做。
    三个消费方各自的错法不一样，但症状同源（静默错人）：
      · 通讯录行条目 → 显示别人的宗门/境界/好感；
      · 点开单聊     → 会话对（按名），但**头像与 L1 自身段是别人的**；
      · 主动开口候选 → **你没加过的同名者会来找你说话**，你加的那个永远沉默。
    """
    cp = _src(CONTACT_PANEL)
    helper = _body(cp, "private static bool ResolveEntry(ContactEntry e, out WorldUnitBase unit)")
    i_exact = helper.index("UnitLookup.ResolveEx(e.Name, id")
    i_name = helper.index("UnitLookup.Resolve(e.Name)")
    assert i_exact < i_name, "ResolveEntry 里精确解析必须排在按名兜底之前"
    # 两个读点都要走这个 helper（不再各自 `Resolve(name)`）
    for sig in ("private void OpenChatFor(ContactEntry e)",
                "private void MergeManualIntoContacts()"):
        assert "ResolveEntry(" in _body(cp, sig), sig + " 没有走两级解析"
    # 候选集那一处在另一个文件里，是内联的两级
    ini = _strip_cs_comments(_src(INITIATIVE))
    i2_exact = ini.index("UnitLookup.ResolveEx(nm, storedId")
    i2_name = ini.index("UnitLookup.Resolve(nm)")
    assert i2_exact < i2_name, "NpcInitiativeMonitor 的手动好友候选也必须先精确后按名"
    assert "ContactStore.ManualUnitId(nm)" in ini, "候选集要拿得到存下来的精确身份"


def test_guessed_resolution_is_never_registered():
    """★ 写侧铁律的**开关那一半**：按名猜出来的人，绝不许进登记表。

    `UnitIdRegistry` 的 ⓪ 分支排在**全图扫描之前** —— 登记错一次，所有按名查这个人的
    地方（L1 自身段、工具查人、候选集）全部挂到别人身上，且不报错、不缺字段。
    `Remember` 的注释原话："按名查出来的结果永远不许写进来"。

    ★ 09-17：登记落点从 `UnitLookup.Pin`（内存）改为 `UnitIdRegistry.Remember`（落盘）。
    **守卫条件一字未变** —— 变的只是"写去哪里"，而"谁有资格写"这条纪律反而更严了
    （落盘后错误会跨会话存活）。
    """
    cp = _src(CONTACT_PANEL)
    body = _body(cp, "private void OpenChatFor(ContactEntry e)")
    assert re.search(r"bool\s+exact\s*=\s*ResolveEntry\(", body), \
        "OpenChatFor 必须接住 ResolveEntry 的\"是否精确\"返回值"
    assert re.search(r"ChatLauncher\.OpenForUnit\(\s*unit\s*,\s*guessed:\s*!\s*exact\s*\)", body), \
        "猜来的人必须带 guessed:true 交给 ChatLauncher"

    lc = _src(LAUNCHER)
    lb = _body(lc, "public static bool OpenForUnit(WorldUnitBase unit, bool guessed = false)")
    assert re.search(r"if\s*\(!\s*guessed\s*\)", lb), "ChatLauncher 必须有 !guessed 守卫"
    # 守卫必须同时罩住「登记」与「回填」：两个都是"把事实写进权威层"的动作
    i_guard = lb.index("if (!guessed)")
    assert i_guard < lb.index("UnitIdRegistry.Remember(") < lb.index("BackfillManualIdentity("), \
        "登记与回填都必须在 !guessed 里面（猜的结果不许写进权威层）"
    # AB 兜底路径同样要守卫（它是第二条登记的地方）
    ab = _src(CHAT_PANEL)
    assert re.search(r"if\s*\(!\s*guessed\s*\)\s*\{\s*try\s*\{\s*UnitIdRegistry\.Remember\(", ab), \
        "AbChatPanel 的兜底登记同样要被 !guessed 罩住"


def test_add_contact_button_hands_over_the_real_unit_id():
    """加好友按钮是**唯一**的加好友入口，也是那一刻唯一握着真身的地方。

    只传名字 = 这个好友的精确身份永远不会被记下来（老洞的根因）。
    """
    src = _src(ADD_CONTACT)
    body = _body(src, "private static void ToggleContact(UINPCInfo panel, GameObject visual)")
    assert "panel.unit" in body, "必须从 panel.unit 取真身"
    assert re.search(r"panel\.unit\.data\.unitData\.unitID", body), \
        "必须把真身的 unitID 读出来"
    assert re.search(r"ContactDuty\.SetManualContact\(name,\s*adding,\s*unitId\)", body)
    assert re.search(r"ContactDuty\.ToggleManualContact\(name,\s*adding,\s*unitId\)", body), \
        "unitID 必须随 RPC 上行（本地镜像 + Python 落盘两边都要）"
    assert re.search(r"ContactDuty\.ToggleManualContact\(name,\s*adding\)", src) is None, \
        "还有漏传 unitId 的旧调用点"


def test_backfill_is_only_reachable_inside_the_not_guessed_branch():
    """回填 = 把 unitID 写进磁盘。**只有手里真握着真身时才许做。**

    这是整个写侧铁律里后果最重的一处：写错了会跨会话固化，而且再也看不出它是猜的。
    """
    src = _src(LAUNCHER)
    body = _body(src, "public static bool OpenForUnit(WorldUnitBase unit, bool guessed = false)")
    assert re.search(r"if\s*\(!\s*guessed\s*\)", body)
    # 两个写动作都在守卫块内（用位移判定：守卫之后、else 分支之前）
    i_guard = body.index("if (!guessed)")
    i_else = body.index("else", i_guard)
    assert i_guard < body.index("BackfillManualIdentity(") < i_else, \
        "回填必须在 !guessed 块内、else 之前"
    # 回填自身还有三道闸：空参 / 不是手动好友 / 库里已经是这个 ID
    helper = _body(src, "private static void BackfillManualIdentity(string name, string unitId)")
    assert "ContactStore.IsManualContact(name)" in helper, "不是手动好友就别回填"
    assert "ContactStore.ManualUnitId(name) == unitId" in helper, \
        "库里已经是这个 ID 就别发 RPC（幂等靠这一句，不然每次开窗都多一次往返）"


def test_selected_unit_ids_only_takes_exact_entries():
    """建群/拉人上行身份时，**只许收精确的那些**。

    猜来的（`Exact == false`）一旦交给 Python 落盘，就是跨会话固化的错误身份。
    """
    src = _src(CONTACT_PANEL)
    helper = _body(src, "private JObject SelectedUnitIds(List<string> names)")
    assert re.search(r"if\s*\(e\s*==\s*null\s*\|\|\s*!\s*e\.Exact\s*\|\|\s*string\.IsNullOrEmpty\(e\.UnitId\)\)\s*continue", helper), \
        "SelectedUnitIds 必须只收 Exact 且 UnitId 非空的条目"
    # 两个上行点都要用它。
    # ★ 09-17 更正：`ConfirmCreate` 的判据从 `ChatLauncher.OpenForRoom(` 换成
    #   `p["member_unit_ids"]`。原因不是"它不再上行身份"，而是**它不再开窗** ——
    #   建群改走 `create_room`（建群是改数据、开窗是开窗，见
    #   `test_confirm_create_never_opens_the_chat_window`），于是原先那个"顺带证明
    #   身份上了路"的标记物消失了。新判据**更严**：旧判据只证明"它调了开窗函数"
    #   （身份是不是真进了 payload 得再看一层），新判据直接要求身份写进发出去的 `p`。
    for sig, needle in (("private void ConfirmCreate()", 'p["member_unit_ids"]'),
                        ("private void ConfirmInvite()", 'p["member_unit_ids"]')):
        body = _body(src, sig)
        assert "SelectedUnitIds(" in body, sig + " 没有交精确身份"
        assert needle in body, sig + " 没把精确身份写进上行参数"
    # 关系层条目天然 Exact（对象就是按 unitID 精确查出来的）—— 判据的源头
    br = _body(src, "private void BuildRelations()")
    assert re.search(r"Exact\s*=\s*!\s*string\.IsNullOrEmpty\(uid\)", br), \
        "BuildRelations 必须给关系层条目打上 Exact（它是建群时身份的主要来源）"


def test_group_identity_travels_with_each_frame_not_via_the_global_registry():
    """★ 09-17 反转：群成员身份**不再回灌全局登记表**，改为**随帧携带**。

    原实现（已删）把服务端回包的 `member_unit_ids` 逐条 `UnitLookup.Pin` 进**全局**表。
    那是**作用域泄漏**：房间级事实写进全局表、且后写覆盖先写 ⇒ 开一个含「益婉容B」的群
    会把单聊里登记过的「益婉容A」**静默覆盖**，之后 A 的自身段/查人全挂到 B 身上 ——
    正是文件头那次事故的形态，只是换了个后门进来。

    现在的三条通道（每一条都直达 `RowAvatars.Apply(img, name, id)`）：
      · `room_say.speaker_id`        → `ApplyRoomSay` → `FinishNpcBubble(…, id)`
      · `room_state.speaking_ids`    → `ApplyRoomState` → `SetRoomSpeaker(name, id)`
      · 回放 `items[].speaker_id`    → `ReplaceHistory` 的 peer 分支
    本测试钉住"它确实还在、且确实没走全局表"，两条缺一不可。
    """
    src = _src(CHAT_PRESENTER)
    assert "PinMemberUnitIds(" not in _strip_cs_comments(src), \
        "群身份不许再回灌全局登记表（作用域泄漏，见该处注释）"
    # 通道①：room_say
    say = _body(src, "private void ApplyRoomSay(string uid, string speaker, string text, string speakerId = null,")
    assert re.search(r'FinishNpcBubble\([^;]*speaker\s*,\s*speakerId\s*\)', say), \
        "room_say 的精确 id 必须传给气泡（否则逐行头像退回按名解析）"
    # ★ 09-17 补：通道①的**消费侧接缝**。光在 `ChatFrameTee` 里解出 `SpeakerId`、
    #   在 `ApplyRoomSay` 里接住它还不够 —— 关窗期间攒下的帧走的是 `DrainRoomFrames`，
    #   那里若不把 `f.SpeakerId` 传下去，**重放路径**的每一行都会退回按名解析
    #   （此前这条变异是假绿：没人钉 drain 这一跳）。
    drain = _body(src, "private void DrainRoomFrames(string roomId, JArray items)")
    # ★ 结尾不钉死 ★（11-21）：后面还可能追加帧字段（本轮加了 `EchoText`）。
    #   这条测试要守的是"帧里的**身份**必须一路传到渲染口"，不是"参数表到此为止"。
    #   钉死结尾会把每一次新增字段都变成误红 —— 而误红几次之后，人们就会开始删这条测试。
    assert re.search(r"ApplyRoomSay\(f\.Uid,\s*f\.Speaker,\s*f\.Text,\s*f\.SpeakerId,\s*f\.Narration",
                     drain), \
        "DrainRoomFrames 必须把帧里的 `SpeakerId`（以及旁白位 `Narration`）一起交给 ApplyRoomSay"
    # 通道②：room_state
    st = _body(src, "private void ApplyRoomState(string phase, string who, string note, string whoId = null)")
    assert re.search(r"_window\.SetRoomSpeaker\(who,\s*whoId\)", st), \
        "room_state 的 id 必须与名字**同一次**交给窗口（分开写会漂移成'名字对、脸是别人的'）"
    # 通道③：回放（落在 ChatWindow，不在 ChatPresenter）
    win = _src(CHAT_WINDOW)
    # ★ 11-20 起断言对象改成 `ApplyHistoryItem` ★：回放拆成分帧构建后，那个 switch
    #   整块搬进了 `ApplyHistoryItem`，`ReplaceHistory` 只剩分流。断言跟着搬，不放松。
    hist = _body(win, "private void ApplyHistoryItem(JToken token)")
    assert re.search(r'string\s+whoId\s*=\s*\(string\)o\["speaker_id"\]', hist), \
        "回放必须读 speaker_id（重开窗那一刻 C# 手上未必还有这个群的身份）"
    assert re.search(r"AppendNpcStatic\(shown,\s*who,\s*whoId\)", hist), \
        "回放的 peer 分支没把精确身份传下去"


def test_group_member_ids_are_threaded_from_the_contact_list_to_the_request():
    """`{npc_id: unitID}` 从通讯录条目一路传到 RPC 请求体，中间不许断。"""
    lc = _src(LAUNCHER)
    assert re.search(r"OpenForRoom\(string roomId, List<string> members, string titleHint,\s*\n?\s*JObject memberUnitIds = null\)", lc), \
        "ChatLauncher.OpenForRoom 必须能收身份表"
    ab = _src(CHAT_PANEL)
    assert re.search(r"OpenForRoomStatic\(string roomId, List<string> members, string titleHint,\s*\n?\s*JObject memberUnitIds = null\)", ab)
    assert "p.OpenRoomById(roomId ?? \"\", members, titleHint ?? \"\", memberUnitIds)" in ab
    cp = _src(CHAT_PRESENTER)
    assert re.search(r"OpenRoomById\(string roomId, List<string> members, string titleHint,\s*\n?\s*JObject memberUnitIds = null\)", cp)
    body = _body(cp, "public void OpenRoomById(string roomId, List<string> members, string titleHint,\n"
                     "                                 JObject memberUnitIds = null)")
    assert re.search(r'if\s*\(memberUnitIds\s*!=\s*null\s*&&\s*memberUnitIds\.Count\s*>\s*0\)\s*p\["member_unit_ids"\]\s*=\s*memberUnitIds', body), \
        "只有非空时才放进请求体（空表上行没有意义）"
    # 拉人那条单独发，别漏
    invite = _body(_src(CONTACT_PANEL), "private void ConfirmInvite()")
    assert re.search(r'if\s*\(unitIds\.Count\s*>\s*0\)\s*p\["member_unit_ids"\]\s*=\s*unitIds', invite), \
        "拉人请求没有带上精确身份"


def test_group_ids_use_a_dict_not_a_parallel_array():
    """★ 身份表必须是 **dict**，不许是与 `members` 平行的数组。

    平行数组只要两端对不齐（过滤掉空成员、C# 多塞了一个、顺序不一致），就会把甲的
    身份安到乙头上 —— 不报错、不缺字段，只是画错脸、把别人的档案端给模型。
    dict 让"谁的身份"由键自己带着走，这种失败模式根本不存在。
    """
    src = _src(CHAT_PRESENTER)
    body = _body(src, "public void OpenRoomById(string roomId, List<string> members, string titleHint,\n"
                      "                                 JObject memberUnitIds = null)")
    assert 'p["member_unit_ids"] = memberUnitIds' in body, \
        "member_unit_ids 必须以对象（dict）上行"
    assert re.search(r'p\["member_unit_ids"\]\s*=\s*new\s+JArray', body) is None, \
        "不许拼成与 members 平行的数组（两端对不齐就会把甲的身份安到乙头上）"
    # Python 侧同款判据：只接受 dict，数组/字符串一律拒（用文本断言，不是 C# 的大括号 helper）
    ws = (ROOT / "transport" / "ws_channel.py").read_text(encoding="utf-8")
    i = ws.index("def _unit_id_map(raw: object)")
    helper = ws[i:ws.index("\n\nclass ", i)]
    assert "if not isinstance(raw, dict)" in helper, \
        "Python 侧必须只接受 dict（数组形态是错位事故的温床，直接拒掉）"
