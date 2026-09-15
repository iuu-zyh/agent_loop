/// <summary>
/// 对话 UI 控制器（MonoBehaviour）—— 全工程唯一认 WS 协议的类：
/// 订阅 WsClient.UiEvent，把 step / text_delta / npc_reply / compact_result 翻译成 ChatWindow 视图语言；
/// 把用户输入经 WsClient 发回 Python；开窗/换 NPC 时发 open_chat（开窗=激活 agent + 历史回放）、
/// 关窗时发 dispose_agent（关窗=落盘销毁）。
///
/// IL2CPP 下的输入提交：InputField 的 onSubmit/onEndEdit 事件家族不可靠（UnityEvent 受委托转类影响），
/// 因此 Update 里轮询按键：消息框有内容 + Enter 即发送（不依赖 isFocused——InputField 提交会自动失焦）。
///
/// 各司其职：不建 UI 节点、不管布局（那是 ChatUiBuilder/ChatWindow 的事）；
/// 不碰传输细节（那是 WsClient 的事）。所有回调都在 Unity 主线程
/// （UiEvent 由 Dispatcher 转入主线程，SendRequest 的回调包装同样转主线程）。
///
/// 已知取舍：窗口未开时到达的事件直接丢弃（不做离线缓存）——
/// 关闭期间的对话可由「开窗→open_chat」补齐（重开即 resume 激活），与直播不交叠。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    /// <summary>
    /// IL2CPP 注意：自定义 MonoBehaviour 需宿主侧显式 ClassInjector.RegisterTypeInIl2Cpp
    /// 注册进类型系统（主工程 ModMain 已调用），否则 AddComponent 抛
    /// TypeInitializationException——与 ChatWindow 同规约。本类不引 MelonLoader，
    /// 保持纯 Unity 依赖。
    /// </summary>
    public class ChatPresenter : MonoBehaviour
    {
        /// <summary>IL2CPP 互操作标准构造</summary>
        public ChatPresenter(System.IntPtr ptr) : base(ptr) { }

        private ChatWindow _window;
        private WsClient _ws;
        private bool _busy;
        private Text _compactNotice;   // 「正在压缩中」提示行（compact_result 到达时原位更新）
        // ---- 压缩在跑（用户反馈"压缩按钮按下去没反应"）----
        // 真机现场：17:23:46 点下 → Python 摘要 LLM 调用跑了 104.7s → 17:25:31 成功。
        // 这 105 秒里 C# 侧一行日志都没有、按钮毫无变化，玩家只能判定"没触发"（于是连点三次）。
        // 现在：按钮忙态（文案「压缩中…」+ 置灰）+ 提示行秒表 + 两端各留一行日志。
        private bool _compacting;
        private float _compactStartAt;      // 起算时刻（unscaled；提示行秒表与超时兜底用）
        private string _compactNoticeBase;  // 提示行原文案（秒表刷新用）
        private const float CompactStuckSec = 900f;   // 兜底：15 分钟没回音就当丢了，恢复按钮（摘要实测 105s）
        private bool _enterDeleteOnReplay;   // 删除模式入口流程中：本次重放落地后进入选择模式
        // ---- 历史重放的「欠账」----
        // 症状：NPC 主动开口 → 同意后开窗，历史全空，底部统计栏停在预制体默认的 "New Text"。
        // 现场（Player.log:2691 + Python 日志 16:44:/33）：`open_chat` **发出去了**，
        // Python 也把 34 项历史送回来了，但响应到达时回合已经流式输出十几秒 → `_busy` 为真
        // → 响应被回调侧守卫丢弃 → `ReplaceHistory` / `SetStats` 双双没跑。
        // **一个 return 解释两个症状**（历史没了 = 没 ReplaceHistory；New Text = 没 SetStats）。
        //
        // 为什么改成「欠账」而不是继续丢弃：`ReplaceHistory` 是全量重投影（第一句就是
        // `ResetContent()`），与"正在追加的直播"天然互斥 —— 所以**拒绝的理由是对的，
        // 丢弃的处理是错的**。冲突不是失败，是「还没轮到」。
        // 记一个持续为真的期望，等冲突解除（回合收口）再还。
        //
        // 为什么还的时候**重新请求**、不套用手里这份响应：这份快照早于回合收口，
        // 按 Python 的语义（`open_chat` 只回已闭合 turn）它**不含**玩家刚看完的那条回复——
        // 直接套用等于把回复擦掉，比现状更糟。重放是幂等的（全量重投影），多跑一次无副作用。
        //
        // 注意：这是**兜底**，不是治本。治本是让开窗赶上首 token 前那 4~13s 的 TTFT 空窗
        // （见 NpcInitiativeMonitor.cs:386）；欠账机制只在那一场竞速输掉时兜住。
        private bool _wantReplay;
        // 图片预览去重键：= 上一次算过预览的输入框文本。解码整图不便宜，只在文本变化时重算
        private string _previewKey;

        // ---- 忙标卡死排查----
        // 症状：对话已收口，删除模式仍提示"回合进行中"（守卫 `_busy || HasActiveBubble`， 忙=True）。
        // 而 `_busy` 只能经 `SetBusy(true)` 置真、且必然伴随一行 `忙标 ON`；日志里只有 ON 没有 OFF
        // ⇒ `SetBusy(false)` 从未执行 ⇒ 要么本 presenter 收不到 `npc_reply`，要么收口路径半途抛异常。
        // 这三条探针把"收不到 / 收到了但没跑完 / 跑了但没清"三种情形一次分开。
        private static int _seq;
        private int _id;                       // 实例号（ClassInjector 注入的托管类，不依赖 GetInstanceID）
        private float _busySince;              // 忙标置真的时刻（unscaled）
        private float _lastFrameAt;            // 最近一次收到本 NPC 帧的时刻（unscaled）
        // 玩家消息时间戳自检：记录上次**打过的**标签，换月才打一行日志。
        // static：多个 presenter 实例（多 NPC）共用一行，免得每开一个 NPC 都重打一遍同一个月。
        private static string _lastStampLogged = "";
        /// <summary>忙标自愈阈值（秒）：超过这么久一条帧都没有 = 这个忙标是死的。
        /// 取 240s —— 实测最长回合约 29s（含工具循环），留近 10 倍余量，绝不会误伤真在跑的回合。</summary>
        private const float BusyStaleSeconds = 240f;

        public void Init(ChatWindow window, WsClient ws)
        {
            _window = window;
            _ws = ws;

            if (_ws != null) _ws.UiEvent += OnUiEvent;
            else ModMain.P("[ChatPresenter] ★Init 时 WsClient 为空 → 未订阅 UiEvent："
                           + "按钮会照接（能发消息）但收不到任何回帧 → 忙标永久卡死。这是已知故障形态。");
            if (_id == 0) _id = ++_seq;
            ModMain.P("[ChatPresenter] Init 完成 实例#" + _id + " 订阅=" + (_ws != null));
            if (_window != null)
            {
                _window.CloseClicked += OnCloseClicked;
                _window.SubmitRequested += OnSubmitRequested;
                _window.DeleteBtnClicked += OnDeleteBtnClicked;
                _window.CompactClicked += OnCompactBtnClicked;
                _window.DeletePreviewRequested += OnDeletePreviewRequested;
                _window.DeleteConfirmed += OnDeleteConfirmed;
            }
        }

        private void OnDestroy()
        {
            if (_ws != null) _ws.UiEvent -= OnUiEvent;
            if (_window != null)
            {
                _window.CloseClicked -= OnCloseClicked;
                _window.SubmitRequested -= OnSubmitRequested;
                _window.DeleteBtnClicked -= OnDeleteBtnClicked;
                _window.CompactClicked -= OnCompactBtnClicked;
                _window.DeletePreviewRequested -= OnDeletePreviewRequested;
                _window.DeleteConfirmed -= OnDeleteConfirmed;
            }
        }

        private void Update()
        {
            if (_window == null) return;
            BusyWatchdog();   // 忙标自愈：收口帧丢了也不至于卡到重启（见 BusyWatchdog 注释）
            CompactWatch();   // 压缩秒表 + 超时兜底（09-13：摘要调用实测 105s，静态提示=像卡死）
            if (!_window.IsOpen)
            {
                // 关窗时清一次预览：预览节点挂在输入框同级（随窗口根一起隐藏），但输入框文本
                // 可能还留着 → 重开时会突兀地又冒出来。清键 + 销毁，语义与"关窗即收"一致。
                if (_previewKey != null) { _previewKey = null; ImageAttachPreview.Hide(); }
                return;
            }

            // F9 开关已删：对话窗一律由界面入口打开——
            // NPC 面板「AI 对话」/ 通讯录行点击 / 剧情 AI 选项（ChatLauncher.OpenForUnit）。
            // 删除原因见 AbHotkeys 文件头：帧回调被双重注册，同帧两次 toggle 会互相抵消。

            // Esc 关闭链（用户反馈"按 Esc 只影响游戏本体 UI，mod 窗不受影响"）：
            // ① 配置窗开着 → 先关配置窗（它是最上层）；② 输入框聚焦 → 先失焦（Esc 退输入态）；
            // ③ 否则关闭对话窗（同点 ✕：dispose agent + 落盘）
            if (Input.GetKeyDown(KeyCode.Escape))
            {
#if AB_UI
                if (AbConfigPanel.IsOpen)
                {
                    AbConfigPanel.ClosePanel();
                    return;
                }
#endif
                bool chatFocused = _window.ChatInput != null && _window.ChatInput.isFocused;
                bool npcFocused = _window.NpcLabel != null && _window.NpcLabel.isFocused;
                if (chatFocused || npcFocused)
                {
                    if (chatFocused) _window.ChatInput.DeactivateInputField();
                    if (npcFocused) _window.NpcLabel.DeactivateInputField();
                    return;
                }
                OnCloseClicked();
                return;
            }

            var input = _window.ChatInput;

            // 图片附件入口②：输入框聚焦时 Ctrl+V —— 剪贴板里的图（复制的文件 / 截图位图）补进输入框。
            // 修正 原实现只读 CF_HDROP（资源管理器复制的文件），而 `Win+Shift+S` 截图
            // **只写 Bitmap/PNG、不写 CF_HDROP**（HasFileDrop=False / HasImage=True）→
            // 用户粘贴后什么都没发生、且无任何日志。现改走 ClipboardImages()（CF_HDROP →
            // 注册格式 "PNG" → CF_DIB 三条来源，后两者落临时文件归一到"路径"），**无论成败都留痕**。
            // 剪贴板里没有图时**原样放行**（不 return），普通文本粘贴照旧。
            if (input != null && input.isFocused &&
                (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl)) &&
                Input.GetKeyDown(KeyCode.V))
            {
                string how;
                var clip = ImageInput.ClipboardImages(out how);
                try { ModMain.P("[ImageInput] Ctrl+V 取剪贴板：" + how + " → " + clip.Count + " 张"); } catch { }
                if (clip.Count > 0)
                {
                    var cur = input.text ?? "";
                    var add = new System.Text.StringBuilder();
                    int n = 0;
                    for (int i = 0; i < clip.Count; i++)
                    {
                        if (cur.IndexOf(clip[i], StringComparison.OrdinalIgnoreCase) >= 0) continue;   // 已在框里
                        if (n > 0) add.Append(' ');
                        // 引号 + 空格分隔，不用换行 输入框可能是**单行** InputField（换行会被 Unity
                        // 吃掉，多张就会挤成一行 → 只有一张被识别）。带引号形式单/多行都成立，
                        // 且与 Windows「复制文件地址」同款；路径含空格也不歧义。
                        add.Append(ImageInput.QuotePath(clip[i]));
                        n++;
                    }
                    if (n > 0)
                    {
                        string joined = add.ToString();
                        input.text = cur.Length > 0 && !cur.EndsWith(" ") ? cur + " " + joined : cur + joined;
                        input.caretPosition = input.text.Length;
                    }
                    return;   // 已消费这次 Ctrl+V
                }
                // 剪贴板有内容但不是图（如普通文本）：放行给 InputField 走原生粘贴
            }

            // 图片预览条：输入文本变了才重算（解码整图不便宜，绝不能每帧跑）。
            // 预览是「到底附上了没有」的唯一可视反馈 —— 没有它，路径通道对玩家就是不可观测的。
            string textNow = input != null ? (input.text ?? "") : "";
            if (textNow != _previewKey)
            {
                _previewKey = textNow;
                if (textNow.Length == 0)
                {
                    ImageAttachPreview.Hide();
                }
                else
                {
                    string _clean; List<string> _paths;
                    ImageInput.SplitPaths(textNow, out _clean, out _paths);
                    ImageAttachPreview.Sync(input, textNow, _paths, ImageInput.MaxCount);
                }
            }

            // 消息回车 → 发送：不要求消息框聚焦（2026-09-08 实机反馈：Unity InputField 单行
            // 按 Enter 会先触发 Submit 并自动失焦，isFocused 判定恒假 → 用户只能手动点发送按钮）。
            // 改为"消息框有内容"即视为发送意图（NpcInput 已只读展示 NPC 名，不再支持手改切换。
            // 原 "NPC 名框未在编辑" 竞态守卫随只读化一并移除——它没有可编辑状态了）。
            if (input != null && input.text.Length > 0 &&
                (Input.GetKeyDown(KeyCode.Return) || Input.GetKeyDown(KeyCode.KeypadEnter)))
            {
                OnSubmitRequested();
                return;
            }
        }

        // ------------------------------------------------------------------
        // 窗口开关 / NPC 切换
        // ------------------------------------------------------------------

        private void ToggleWindow()
        {
            if (_window == null) return;
            if (_window.IsOpen)
            {
                string closingId = _window.CurrentNpcId;
                UnreadStore.NotifyChatClosed(closingId);
                DisposeAgentOnServer(_ws, closingId);   // 关窗=销毁活体（落盘留账，重开自动 resume）
#if AB_UI
                AbChatPanel.CloseViaManager();   // 方案 A：关闭交游戏（实例可被销毁，重开自动 resume+回放）
#else
                _window.Close();
#endif
                return;
            }
            string id = _window.NpcLabel != null && _window.NpcLabel.text != null
                ? _window.NpcLabel.text.Trim()
                : "";
            _window.OpenFor(id);
            UnreadStore.NotifyChatOpen(id);
            if (id.Length == 0)
            {
                _window.AppendSystemNotice("请先在右上角输入 NPC 名（回车生效）");
                return;
            }
            // 经 SetBusy 而不是直接赋字段：字段与窗口标签必须一起复位。
            // 原来只改 `_busy`（Presenter 的私有字段），`ChatWindow` 的 BusyLabel 无人 SetActive(false)
            // ⇒ 守卫已经放行、标签却还亮着（实机 bug 的最后一环）。
            SetBusy(false);
            OpenChatAndReplay();
        }

        private void OnCloseClicked()
        {
            // 关窗即清欠账：否则下一回合收口会对着一个已关的窗口补发 open_chat
            //（回调侧有 `!IsOpen` 守卫会拦下，所以只是白发一次 RPC，但没必要）。
            _wantReplay = false;
            if (_window != null)
            {
                string closingId = _window.CurrentNpcId;
                UnreadStore.NotifyChatClosed(closingId);
                DisposeAgentOnServer(_ws, closingId);
#if AB_UI
                AbChatPanel.CloseViaManager();   // 方案 A：关闭交游戏
#else
                _window.Close();
#endif
            }
        }

        /// <summary>NPC 面板按钮入口：开窗并绑定当前 NPC（同 F9 开窗逻辑，供 NpcPanelButton 外部调用）</summary>
        public void OpenForNpc(string npcId)
        {
            if (_window == null) return;
            string id = (npcId ?? "").Trim();
            _window.OpenFor(id);
            UnreadStore.NotifyChatOpen(id);
            SetBusy(false);   // 同上：换 NPC 时字段与标签一起复位
            // 换 NPC / 关开窗：压缩提示行会随重放被销毁，忙态也要一起复位
            // （压缩若仍在跑，结果到达时按 npc_id 过滤后落到 `AppendSystemNotice` 兜底补一行）
            if (_compacting) { _compactNotice = null; ClearCompactBusy(); }
            if (id.Length == 0)
            {
                _window.AppendSystemNotice("未获取到 NPC id");
                return;
            }
            OpenChatAndReplay();
        }

        // ------------------------------------------------------------------
        // 发送（用户 → Python）
        // ------------------------------------------------------------------

        private void OnSubmitRequested()
        {
            if (_window == null) return;
            var input = _window.ChatInput;
            string msg = input != null ? input.text.Trim() : "";
            if (msg.Length == 0) return;

            string npc = _window.CurrentNpcId;
            if (npc.Length == 0)
            {
                _window.AppendSystemNotice("请先填写 NPC 名");
                return;
            }
            if (_ws == null)
            {
                _window.AppendSystemNotice("未连接 Python（WsClient 未启动）");
                return;
            }
            // 上面那个判据查的是「客户端对象在不在」，而 `_ws` 在 Init 赋值后
            // **永不为 null**（见本文件 :37/:71）——所以 Python 进程死掉时这里照样放行，
            // 帧到 `WsClient.Send` 被 `!IsOpen` 静默丢弃，玩家屏幕上"什么都没发生"。
            // 这里按**连接状态**再判一道，并把当前真实状态如实告诉玩家（不许静默失败）。
            if (!_ws.IsConnected)
            {
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return;
            }

            // /compact 控制指令：不进玩家气泡、不进对话历史——清空输入框后
            // 走专用压缩通道（event=compact），Python 端调 compact_now 并回推 compact_result；
            // 「正在压缩中」提示行由结果事件原位更新为压缩反馈（成功/无内容/失败/忙）。
            if (msg.Equals("/compact", StringComparison.OrdinalIgnoreCase))
            {
                if (input != null)
                {
                    input.text = "";
                    TryActivateInput(input);
                }
                StartManualCompact(npc);
                return;
            }

            // 图片附件：文本里的图片路径 → 读文件 → 降采样 → base64 data URL。
            // 路径**从正文里摘掉**（路径对模型是噪音），再按 `[图片：文件名]` 占位拼回 text ——
            // 占位由 C# 一侧生成、Python 不再改写，故本地气泡 / 落盘历史 / 后续回合三者同一串。
            List<string> imgPaths;
            string clean;
            ImageInput.SplitPaths(msg, out clean, out imgPaths);

            List<JObject> attachments = null;
            if (imgPaths.Count > 0)
            {
                List<string> names, errors;
                attachments = ImageInput.Build(imgPaths, out names, out errors);
                for (int i = 0; i < errors.Count; i++) _window.AppendSystemNotice("图片：" + errors[i]);
                if (attachments.Count == 0) attachments = null;
                else
                {
                    var sb = new System.Text.StringBuilder(clean);
                    for (int i = 0; i < names.Count; i++)
                    {
                        if (sb.Length > 0) sb.Append('\n');
                        sb.Append(ImageInput.Placeholder(names[i]));
                    }
                    clean = sb.ToString();
                    _window.AppendSystemNotice("已附加 " + attachments.Count + " 张图片");
                }
            }
            if (clean.Length == 0) return;      // 路径全被吃掉又没有正文：理论上不会（占位非空）

            // 游戏时间戳：NPC 感知不到时间，故玩家消息在**进 WS 之前**拼上当前游戏
            // 年月（`[1年1月] `），让 Python 账本永久带上"这句话是哪年哪月说的"——这样 NPC 才
            // 可能说出"上次见你是三月前"。日期读数与换算见 NpcInitiativeMonitor.CurrentTimeLabel
            // / UnitSnapshot.CnYearMonth（与经历「近况」共用同一条换算，不各写一份）。
            //
            // 位置必须在这里（本地回显之前）—— 气泡 / 账本 / 模型后续回合看到的必须是
            //   **同一个串**，同 `[图片：xx]` 占位那条唯一来源约定。若改在 WsClient.SendPlayerMessage
            //   里拼，实时气泡是干净的、而重开窗口走 get_history 回放时气泡会突然长出时间戳，
            //   同一条消息两种样子（回放走 ChatWindow.cs 的 AppendUserMessage(o["text"])，读的就是账本）。
            //   代价是气泡里也看得见时间戳——参照 mod 同样把 `[N年M月D日]` 显示在聊天条目上。
            // 日历取不到 → 标签为空串 → **原样照发**，时间戳绝不阻断玩家发言。
            string stamp = NpcInitiativeMonitor.CurrentTimeLabel();
            if (stamp.Length > 0)
            {
                // 自检留痕（换月才打一行，不刷屏）：这是**唯一**能反查"账面月标度对不对"的现场证据。
                // 判读：同一游戏月内，这里打的标签必须与经历「近况」条目前的 `(N年M月)` 一致；
                // 若整月差一个月（近况 (1年1月) 而这里 [1年2月]），就是 CurrentAccountMonth 的
                // `roundMonth + 1` 标度错了 —— 那是本机制唯一的一处魔法常量。
                if (stamp != _lastStampLogged)
                {
                    _lastStampLogged = stamp;
                    ModMain.P("[ChatPresenter] 玩家消息时间戳 " + stamp
                              + "（roundMonth=" + (NpcInitiativeMonitor.CurrentAccountMonth() - 1) + "）");
                }
                clean = stamp + " " + clean;
            }

            // 本地回显 → 清输入框并保持焦点 → 发 WS
            _window.AppendUserMessage(clean);
            if (input != null)
            {
                input.text = "";
                TryActivateInput(input);
            }
            SetBusy(true, "玩家发消息 npc=" + npc);
            // 无图走原重载（保外部自测工程既有 override 生效）；带图才走新重载
            if (attachments != null) _ws.SendPlayerMessage(npc, clean, new JArray(attachments));
            else _ws.SendPlayerMessage(npc, clean);
        }

        /// <summary>手动压缩公共入口（/compact 命令与标题栏「压缩」按钮共用，单一实现不复制）：
        /// 立一条「正在压缩中」提示行，压缩结果由 compact_result 事件原位更新该行。
        /// 调用方自把守卫（按钮路径忙时拒绝；命令路径沿用原语义，Python 侧闸兜底）。</summary>
        private void StartManualCompact(string npc)
        {
            _compactNoticeBase = "正在压缩对话历史…";
            _compactNotice = _window.AppendSystemNotice(_compactNoticeBase + "（已 0s，摘要调用较慢，请勿重复点击）");
            _compacting = true;
            _compactStartAt = Time.unscaledTime;
            _compactLastSec = -1;
            try { _window.SetCompactBusy(true); } catch { }
            // 日志：此前零日志 → "点了没反应"无法从 C# 侧确认；现在两端都有时间戳
            ModMain.P("[ChatPresenter] 压缩请求已发：npc=" + npc +
                      (_compactNotice != null ? "" :
                       "（★提示行未渲染：系统提示行模板缺失？按钮忙态仍可见，压缩照常执行）"));
            _ws.SendCompactRequest(npc);
        }

        /// <summary>标题栏「压缩」按钮：忙时即时拒绝（与 🗑 删除按钮同款轻守卫，反馈不隔回合），
        /// 闲时与 /compact 命令同通路。删除选择模式中放行——压缩与删除在 Python 侧天然互斥
        /// （delete RPC 有 _compacting 闸），无需特判。</summary>
        private void OnCompactBtnClicked()
        {
            if (_window == null || _ws == null || string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            // 未连接时压缩请求同样会被静默丢弃（它的回调永不触发 → 看起来"按了没反应"）
            if (!_ws.IsConnected)
            {
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return;
            }
            if (_busy || _window.HasActiveBubble)
            {
                _window.AppendSystemNotice("回合进行中，结束后再压缩");
                return;
            }
            StartManualCompact(_window.CurrentNpcId);
        }

        private static void TryActivateInput(InputField field)
        {
            try
            {
                if (!field.isFocused) field.ActivateInputField();
            }
            catch (Exception e)
            {
                ModMain.P("[ChatPresenter] ActivateInputField: " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // 入站（Python → 视图）：唯一的协议翻译点
        // ------------------------------------------------------------------

        private void OnUiEvent(JObject payload)
        {
            string ev0 = (string)payload?["event"] ?? "";
            // 收口类帧逐帧留痕（其余帧高频，只在被丢弃时打）：把"收不到"与"收到了但守卫拦了"分开
            if (ev0 == "npc_reply")
                ModMain.P("[ChatPresenter] npc_reply 入站 实例#" + _id + " 窗口=" + (_window != null)
                          + " 开=" + (_window != null && _window.IsOpen) + " 忙(前)=" + _busy);
            if (_window == null) { DiagDrop(ev0, payload, "窗口为空"); return; }
            if (!_window.IsOpen) { DiagDrop(ev0, payload, "窗未开"); return; }
            string npcId = (string)payload["npc_id"] ?? "";
            if (npcId != _window.CurrentNpcId)
            {
                DiagDrop(ev0, payload, "npc不匹配 payload=" + npcId + " 面板=" + _window.CurrentNpcId);
                return;
            }
            _lastFrameAt = Time.unscaledTime;
            switch (ev0)
            {
                case "step":
                    OnStepEvent(payload);
                    break;
                case "text_delta":
                    OnDeltaEvent(payload);
                    break;
                case "npc_reply":
                    OnReplyEvent(payload);
                    break;
                case "compact_result":
                    OnCompactResult(payload);
                    break;
                case "stats_update":
                    // 回合结束全量 token/时延快照 → 刷新底部统计条
                    if (_window != null) _window.SetStats(payload);
                    break;
            }
        }

        /// <summary>被守卫拦下的帧留痕（只关心收口类，避免高频帧刷屏）。
        /// ：删/压缩入口卡死时，"帧到底有没有到 presenter"此前完全无迹可查 ——
        /// 只能靠推理，而推理在"到底卡在收不到还是卡在没复位"上会绕圈。</summary>
        private void DiagDrop(string ev, JObject payload, string why)
        {
            if (ev != "npc_reply") return;
            try
            {
                ModMain.P("[ChatPresenter] ★帧被丢弃(" + why + ") event=" + ev
                          + " npc=" + ((string)payload?["npc_id"] ?? "") + " 实例#" + _id);
            }
            catch { }
        }

        /// <summary>手动压缩结果（/compact 专用通道回推）：把「正在压缩中」提示行原位更新为
        /// 反馈文案。提示行已随开窗重放/换 NPC 重建而销毁时（Unity 已销毁对象 == null），
        /// 兜底直接补一行系统提示，反馈不丢。</summary>
        private void OnCompactResult(JObject payload)
        {
            string text = payload["text"]?.ToString() ?? "";
            bool ok = payload["ok"]?.Value<bool>() ?? false;
            float ms = _compacting ? (Time.unscaledTime - _compactStartAt) * 1000f : 0f;
            ClearCompactBusy();
            // 日志：这条路径此前**零日志**，导致"点了没反应"只能靠 Python 单侧推断
            ModMain.P("[ChatPresenter] 压缩结果：ok=" + ok + " 耗时=" + ms.ToString("F0") + "ms 反馈=" +
                      (text.Length > 80 ? text.Substring(0, 80) + "…" : text));
            if (text.Length == 0) return;
            if (_compactNotice != null)
            {
                _compactNotice.text = text;
                _compactNotice = null;
                return;
            }
            // 提示行不在（重放/换 NPC 销毁了，或系统提示模板缺失）→ 补一行兜底
            var line = _window.AppendSystemNotice(text);
            if (line == null)
                ModMain.P("[ChatPresenter] 压缩反馈渲染失败（系统提示行模板缺失？）原文=" + text);
        }

        /// <summary>压缩忙态收尾：按钮还原 + 秒表字段清零（结果到达 / 超时 / 换 NPC / 关窗共用）。</summary>
        private void ClearCompactBusy()
        {
            _compacting = false;
            _compactNoticeBase = null;
            try { if (_window != null) _window.SetCompactBusy(false); } catch { }
        }

        /// <summary>压缩秒表（Update 每帧调）：提示行刷新"已 Ns"，超时兜底恢复按钮。
        /// 为什么要有：摘要 LLM 一次可跑 100s+，静态的「正在压缩对话历史…」与卡死无法区分。</summary>
        private void CompactWatch()
        {
            if (!_compacting) return;
            float el = Time.unscaledTime - _compactStartAt;
            if (el > CompactStuckSec)
            {
                ModMain.P("[ChatPresenter] 压缩超过 " + CompactStuckSec + "s 无回音 → 恢复按钮（结果若迟到仍会补一行提示）");
                if (_compactNotice != null) { _compactNotice.text = "压缩长时间无响应（可再试一次）"; _compactNotice = null; }
                ClearCompactBusy();
                return;
            }
            int sec = (int)el;
            if (sec == _compactLastSec) return;
            _compactLastSec = sec;
            if (_compactNotice != null && _compactNoticeBase != null)
                _compactNotice.text = _compactNoticeBase + "（已 " + sec + "s，摘要调用较慢，请勿重复点击）";
        }
        private int _compactLastSec = -1;

        private void OnStepEvent(JObject payload)
        {
            // 记回合号：npc_reply 帧不带 turn，"同回合同段正文只成泡一次"的去重判据靠它
            // （修 09-13 实机「step(text) 与 npc_reply 逐字相同 → 结果输出两遍」）
            _window.NoteTurn(payload["turn"]?.Value<int>() ?? -1);
            string kind = (string)payload["kind"] ?? "";
            switch (kind)
            {
                case "tool_call":
                    _window.BeginTurnProcess();
                    _window.AddToolCall(payload["name"]?.ToString() ?? "",
                                        payload["args"]?.ToString() ?? "");
                    break;
                case "tool_result":
                    _window.AddToolResult(payload["text"]?.ToString() ?? "",
                                          payload["isError"]?.Value<bool>() ?? false);
                    break;
                case "think":
                    // 思考整段（非流式后端）：独立折叠区展示，默认收起
                    _window.AppendThinkStatic(payload["text"]?.ToString() ?? "");
                    break;
                case "sys":
                    // 输入侧：系统组装（sections 全列），只实时不重放；换回合自动新建折叠块
                    AppendInputParts(payload, "sys", "系统组装");
                    break;
                case "ctx":
                    // 输入侧：运行时上下文差分（只含变化段）
                    AppendInputParts(payload, "ctx", "运行时上下文");
                    break;
                case "text":
                    // 非流式后端整段正文：无条件渲染为最终全文（2026-09-09 实证根因——
                    // 旧 `if (!_sawDelta)` 挡板把全文跳掉：只要之前来过任意 text_delta（哪怕
                    // 只有首行），step(text) 全文就被丢弃 → UI 永久停留在流式残留首行）。
                    // 覆盖语义：有活跃流式气泡则全文覆盖，无则新建气泡，同一正文双路到达无害。
                    string t = payload["text"]?.ToString() ?? "";
                    if (t.Length == 0) break;
                    _window.FinishStepProcess();
                    _window.FinishNpcBubble(t, false);
                    break;
            }
        }

        /// <summary>把 sys/ctx 的 parts 数组展开成输入折叠块行。sys=全段无变更标记；ctx=仅变化段。
        /// remove（段消失）显示“（已移除）”。</summary>
        private void AppendInputParts(JObject payload, string kind, string title)
        {
            int turn = payload["turn"]?.Value<int>() ?? 0;
            var arr = payload["parts"] as JArray;
            if (arr == null) return;
            foreach (var p in arr)
            {
                if (!(p is JObject o)) continue;
                string label = o["label"]?.ToString() ?? o["name"]?.ToString() ?? "?";
                string text = o["text"]?.ToString() ?? "";
                string op = o["op"]?.ToString() ?? "set";
                bool changed = kind == "ctx" && op == "set";
                if (kind == "ctx" && op == "remove") text = "（已移除）";
                _window.AppendInputPart(turn, kind, title, label, text, changed);
            }
        }

        private void OnDeltaEvent(JObject payload)
        {
            string tok = payload["text"]?.ToString() ?? "";
            if (tok.Length == 0) return;

            // 分通道：think 进「内心思量」折叠区（Header 实时涨字数）；body 进正文气泡
            string part = payload["part"]?.ToString() ?? "body";
            if (part == "think")
            {
                _window.AppendThinkDelta(tok);
                return;
            }

            // ：不再在 body delta 到达时立即 FinishThink（旧逻辑每 token 关一次
            // think 折叠 → think 流后续内容会新建第二条折叠，正文后出现重复 think）。
            // think 折叠的生命周期改为由回合收口统一关闭（OnReplyEvent），期间所有 think
            // delta 累积进同一个「内心思量」组。
            _window.FinishStepProcess();
            // 只在还没有活跃流式气泡时开新泡；BeginNpcBubble 是无条件新建，
            // 每个 delta 都调会把流式拆成一堆单 token 小气泡（实机截图实证）
            if (!_window.HasActiveBubble) _window.BeginNpcBubble("");
            _window.AppendDelta(tok);
        }

        private void OnReplyEvent(JObject payload)
        {
            // 解析必须全部在 try 之内（忙标卡死的真根因）
            //   Python 玩家消息回合固定发 `error: null`（`is_fallback or None`），而
            //   `payload["error"]?.Value<bool>()` 在**键存在、值为 JSON null** 时 `?.` **不会短路**
            //   —— `JValue.Value<bool>()` 对 null 值抛
            //   `InvalidCastException: Null object cannot be converted to a value type`。
            //   这行原先写在 try 之外 → 异常在进入 try 前抛出 → **`finally` 根本不执行** →
            //   `SetBusy(false)` 被跳过 → `_busy` 永久为真 → 删除/压缩入口永远提示"回合进行中"，
            //   且只有重启能恢复。气泡照常显示，因为正文走的是 `step(text)` 那条不碰这两个布尔的路径，
            //   所以现象极具迷惑性："对话明明完了"。
            //   两道保险：① 取值换成 Null 安全的 BoolOf；② 解析本身也挪进 try，将来任何解析异常
            //   都不会再绕过 finally。
            try
            {
                bool isError = BoolOf(payload, "error");
                bool initiative = BoolOf(payload, "initiative");
                string text = payload["text"]?.ToString() ?? "";

                // 回合收口：think 折叠统一在此定稿关闭（body delta 不再逐个中断 think，见 OnDeltaEvent）
                _window.FinishThink();
                _window.FinishStepProcess();
                if (isError)
                {
                    // 失败兜底帧：不留半截气泡，推系统提示（友好文案只进 event，账本不留）
                    if (_window.HasActiveBubble) _window.CancelActiveBubble();
                    _window.AppendSystemNotice(text.Length > 0 ? text : "（NPC 没有回应）");
                }
                else
                {
                    _window.FinishNpcBubble(text, initiative);
                }
            }
            finally
            {
                // 收口帧 = "这一回合没在飞了"的唯一权威信号
                //   忙标与「活跃流式泡」是删除/压缩入口守卫的两个分量（`_busy || HasActiveBubble`）。
                //   原先只复位忙标；活跃泡标记留在 `FinishNpcBubble` 内部清 —— 渲染一旦半途抛异常，
                //   标记就永久泄漏，表现为「对话明明完了，删除模式却一直说回合进行中」。
                //   两者同源（都表示"回合在飞"），收口时必须一起落。见 ChatWindow.EndTurnStreaming。
                try { _window.EndTurnStreaming(); } catch { }
                SetBusy(false, "回合收口");
                ModMain.P("[ChatPresenter] 收口复位完成 实例#" + _id
                          + " 忙(后)=" + _busy + " 活跃泡=" + (_window != null && _window.HasActiveBubble));

                // 收口 = 「这一回合没在飞了」的权威信号，也是**历史重放欠账该还的时刻**
                //   位置有意放在 EndTurnStreaming + SetBusy(false) **之后**：
                //   那一刻按定义没有东西在飞，`ReplaceHistory` 的 ResetContent 冲不掉任何直播内容。
                //   还账走同一条 `OpenChatAndReplay`（重新请求 → 拿到含本回合的最新投影），
                //   不新增路径、不套用旧响应（旧快照不含刚收口的这一回合，套用等于把回复擦掉）。
                //   若这次又撞上直播（下一回合已经开始），它会重新记上欠账，等再下一次收口。
                if (_wantReplay)
                {
                    _wantReplay = false;
                    ModMain.P("[ChatPresenter] 回合收口 → 补做挂起的历史回放 实例#" + _id);
                    try { OpenChatAndReplay(); }
                    catch (Exception e) { ModMain.P("[ChatPresenter] 补做历史回放异常: " + e.Message); }
                }
            }
        }

        // ------------------------------------------------------------------
        // 开窗激活 + 历史回放（C#→Python open_chat：get or create 激活活体并返回历史投影）
        // ------------------------------------------------------------------

        /// <summary>连接恢复后调用：把当前 NPC 的会话重新激活。
        ///
        /// 断线期间 `OpenChatAndReplay` 发出的 `open_chat` 会被 `WsClient.Send`
        /// 静默丢弃，而重连（`WsClient.Connected`）原先只被 ModMain 用来重拉 initiative 参数——
        /// 于是"连接回来了但会话没激活"，玩家接着发消息会落到一个未激活的 agent 上。
        /// 由 BrainLink 在「重新变成 Ready」的那一刻调用。</summary>
        public void ReopenAfterReconnect()
        {
            if (_window == null || _ws == null || !_ws.IsConnected) return;
            if (string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            ModMain.P("[ChatPresenter] 连接已恢复 → 重新激活会话 npc=" + _window.CurrentNpcId);
            OpenChatAndReplay();
        }

        /// <summary>开窗/换 NPC 时调：发 open_chat 激活该 NPC 的 agent（磁盘账本自动 resume）
        /// 并用响应回放历史（响应形状与旧 get_history 完全一致，回放代码不变）。</summary>
        private void OpenChatAndReplay()
        {
            if (_window == null || _ws == null)
            {
                // 这 8 个提前返回原先**一个日志都没有**，所以这次故障在 C# 侧零痕迹，
                // 只能靠 Python 侧那句 `open_chat：...历史回放 34 项` 反推"请求发了、被丢了"。
                // 这份代码自己立的规矩（ChatWindow.cs「不许静默失败」）在本函数内部从没执行过。
                ModMain.P("[ChatPresenter] 历史回放放弃：窗口或 WsClient 为空 ｜ 窗口=" + (_window != null)
                          + " 客户端=" + (_ws != null));
                return;
            }
            string npc = _window.CurrentNpcId;
            if (string.IsNullOrEmpty(npc))
            {
                ModMain.P("[ChatPresenter] 历史回放放弃：当前 NPC 名为空");
                return;
            }
            // 直播优先：**不丢弃，记欠账**。冲突解除的权威信号 = 回合收口的 finally
            //（见 OnReplyEvent 里那句「收口帧 = 这一回合没在飞了的唯一权威信号」）。
            if (_busy || _window.HasActiveBubble)
            {
                _wantReplay = true;
                ModMain.P("[ChatPresenter] 历史回放让位于直播 → 记欠账，回合收口后补 ｜ npc=" + npc
                          + " 忙=" + _busy + " 活跃泡=" + _window.HasActiveBubble);
                return;
            }

            // 真正发出去了 = 这份欠账已有人负责（回调若再撞上直播会重新记上）
            _wantReplay = false;

            JObject p = new JObject();
            p["npc_id"] = npc;
            p["limit"] = 0;   // 0 = 全部历史（09-13 用户要求"我要看全部的"；由 Python _parse_limit 解释）

            _ws.SendRequest("open_chat", p, resp =>
            {
                // 所有校验都在主线程回调里做：窗口已关/对象已切/直播已起
                if (_window == null || !_window.IsOpen)
                {
                    ModMain.P("[ChatPresenter] 历史回放响应丢弃：窗口已关 ｜ npc=" + npc
                              + " 窗口=" + (_window != null) + " 开=" + (_window != null && _window.IsOpen));
                    return;
                }
                if (resp["ok"]?.Value<bool>() != true)
                {
                    ModMain.P("[ChatPresenter] 历史回放响应失败 ｜ npc=" + npc
                              + " ok=" + resp["ok"] + " error=" + resp["error"]);
                    return;
                }
                var data = resp["data"] as JObject;
                if (data == null)
                {
                    ModMain.P("[ChatPresenter] 历史回放响应无 data ｜ npc=" + npc);
                    return;
                }
                if (data["npc_id"]?.ToString() != _window.CurrentNpcId)
                {
                    ModMain.P("[ChatPresenter] 历史回放响应丢弃：NPC 已切 ｜ 响应=" + data["npc_id"]
                              + " 当前=" + _window.CurrentNpcId);
                    return;
                }
                if (_busy || _window.HasActiveBubble)
                {
                    // 就是这一次造成了「主动开口开窗后没历史」（现场）
                    //   响应比首 token 晚了 → 让位给直播，但**记欠账**而不是丢弃。
                    _wantReplay = true;
                    ModMain.P("[ChatPresenter] 历史回放响应到达时已在直播中 → 记欠账，回合收口后补 ｜ npc="
                              + npc + " 忙=" + _busy + " 活跃泡=" + _window.HasActiveBubble);
                    return;
                }
                var items = data["items"] as JArray;
                ModMain.P("[ChatPresenter] 历史回放落地 ｜ npc=" + npc + " 条数=" + (items != null ? items.Count : 0)
                          + " 带统计=" + (data["stats"] != null));
                _window.ReplaceHistory(items ?? new JArray());
                // 首屏 token/时延基线（open_chat 响应的 stats 与 stats_update 同构），开窗即有数
                var stats = data["stats"] as JObject;
                if (stats != null) _window.SetStats(stats);
                // 删除模式入口流程：重放落地（每行已绑定回合号）→ 进选择模式
                if (_enterDeleteOnReplay)
                {
                    _enterDeleteOnReplay = false;
                    _window.EnterSelecting();
                }
            }, 10000);
        }

        // ------------------------------------------------------------------
        // 删除模式（微信式多选）：入口流程 + preview/delete 两段 RPC
        // 语义（对齐 Python history_ops）：删除以**完整回合**为单位——C# 只发 turn 编号，
        // seq 跨度由 Python 从账本换算（绝不信任裸 seq）；preview_delete_history 预检
        // 并给确认摘要（含「将同时删除折叠记忆」），delete_history 执行后**直接回新历史**
        // （C# 一次往返完成重放，不再二次 open_chat）。完整性与配对由 Python
        // plan_delete fail-closed 兜底（模拟新目录不平衡 → 整体拒绝，账本零写入）。
        // 响应帧双层语义：传输层 ok=false + error（压缩中/回合中/未激活等异常）；
        // 传输层 ok=true + data.ok=false + reason（配对 fail-closed）。
        // ------------------------------------------------------------------

        /// <summary>标题栏 🗑：选择模式中=退出；否则空闲才进（忙时拒绝）。进入前先静默重放一次，
        /// 让刚产生的直播行也绑上账本回合号（否则它们不可选）。</summary>
        private void OnDeleteBtnClicked()
        {
            if (_window == null || _ws == null || string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            if (_window.Selecting)
            {
                _window.ExitSelecting();
                return;
            }
            if (_busy || _window.HasActiveBubble)
            {
                // 留证据：这个守卫此前**哪一项为真完全没日志**，"对话都完了为什么还说回合进行中"
                // 只能靠推理。两个分量分开打，一眼能看出是忙标没落还是活跃泡标记泄漏。
                try
                {
                    ModMain.P("[ChatPresenter] 删除入口被拒：忙=" + _busy
                              + " 活跃泡=" + _window.HasActiveBubble + " npc=" + _window.CurrentNpcId);
                }
                catch { }
                _window.AppendSystemNotice("回合进行中，结束后再进入删除模式");
                return;
            }
            _enterDeleteOnReplay = true;
            OpenChatAndReplay();
        }

        private int _previewReq;   // preview 请求序号：回执落地前选择集变了就丢弃（防过期摘要误导）

        /// <summary>确认按钮第一击武装时取弹窗数据：preview_delete_history → 摘要写计数位；
        /// 预检失败（配对不平衡/回合不存在）→ 解除武装 + 系统提示原因</summary>
        private void OnDeletePreviewRequested(List<int> turns)
        {
            if (_window == null || _ws == null || turns == null || turns.Count == 0) return;
            if (string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            int req = ++_previewReq;
            JObject p = new JObject();
            p["npc_id"] = _window.CurrentNpcId;
            p["turns"] = ToJArray(turns);
            _ws.SendRequest("preview_delete_history", p, resp =>
            {
                if (_window == null || !_window.IsOpen || !_window.Selecting) return;   // 已退出模式：回执作废
                if (req != _previewReq) return;                                          // 期间选择集已变：过期回执
                string err = RpcError(resp);
                if (err != null)
                {
                    _window.ApplyDeletePreview(false, null);
                    _window.AppendSystemNotice("无法删除：" + err);
                    return;
                }
                _window.ApplyDeletePreview(true, BuildPreviewSummary(resp["data"]?["preview"] as JObject));
            }, 10000);
        }

        /// <summary>两段确认后执行：delete_history（载荷=回合号）→ 成功=退出模式 + 用响应里的
        /// 新历史直接重放（一次往返）+ 提示；失败=提示并留在选择模式</summary>
        private void OnDeleteConfirmed(List<int> turns)
        {
            if (_window == null || _ws == null || turns == null || turns.Count == 0) return;
            if (string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            JObject p = new JObject();
            p["npc_id"] = _window.CurrentNpcId;
            p["turns"] = ToJArray(turns);
            p["reason"] = "user";
            p["limit"] = 0;    // 与 open_chat 同宽：删完重放也回全部（否则删一次就把更早的挤没了）
            _ws.SendRequest("delete_history", p, resp =>
            {
                if (_window == null || !_window.IsOpen) return;
                string err = RpcError(resp);
                if (err != null)
                {
                    _window.AppendSystemNotice("删除失败：" + err);
                    return;   // 留在选择模式，用户可改选重试
                }
                var data = resp["data"] as JObject;
                if (data?["npc_id"]?.ToString() != _window.CurrentNpcId) return;
                int n = (data["deleted"]?["turns"] as JArray)?.Count ?? turns.Count;
                var items = data["history"]?["items"] as JArray;
                _window.ExitSelecting();
                _window.ReplaceHistory(items ?? new JArray());
                // 删除是「内存账本追加注记」：真正写进 session jsonl 的时机是游戏存档
                // （save_happened→flush_all）——提示里写明，免得以为立刻落盘了
                _window.AppendSystemNotice("已删除 " + n + " 个回合（随游戏存档固化）");
            }, 10000);
        }

        /// <summary>统一解 RPC 错误帧：传输层失败取顶层 error（异常路径），业务失败取 data.reason
        /// （plan fail-closed 路径）；两者都无则给通用文案。null=成功</summary>
        private static string RpcError(JObject resp)
        {
            if (resp == null) return "无响应";
            if (resp["ok"]?.Value<bool>() != true)
                return resp["error"]?.ToString() ?? "未知错误";
            var data = resp["data"] as JObject;
            if (data != null && data["ok"]?.Value<bool>() == false)
                return data["reason"]?.ToString() ?? "删除方案未通过校验";
            return null;
        }

        private static JArray ToJArray(List<int> list)
        {
            var arr = new JArray();
            foreach (var t in list) arr.Add(t);
            return arr;
        }

        /// <summary>
        /// 从 `JObject` 安全取 bool：**键缺失、值为 JSON null、类型不符，一律当 false**。
        ///
        /// **绝不能**写 `o[k]?.Value&lt;bool&gt;() ?? false` —— 键存在但值是 JSON `null` 时
        /// `?.` 不会短路（`o[k]` 返回的是 `JValue` 实例，不是 C# null），随后
        /// `JValue.Value&lt;bool&gt;()` 对 null 值调 `Convert.ChangeType` 抛
        /// `InvalidCastException: Null object cannot be converted to a value type`。
        /// 「对话完了还说回合进行中」的根因就是它在 `OnReplyEvent` 的 try 之外炸掉，
        /// 连带 `finally` 里的忙标复位一起被跳过（详见 OnReplyEvent 注释）。
        /// </summary>
        private static bool BoolOf(JObject o, string key)
        {
            try
            {
                var t = o?[key];
                if (t == null || t.Type == JTokenType.Null) return false;
                return t.Value<bool>();
            }
            catch { return false; }
        }

        /// <summary>预览摘要（plan_delete 的 preview 报告 → 一行确认文案）：
        /// 将删条数/字数 + 纪要连带提示（全覆盖时连折叠记忆一起忘，用户必须知情）</summary>
        private static string BuildPreviewSummary(JObject plan)
        {
            if (plan == null) return null;
            int nodes = plan["removed_nodes"]?.Value<int>() ?? 0;
            int chars = plan["removed_chars"]?.Value<int>() ?? 0;
            int droppedSummaries = (plan["dropped_summaries"] as JArray)?.Count ?? 0;
            string s = "将删 " + nodes + " 条 · 约 " + chars + " 字";
            if (droppedSummaries > 0) s += " · 含折叠记忆 " + droppedSummaries + " 条";
            return s;
        }

        /// <summary>通知 Python 销毁该 NPC 的活体（关窗=落盘销毁；回合在跑由 Python 侧延至回合毕）。
        /// fire-and-forget：响应被忽略，但回调不可为 null（WsClient 全路径都会调它）。</summary>
        internal static void DisposeAgentOnServer(WsClient ws, string npcId)
        {
            if (ws == null || string.IsNullOrEmpty(npcId)) return;
            JObject p = new JObject();
            p["npc_id"] = npcId;
            ws.SendRequest("dispose_agent", p, _ => { }, 10000);
        }

        private void SetBusy(bool busy, string why = "")
        {
            if (_busy != busy)
            {
                try
                {
                    ModMain.P("[ChatPresenter] 忙 " + (busy ? "ON" : "OFF")
                              + " 实例#" + _id + (busy ? " ← " + why : ""));
                }
                catch { }
            }
            _busy = busy;
            if (busy) _busySince = Time.unscaledTime;
            if (_window != null) _window.SetBusy(busy);
        }

        /// <summary>
        /// 忙标自愈：`_busy` 唯一的清点是回合收口帧。一旦收口帧**没到**（presenter 未订阅 /
        /// 被守卫丢弃），它就是个死标志 —— 表现是"对话明明完了，删除/压缩永远提示回合进行中"，
        /// 而且**只有重启游戏才能恢复**。这里给一条有界兜底：只要连着 `BusyStaleSeconds` 一条帧都没
        /// 收到（真回合不可能这么久不吐一个字），就认定这个忙标是死的，主动放行。
        /// 判据用"最近一次收到帧的时刻"而不是"置忙时刻"—— 长回合会一直吐帧，不会被误杀。
        /// </summary>
        private void BusyWatchdog()
        {
            if (!_busy) return;
            float last = _busySince > _lastFrameAt ? _busySince : _lastFrameAt;
            if (Time.unscaledTime - last < BusyStaleSeconds) return;
            ModMain.P("[ChatPresenter] ★忙标自愈★ 实例#" + _id + " 已 " + (int)(Time.unscaledTime - last)
                      + "s 无任何帧 → 强制复位（收口帧大概率丢了，查上面的 ★帧被丢弃 / Init 订阅）");
            SetBusy(false, "自愈");
        }
    }
}