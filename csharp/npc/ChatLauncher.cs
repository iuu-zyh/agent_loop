/// <summary>
/// 统一「打开对话 UI」入口 —— NPC 面板「AI 对话」按钮与通讯录行点击共用。
///
/// AB 模式：对话常驻宿主（首次打开时 AbChatPanel.EnsureResident 经 g.ui.OpenUI 懒创建，
/// 挂游戏 UI 层、层级自动排）→ OpenForUnitStatic 重绑当前 NPC 并显形
/// （历史回放/open_chat/立绘全在 Presenter 路径内）。打不开只记日志放弃。
/// 代码模式：ModMain.PresenterInstance.OpenForNpc(中文名) 复用现成 ChatWindow。
///
/// npc_id 统一发中文名（Python 侧会话/人设以中文名为键，见 UnitLookup 注释）；
/// GetName 失败回退 unitID。硬约束：触 g.ui / g.world，仅主线程调用。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;   // JObject —— 群成员精确身份 member_unit_ids 的载体
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class ChatLauncher
    {
        /// <summary>打开对话 UI 并绑定该单位。unit 为 null 时返回 false。
        ///
        /// <paramref name="guessed"/> = **这个对象是"按名猜"出来的，不是握在手里的真身**。
        /// 默认 false（6 个调用方 —— NPC 面板按钮 / 通讯录关系层行 / 剧情窗 / NPC 主动开口 /
        /// 当面开口 / 动作完成 —— 拿到的都是真身）。只有 `ContactPresenter.OpenChatFor` 在
        /// "精确 unitID 失效、退回按名解析"那一条路上会传 true。
        ///
        /// 为什么必须区分：登记一份"名字 → unitID"是**权威声明**，而 `UnitLookup` 的
        /// ⓪ 分支排在**全图扫描之前** —— 登记错一次，所有按名查这个人的地方（L1 自身段、
        /// 工具查人、候选人集）全部挂到别人身上，且**不报错、不缺字段**
        /// （文件头那次真机事故："自身部分内容也是错的，太离谱了"）。
        /// `UnitIdRegistry.Remember` 的注释写着"按名查出来的结果永远不许写进来" ——
        /// 这个参数就是那句话的开关。</summary>
        public static bool OpenForUnit(WorldUnitBase unit, bool guessed = false)
        {
            if (unit == null) return false;
            string id = null;
            try { id = unit.data.unitData.propertyData.GetName(); } catch { }
            string unitId = null;
            try { unitId = unit.data.unitData.unitID; } catch { }
            if (string.IsNullOrEmpty(id)) id = unitId;
            if (!guessed)
            {
                // 抓住真身的那一刻把名字登记到 unitID：这里是全工程唯一"拿着 WorldUnitBase
                // 去开对话窗"的入口（NPC 面板按钮 / 通讯录行点击 / 剧情窗按钮全汇到这），
                // 也是**自身段（L1）唯一能零歧义确定"我是谁"的时机** —— 后续 GameContext.GetL1(npc_id)
                // 按名解析时先查这份登记，重名也不会挂到别人身上。
                // 09-17 起落在 UnitIdRegistry：**落盘 + 按存档隔离**，重开游戏仍然精确
                // （原 UnitLookup.Pin 是纯内存的，一重启就没了）。
                // `why` = "AI 对话"，与另一个合法写入点（加好友）在日志里可区分。
                try { UnitIdRegistry.Remember(id, unitId, "打开对话"); } catch { }
                // 顺手让手动好友的精确身份自愈（老 contacts.json 没有 unit_id，见方法注释）。
                try { BackfillManualIdentity(id, unitId); } catch { }
            }
            else
            {
                ModMain.P("[ChatLauncher] 本次是**按名猜**出来的对象（" + id +
                          "），不写登记表、不回填（把猜测固化成权威比不记更糟）");
            }

#if AB_UI
            // AB 模式：懒创建常驻宿主（首次调用时经 g.ui.OpenUI 挂游戏 UI 层）+ 重绑显形。
            // 层级随游戏 UI 管理器自动排（迁移），确认窗/论道弹窗后开自然盖住对话窗。
            if (AbChatPanel.OpenForUnitStatic(unit, guessed)) return true;
            ModMain.P("[ChatLauncher] 对话宿主不可用（OpenUI 失败），本次不打开");
            return false;
#else
            // 无 AB 编译时的默认路径：复用代码构建 UI（Presenter 打开同一窗口）
            if (ModMain.PresenterInstance == null)
            {
                ModMain.P("[ChatLauncher] PresenterInstance 未就绪，无法打开对话 UI");
                return false;
            }
            ModMain.PresenterInstance.OpenForNpc(id);
            FillPortraits(unit);
            return true;
#endif
        }

        /// <summary>按**名字**开窗（没有 `WorldUnitBase`）—— 死者 / unitID 失效的通讯录条目走这条。
        ///
        /// 与 `OpenForUnit` 的三点差别，每一点都是刻意的：
        ///   ① **不写 `UnitIdRegistry`**：那张表是"自身段"的权威名册（`GameContext.GetL1` 先查它），
        ///      而没有真身时写的只能是从快照里读来的旧 id —— 一旦失效就是**静默错人**。
        ///   ② **不 `BackfillManualIdentity`**：同上，没有真身就没有可回填的权威值。
        ///   ③ 立绘不走 `PortraitService.Fill`（它要活体），改由 `AbChatPanel.OpenForName`
        ///      走 `RowAvatars`（磁盘缓存里死前那张还能用）。
        ///
        /// 为什么下层本来就支持：Python `open_chat` 只认 `npc_id` 字符串（`unit_id` 是可选的），
        /// 账本按名字从 jsonl 恢复 —— 于是"翻旧账"不需要任何后端改动。
        /// </summary>
        public static bool OpenForName(string npcId, string unitId = null)
        {
            string id = (npcId ?? "").Trim();
            if (string.IsNullOrEmpty(id)) return false;
            ModMain.P("[ChatLauncher] 按名开窗（无真身）：npc=" + id + " unitId=" + (unitId ?? "-"));
#if AB_UI
            if (AbChatPanel.OpenForNameStatic(id, unitId)) return true;
            ModMain.P("[ChatLauncher] 对话宿主不可用（OpenUI 失败），本次不打开");
            return false;
#else
            if (ModMain.PresenterInstance == null)
            {
                ModMain.P("[ChatLauncher] PresenterInstance 未就绪，无法打开对话 UI");
                return false;
            }
            ModMain.PresenterInstance.OpenForNpc(id);
            return true;
#endif
        }

        /// <summary>手动好友的**精确身份自愈**：库里没有这个 ID（本功能上线前加的好友）
        /// 或 ID 已经变了（存档变动）→ 补写一次。
        ///
        /// 时机是刻意的：**"点开他的对话"本身就意味着玩家对这个人有意图**，而这一刻我们
        /// 手里正握着真身 —— 于是这是回填最自然、也最不需要额外动作的落点。
        /// （另一个候选时机是"打开 NPC 面板"，但那是纯查看动作，会平白产生磁盘写入。）
        ///
        /// 幂等且零成本：镜像查询是纯内存的，库里已经是这个 ID 就什么都不做。
        /// ★ 只许在 `guessed == false` 时调用 —— 见 `OpenForUnit`。</summary>
        private static void BackfillManualIdentity(string name, string unitId)
        {
            if (string.IsNullOrEmpty(name) || string.IsNullOrEmpty(unitId)) return;
            if (!ContactStore.IsManualContact(name)) return;
            if (ContactStore.ManualUnitId(name) == unitId) return;
            ContactDuty.BackfillManualUnitId(name, unitId);
        }

        /// <summary>打开对话 UI 并切到群聊模式（通讯录群列表点一行 / 选人后确认建群）。
        ///
        /// `roomId` 非空 = 续聊那个群（`members` 可传 null，成员以服务端为准）；
        /// 为空则必须给 `members` = 新建一个群。**room_id 由 Python 分配并回传**，
        /// 这里只负责"把请求交出去 + 把窗口切到群模式"。
        ///
        /// `memberUnitIds`（可选）= `{npc_id: unitID}`，C# 在**选人确认那一刻**从通讯录
        /// 条目上取到的精确身份（只有 `ContactEntry.Exact` 的才收，见 ContactPresenter）。
        /// 它随 `open_room` 上行、落进 Python 的 `Room.member_unit_ids`，回包时再原样回来
        /// 并由 Python **随帧携带**回来（`room_say.speaker_id` / `room_state.speaking_ids` /
        /// 回放 `items[].speaker_id`）—— 于是群成员的立绘取图**一行都不走按名解析**。
        /// （09-17 起不再喂回全局登记表：那是房间级事实写进全局表，会静默覆盖单聊的同名者。）
        /// 传 null 或空对象完全无害：回落按名解析，行为与本功能上线前一致。
        ///
        /// 打不开只记日志放弃（与 OpenForUnit 同约定，调用方已 Hide 过自己）。
        /// 仅主线程调用。</summary>
        public static bool OpenForRoom(string roomId, List<string> members, string titleHint,
                                       JObject memberUnitIds = null)
        {
#if AB_UI
            if (AbChatPanel.OpenForRoomStatic(roomId, members, titleHint, memberUnitIds)) return true;
            ModMain.P("[ChatLauncher] 对话宿主不可用（OpenUI 失败），群聊窗本次未打开");
            return false;
#else
            // 代码版 UI（无 AB_UI）没有群聊窗：那条链连单聊窗都只在预览工程里用
            ModMain.P("[ChatLauncher] 本构建无群聊 UI（需 AB_UI），群聊窗本次未打开");
            return false;
#endif
        }

        /// <summary>「对方正在斟酌…」占位（用户拍板：同意后立刻开窗吃流式）。
        /// 开窗到首个 token 之间有 4~13s 空窗（ttft），期间显示忙提示；回合收口时
        /// Presenter 的 OnReplyEvent → SetBusy(false) 自动关掉，开窗重放也会清（ChatWindow.ReplaceHistory）。
        /// 打不开就静默（不是关键路径）。仅主线程调用。</summary>
        public static void SetThinking(bool on)
        {
#if AB_UI
            try { if (AbChatPanel.SetBusyStatic(on)) return; } catch { }
#endif
            try
            {
                var w = ModMain.ChatWindowInstance;
                if (w != null) w.SetBusy(on);
            }
            catch { }
        }

        /// <summary>代码版窗口的左右立绘（左 玩家 / 右 NPC，与 AB 版同方向；失败静默隐藏槽位）。
        /// AB 模式的立绘在常驻宿主 OpenForUnit 内填充（其窗口不经 ModMain 注册）。</summary>
        private static void FillPortraits(WorldUnitBase unit)
        {
            try
            {
                var w = ModMain.ChatWindowInstance;
                if (w == null) return;
                if (w.PortraitLeft != null)
                    w.PortraitLeft.gameObject.SetActive(PortraitService.Fill(g.world.playerUnit, w.PortraitLeft));
                if (w.PortraitRight != null)
                    w.PortraitRight.gameObject.SetActive(PortraitService.Fill(unit, w.PortraitRight));
            }
            catch (Exception e)
            {
                ModMain.P("[ChatLauncher] portraits: " + e.Message);
            }
        }

#if AB_UI
#endif
    }
}
