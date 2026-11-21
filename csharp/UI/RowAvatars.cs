/// <summary>
/// 对话窗**逐行头像** —— 群聊里每一行气泡左侧那 80×80 的槽位，按**说话人**贴他自己的立绘。
///
/// ## 为什么需要它（现状是错的）
/// 单聊窗的立绘是"开窗时渲一次、写进**行模板**的 `Npcavatar`"（`AbChatPanel.FillPortraits`），
/// 克隆出来的行**共享那一张纹理**。单聊里这是对的（一个窗口只有一个对话对象）；
/// 群聊里就变成「所有人共用一张脸」——而且那张脸是**上一个单聊 NPC** 的
/// （`OpenForRoom` 从不调 `FillPortraits`，模板里的纹理就停在上一次单聊的结果）。
/// 更糟的是这一条**没有任何报错**：头像看得见、只是画着别人。
///
/// ## 取图路径
/// 说话人只有**中文名**（全项目 npc_id 都是中文名），而像素快照按 **unitID** 存
/// ⇒ 先 `PortraitCache.IdByName` 翻译，再 `TryGet` / `RequestLoad`：
///
///   名字 →[UnitLookup 解析，带备忘录]→ unitID →[内存 LRU]→ 贴图
///                                              └[未命中]→ 排队读盘 + 本行暂不显示
///
/// ## 未命中为什么"隐藏"而不是留占位
/// 留占位（空方块或模板那张别人的脸）比不显示更糟：前者像坏图，后者在撒谎。
/// 隐藏的代价只是"这条消息没有头像"，气泡本来就贴边排（与单聊无立绘时的既有行为一致）。
///
/// ## 待贴表是**静态**的
/// `ChatBubble` 是普通 C# 类（不是 MonoBehaviour，没有 Update），而"读了盘要贴上去"
/// 必须逐帧推进。宿主 `ChatWindow` 每帧调一次 `Pump()`；表里存的是行上的 RawImage 引用，
/// 行被销毁（重放/裁剪）后引用变 Unity fake-null，`Pump` 自动出列。
/// 本工程只有**一个**对话窗实例（常驻宿主），所以静态单例是安全的；
/// `Reset()` 在整块重建内容时清表。
/// </summary>
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class RowAvatars
    {
        private class Pending
        {
            public string Id;
            public RawImage Img;
        }

        private static readonly List<Pending> _pending = new List<Pending>();

        /// <summary>
        /// 把 <paramref name="speakerName"/> 的立绘贴到这一行的头像槽位。
        ///
        /// 参数是**三态**的，那个 null/非 null 的差别就是"用谁的脸"的全部语义：
        ///   · `null`      = 不管：保持模板预填的立绘不动 —— 单聊的语义
        ///                   （玩家行与 NPC 行都用 `FillPortraits` 渲进模板的那张）。
        ///   · `""`（空串）= 这是**群聊**但不知道说话人是谁（窗口关着时那条点名帧被丢掉后
        ///                   又重开 / 中途切走再切回 / 终帧之后还有迟到 delta；**不是帧乱序**）：
        ///                   **隐藏头像**。群聊里退回模板立绘 = 画上一个单聊 NPC 的脸，
        ///                   那是静默撒谎；没有脸只是少一张图。
        ///   · 非空名字    = 这一行的头像完全由本方法决定（含取不到时隐藏）。
        ///
        /// <param name="speakerId">说话人的**精确 unitID**（群聊帧 / 群历史投影随行携带）。
        /// 给了它就**直接取图、一次解析都不做** —— 这是本方法的首选路径：名字只是显示用的，
        /// 而"这个名字是谁"在群聊里 Python 侧本来就知道（`Room.member_unit_ids`），
        /// 没有理由让 C# 再按名猜一次（重名时猜错**不报错、不缺字段**，只是画上别人的脸）。
        /// 空/null ⇒ 回落 `PortraitCache.IdByName(name)`（登记表 → 全图按名），
        /// 单聊与老数据走这条。**两者都不会画错人**：前者是精确值，后者至少会命中登记表。</summary>
        public static void Apply(RawImage img, string speakerName, string speakerId = null)
        {
            if (img == null || speakerName == null) return;
            if (speakerName.Length == 0)
            {
                Hide(img);          // 群聊 + 说话人未知：不画错人
                return;
            }

            // ★ 首选精确 id；没有才按名解析（解析带失败限流，见 IdByName 的注释）
            string id = !string.IsNullOrEmpty(speakerId) ? speakerId : PortraitCache.IdByName(speakerName);
            if (string.IsNullOrEmpty(id))
            {
                // 解析不到（还没进场 / 全图无此人 / unitID 读失败）。不排队：`IdByName`
                // 自己带失败限流，下一行/下一帧再问一次比在这里堆任务干净。
                Hide(img);
                return;
            }

            Texture2D tex;
            if (PortraitCache.TryGet(id, out tex))
            {
                Show(img, tex);
                return;
            }

            // 未命中：排队读盘，先不显示。待贴表按**槽位**去重（同一行只留一条）。
            PortraitCache.RequestLoad(id);
            Hide(img);
            for (int i = 0; i < _pending.Count; i++)
            {
                if (_pending[i].Img != img) continue;
                _pending[i].Id = id;      // 同一行换了说话人（理论上不会）→ 以最后一次为准
                return;
            }
            _pending.Add(new Pending { Id = id, Img = img });
        }

        /// <summary>每帧泵（`ChatWindow.Update` 调）：限流读盘 + 给已就绪的待贴行贴图。
        /// ★ 必须由**对话窗**来泵：`PortraitCache.PumpLoad` 原先只挂在 `ContactPresenter.Update`
        /// 上，而通讯录关闭时它所在根节点是 OFF 的（`Update` 根本不跑）—— 只看对话窗时
        /// 磁盘上的快照**永远不会被读进内存**，表现就是"头像一直不出现"，且零报错。</summary>
        /// <summary>把某个槽位从待贴表里摘掉。
        ///
        /// 什么时候必须调：槽位被**别的路径接管**时。典型是对话窗按名开窗（死者）后又换回
        /// 一个活人 NPC —— 活人走 `PortraitService.Fill` 直接写纹理，而待贴表里还留着死者
        /// 那条 pending，下一帧 `Pump()` 就会把**死者的脸**盖回去（看得见、但画错了人，
        /// 零报错）。待贴表按槽位去重（同一 RawImage 只有一条），所以摘一条就够。</summary>
        public static void ClearSlot(RawImage img)
        {
            if (img == null) return;
            for (int i = _pending.Count - 1; i >= 0; i--)
                if (_pending[i].Img == img) _pending.RemoveAt(i);
        }

        public static void Pump()
        {
            PortraitCache.PumpLoad();
            for (int i = _pending.Count - 1; i >= 0; i--)
            {
                var p = _pending[i];
                if (p.Img == null) { _pending.RemoveAt(i); continue; }   // 行已销毁（重放/裁剪）
                Texture2D tex;
                if (!PortraitCache.TryGet(p.Id, out tex)) continue;      // 还没落地，下帧再看
                Show(p.Img, tex);
                _pending.RemoveAt(i);
            }
        }

        /// <summary>清空待贴表（整块重建内容 / 关窗）。**不清缓存** —— 已读进内存的图继续可用。</summary>
        public static void Reset()
        {
            _pending.Clear();
        }

        private static void Show(RawImage img, Texture2D tex)
        {
            try
            {
                img.texture = tex;
                img.color = Color.white;
                img.gameObject.SetActive(true);
            }
            catch { }
        }

        private static void Hide(RawImage img)
        {
            try { img.gameObject.SetActive(false); }
            catch { }
        }
    }
}
