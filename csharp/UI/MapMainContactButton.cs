/// <summary>
/// 主界面 HUD「传音簿」按钮注入 —— Harmony Postfix on UIMapMainPlayerInfo.Init。
///
/// 目标（用户需求）：主 HUD 加一个「传」按钮，点击打开通讯录（ContactPanelOpener.Toggle）。
///
/// 结构依据（结构 dump + 命中日志实证，路径已固化，不再递归搜索/全树探针）：
///   修为条文本所在容器 = MapMain/Group:PlayerInfo/G:goPlayerInfo/LanguageGroup/G:btnFateFeature
///     （btnFateFeature 内嵌“炼气初期”等短文本；LanguageGroup 即修为条容器所在父层）
///   模板圆钮 = 同链 G:goPlayerInfo 下 G:btnEmail（信件类图标贴合传音语义）
///   → 传钮落点：修为容器（G:btnFateFeature）右缘外，挂进 LanguageGroup。
///   实证命中：容器=G:btnFateFeature，注入后 pos=(86,2)；本次按用户反馈右移
///   （右缘间隙 12 → 36，中心右移约 24px，避开 修为/气运 文字区）。
///
/// 实现要点（沿用成熟范式）：
///  ① 挂点：UIMapMainPlayerInfo.Init 每次主界面 HUD 构建必调用；注入前先清同父旧钮（HUD 重建幂等）。
///  ② 模板：G:btnEmail 直取（G:goPlayerInfo 直接子级），退而 G:btnTask/G:btnBag/G:btnMartial。
///  ③ 落点：修为容器右缘外（显式 +X 公式，不再用邻钮差值——环形布局邻钮差值会落进圆环槽位）。
///  ④ 点击：三步写法（ClickUtils）→ ContactPanelOpener.Toggle()；通讯录未就绪（AB 装配失败）
///     时由 Opener 记日志，无任何代码版回退（按需求）。
/// </summary>
using System;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    [HarmonyPatch(typeof(UIMapMainPlayerInfo), nameof(UIMapMainPlayerInfo.Init))]
    internal static class MapMainContactButton
    {
        private const string ButtonName = "AgentLoopContactButton";
        /// <summary>按钮文案（热键字母位置）；圆形钮单字最贴</summary>
        private const string Label = "传";

        /// <summary>最近一次注入的实例（诊断开关要即时移除它，见 RemoveInjected）。</summary>
        private static GameObject _lastInjected;

        // ------------------------------------------------------------------
        // 未读角标（重做：真圆 + 白描边 + 呼吸；修「读完不灭」）
        //
        // 旧实现三个毛病（用户反馈"好丑陋"）：
        //   ① `new Image()` 没给 sprite —— Unity 的 Image 在 sprite==null 时退化走
        //      Graphic.OnPopulateMesh，画的是**纯色矩形**，所以那不是"红点"是"红方块"；
        //   ② 锚在按钮 RectTransform 的右上角，而模板 G:btnEmail 是**圆钮**——圆钮矩形
        //      四角是透明区，14×14 方块基本悬在铜环**外面**（-4,-4 只压进角 4px）；
        //   ③ 尺寸颜色全写死（14px / #F24040），和面板行红点、顶部横幅圆点（都是
        //      ChatUiBuilder.MakeCircleSprite 抗锯齿真圆 + #FF3B30）不是一套。
        // 现方案：真圆 sprite 落在 **45° 铜环上**（圆心压在环线上，半个角标压住按钮），
        // 外圈暖白描边保证任何底色上都读得出，直径按可见圆钮直径实算，并做 1.2s 呼吸。
        //
        // 显隐由帧回调**轮询** UnreadStore.Any 驱动（不再只在"注入时/登记未读时"写）：
        // 旧写法下"打开对话即已读"走的是 UnreadStore.Clear()，没人通知 HUD → 读完红点不灭。
        // ------------------------------------------------------------------

        /// <summary>角标直径 / 可见圆钮直径。约 1/3 是角标的舒适区（再大就压住图标）。</summary>
        private const float BadgeRatio = 0.30f;
        private const float BadgeMin = 11f;      // 圆钮很小（或读不到尺寸）时的下限
        private const float BadgeMax = 20f;      // 上限：再大就抢图标了
        /// <summary>呼吸周期（s）与幅度：缩放 1.00↔1.12，透明度 0.88↔1.00。</summary>
        private const float BreathPeriod = 1.2f;
        private const float BreathAmp = 0.12f;

        private static GameObject _dot;          // 角标根（缩放在它身上，pivot 居中所以圆心不动）
        private static Image _dotFill;           // 内层红圆（透明度呼吸）
        private static Sprite _dotCircle;        // 圆 sprite 缓存（MakeCircleSprite 每次都新建 Texture2D）
        private static bool _dotShown;           // 最近一次应用到实体的显隐（帧回调比对用）
        private static bool _ticking;            // 帧回调只注册一次（闭包只引用静态，宿主销毁也不悬挂）
        // （构建失败的去重改用 _lastBail，见 LogOnce）

        /// <summary>诊断用：立刻移除已注入的按钮（二分排查世界输入失效）。</summary>
        internal static void RemoveInjected()
        {
            try { if (_lastInjected != null) UnityEngine.Object.Destroy(_lastInjected); }
            catch { }
            _lastInjected = null;
            _dot = null; _dotFill = null; _dotShown = false;
        }

        /// <summary>
        /// 未读角标显隐（HUD「传」钮右上环上）：通讯录面板关闭态时，未读提醒的唯一可见载体
        /// （旧横幅是面板子节点，面板根 OFF 后显示不出来）。由帧回调按 UnreadStore.Any 驱动，
        /// 也保留公开入口给注入完成时同步一次。
        /// </summary>
        public static void SetUnread(bool on)
        {
            try
            {
                _dotShown = on;
                if (_lastInjected == null)
                {
                    // 不静默（教训）：角标"该亮却没亮"排查过一次，元凶就是这类无声 return
                    if (on) LogOnce("noBtn", "未读角标：HUD 传音簿按钮未注入/已销毁 → 无处可挂（on=true 已忽略）");
                    return;
                }
                if (_dot == null)                 // 没建过，或 HUD 重建后被销毁（Unity fake-null）
                {
                    _dotFill = null;
                    // 无未读也照样建（建成后隐藏）：把"建不出来"暴露在**注入那一刻**，
                    // 而不是拖到第一条未读到达时才无声失败（之前的写法就是这样藏的）
                    BuildBadge();
                    if (_dot == null) return;
                }
                _dot.SetActive(on);
                if (on) ApplyBreath();
            }
            catch (Exception e) { ModMain.P("[MapMainContactButton] SetUnread: " + e); }
        }

        /// <summary>同类"没做成"的原因只打一次（帧回调会反复调，防刷屏）；成功后复位。</summary>
        private static string _lastBail;
        private static void LogOnce(string key, string msg)
        {
            if (_lastBail == key) return;
            _lastBail = key;
            ModMain.P("[MapMainContactButton] " + msg);
        }

        /// <summary>
        /// 建角标：真圆 + 白描边，圆心落在 45° 铜环上。
        /// 可见直径优先取"带贴图的最大的那个子层"（图标层，通常就是可见圆盘）——
        /// 若拿不到（子层太小/无贴图）就退回按钮 RectTransform 的短边。两者都进日志，
        /// 便于按真实值微调（BadgeRatio/BadgeMin/BadgeMax）。
        /// </summary>
        private static void BuildBadge()
        {
            try
            {
                // IL2CPP 坑（实机定位的红点不亮元凶）
                // 旧写法 `_lastInjected.transform as RectTransform` 在本环境**静默返回 null**：
                // `GameObject.transform` 给的是 Transform 包装，向下转型拿不到 RectTransform →
                // 函数第一行就 return，角标永不创建、且日志一行不打（横幅照常，唯独红点失踪）。
                // 正确取法 = GetComponent<RectTransform>()（注入时就是这么取的，实机验证可行）。
                var host = _lastInjected.GetComponent<RectTransform>();
                if (host == null) host = _lastInjected.transform as RectTransform;   // 兜底（别的运行环境）
                if (host == null)
                {
                    LogOnce("noHost", "未读角标构建失败：取不到按钮 RectTransform（GetComponent 与 transform 转型都为空）");
                    return;
                }

                // 按钮本体矩形（anchoredPosition 的参照系；角标仍挂根上，避开子层可能的圆形 Mask 裁剪）
                float W = host.rect.width, H = host.rect.height;
                if (W < 1f) W = Mathf.Abs(host.sizeDelta.x);
                if (H < 1f) H = Mathf.Abs(host.sizeDelta.y);
                if (W < 1f || H < 1f)
                {
                    LogOnce("noSize", "未读角标构建失败：按钮矩形尺寸读不到（W=" + W + " H=" + H +
                                      "）—— rect 与 sizeDelta 都为空");
                    return;
                }

                // 可见圆盘直径：扫子孙 ≤2 层里带 sprite 的最大者（须小于根短边，才可能是"图标层"）
                float rootShort = Mathf.Min(W, H);
                float vis = rootShort;
                string visFrom = "(root " + W.ToString("F0") + "×" + H.ToString("F0") + ")";
                float best = 0f; string bestName = null;
                FindDisc(host, rootShort, 2, ref best, ref bestName);
                if (best > rootShort * 0.4f)   // 够大才认作可见圆盘，否则退回根尺寸
                {
                    vis = best;
                    visFrom = "(icon " + bestName + " " + best.ToString("F0") + ")";
                }

                float R = vis * 0.5f;
                float d = Mathf.Clamp(vis * BadgeRatio, BadgeMin, BadgeMax);
                float ringW = Mathf.Max(1.5f, d * 0.10f);
                const float cos45 = 0.7071068f;

                var go = new GameObject("AgentLoopUnreadDot");
                var rt = go.AddComponent<RectTransform>();
                rt.SetParent(host, false);
                rt.anchorMin = new Vector2(1f, 1f);
                rt.anchorMax = new Vector2(1f, 1f);
                rt.pivot = new Vector2(0.5f, 0.5f);
                rt.sizeDelta = new Vector2(d, d);
                // 45° 环上：按钮中心 = 右上角锚点 - (W/2, H/2)；环点 = 中心 + R·(cos45, cos45)
                rt.anchoredPosition = new Vector2(-W * 0.5f + cos45 * R, -H * 0.5f + cos45 * R);

                var ring = go.AddComponent<Image>();
                ring.sprite = DotCircle();
                ring.color = UnreadStore.UnreadRing;   // 暖白外圈（描边）
                ring.type = Image.Type.Simple;
                ring.raycastTarget = false;            // 纯指示，不挡点击

                var fillGo = new GameObject("Fill");
                var frt = fillGo.AddComponent<RectTransform>();
                frt.SetParent(rt, false);
                frt.anchorMin = new Vector2(0.5f, 0.5f);
                frt.anchorMax = new Vector2(0.5f, 0.5f);
                frt.pivot = new Vector2(0.5f, 0.5f);
                frt.sizeDelta = new Vector2(d - ringW * 2f, d - ringW * 2f);
                frt.anchoredPosition = Vector2.zero;
                var fill = fillGo.AddComponent<Image>();
                fill.sprite = DotCircle();
                fill.color = UnreadStore.UnreadRed;    // #FF3B30
                fill.type = Image.Type.Simple;
                fill.raycastTarget = false;

                _dot = go;
                _dotFill = fill;
                _lastBail = null;      // 成功了：允许下一次失败重新出声
                ModMain.P("[MapMainContactButton] 未读角标已建：真圆+白描边 直径=" + d.ToString("F0") +
                          " 环宽=" + ringW.ToString("F1") + " 可见圆=" + vis.ToString("F0") + visFrom +
                          " 落点=(" + rt.anchoredPosition.x.ToString("F0") + "," + rt.anchoredPosition.y.ToString("F0") + ")");
            }
            catch (Exception e) { LogBuildFail(e.Message); }
        }

        /// <summary>
        /// 在 root 的子孙（≤depth 层）里找"最大的带贴图层"，要求尺寸小于根短边（等大的就是背景框，
        /// 不算图标层）。HUD 圆钮的可见圆盘常在 root→Icon→Img 这种两层嵌套里，故扫两层。
        /// 命中的尺寸/名字会进日志，便于按真实值微调落点。
        /// </summary>
        private static void FindDisc(Transform parent, float rootShort, int depth, ref float best, ref string bestName)
        {
            if (parent == null || depth <= 0) return;
            int n = 0;
            try { n = parent.childCount; } catch { return; }
            for (int i = 0; i < n; i++)
            {
                Transform ch = null;
                try { ch = parent.GetChild(i); } catch { continue; }
                if (ch == null) continue;
                try
                {
                    var ci = ch.GetComponent<Image>();
                    var cr = UiRects.Of(ch);   // ★09-13★ 旧写法 `ch as RectTransform` 恒 null（IL2CPP 铁律）
                    if (ci != null && ci.sprite != null && cr != null)
                    {
                        float cs = Mathf.Min(cr.rect.width, cr.rect.height);
                        if (cs > best && cs < rootShort * 0.999f) { best = cs; bestName = ch.name; }
                    }
                }
                catch { }
                FindDisc(ch, rootShort, depth - 1, ref best, ref bestName);
            }
        }

        private static void LogBuildFail(string why)
        {
            LogOnce("build:" + why, "未读角标构建失败：" + why);
        }

        /// <summary>圆 sprite（白底 alpha 圆，靠 Image.color 上色）；缓存避免每次新建 Texture2D。</summary>
        private static Sprite DotCircle()
        {
            if (_dotCircle == null) _dotCircle = ChatUiBuilder.MakeCircleSprite(Color.white, 64);
            return _dotCircle;
        }

        /// <summary>呼吸：1.2s 周期，缩放 1.00↔1.12（绕 pivot 缩放，圆心不动）+ 红圆透明度 0.88↔1.00。</summary>
        private static void ApplyBreath()
        {
            if (_dot == null) return;
            float s01 = 0.5f + 0.5f * Mathf.Sin(Time.unscaledTime * (6.2831853f / BreathPeriod));
            float k = 1f + BreathAmp * s01;
            _dot.transform.localScale = new Vector3(k, k, 1f);
            if (_dotFill != null)
            {
                var c = UnreadStore.UnreadRed;
                c.a = 0.88f + 0.12f * s01;
                _dotFill.color = c;
            }
        }

        /// <summary>
        /// 帧回调：① 未读集合与上一次应用的显隐不一致（或实体被 HUD 重建销毁）→ 立刻重建/亮灭，
        /// 这就是"读完即灭"的修复点；② 亮着时做呼吸。
        /// </summary>
        private static void Tick()
        {
            try
            {
                bool want = UnreadStore.Any;
                if (want != _dotShown || (want && _dot == null)) SetUnread(want);
                if (!want) return;
                ApplyBreath();
            }
            catch (Exception e) { ModMain.P("[MapMainContactButton] Tick: " + e.Message); }
        }

        private static void EnsureTick()
        {
            if (_ticking) return;
            _ticking = true;
            try { g.timer.Frame(new Action(Tick), 1, true); }
            catch (Exception e) { _ticking = false; ModMain.P("[MapMainContactButton] EnsureTick: " + e.Message); }
        }

        // 修为容器路径段（自 MapMain 起逐级“直接子级按名”，无递归）
        private const string SegPlayerInfo = "Group:PlayerInfo";
        private const string SegGoPlayer = "G:goPlayerInfo";
        private const string SegLanguage = "LanguageGroup";
        private const string SegRealmBox = "G:btnFateFeature";   // 修为文本所在容器（2026-09-07 实证）
        /// <summary>修为容器右缘外的固定间隙：12→36（用户反馈“更右边一点会更好”，中心右移约 24px）</summary>
        private const float RightGap = 36f;

        /// <summary>模板候选（同父 G:goPlayerInfo 直接子级按名直取；首项 G:btnEmail=信件图标）</summary>
        private static readonly string[] TemplateNames = { "G:btnEmail", "G:btnTask", "G:btnBag", "G:btnMartial" };

        /// <summary>首轮失败后的轮询间隔（s）。境界文本晚建约 1.5s 内；0.3s 起步尽量贴就绪时刻。</summary>
        private static readonly float[] RetryDelays = { 0.3f, 0.8f, 1.5f, 3.0f };

        private static void Postfix(UIMapMainPlayerInfo __instance, Transform transform)
        {
            try
            {
                if (transform == null) return;
                if (TryInject(transform, false)) return;
                ModMain.P("[MapMainContactButton] 首轮未命中（境界条晚建），0.3/0.8/1.5/3.0s 轮询补注...");
                ScheduleRetry(transform, 0);
            }
            catch (Exception e)
            {
                ModMain.P("[MapMainContactButton] inject: " + e);
            }
        }

        /// <summary>轮询补注：境界文本晚建，按 RetryDelays 逐档补；注入成功/销毁/档位耗尽即停。</summary>
        private static void ScheduleRetry(Transform hud, int idx)
        {
            try
            {
                if (idx < 0 || idx >= RetryDelays.Length) return;
                float delay = RetryDelays[idx];
                g.timer.Time(new Action(() =>
                {
                    try
                    {
                        if (hud == null) return;
                        try { var _ = hud.childCount; } catch { return; } // HUD 已销毁
                        ModMain.P("[MapMainContactButton] 轮询补注#" + (idx + 1) + "（+" + delay + "s）...");
                        if (TryInject(hud, idx >= RetryDelays.Length - 1)) return;
                        ScheduleRetry(hud, idx + 1);
                    }
                    catch (Exception e) { ModMain.P("[MapMainContactButton] 轮询终止: " + e.Message); }
                }), delay, false);
            }
            catch (Exception e) { ModMain.P("[MapMainContactButton] 排轮询: " + e.Message); }
        }

        /// <summary>
        /// 完整注入（首轮与轮询共用）。finalAttempt=true（最后一档）仍找不到修为容器时
        /// 才落“模板右侧”兜底；之前几档找不到就返回 false 等下一轮。成功返回 true。
        /// </summary>
        private static bool TryInject(Transform transform, bool finalAttempt)
        {
            try
            {
                if (transform == null) return false;

                // ① 固化路径下行：先爬到 MapMain，再逐级直取（不递归、不整树探针）
                Transform mapMain = ClimbToNamed(transform, "MapMain", 12);
                Transform root = mapMain ?? transform;
                Transform playerInfo = DescendTo(root, SegPlayerInfo);
                Transform goPlayer = playerInfo != null ? DirectChildByName(playerInfo, SegGoPlayer) : DirectChildByName(root, SegGoPlayer);
                if (goPlayer == null)
                {
                    ModMain.P("[MapMainContactButton] G:goPlayerInfo 未就绪（HUD 晚建）");
                    return false;
                }
                Transform languageGroup = DirectChildByName(goPlayer, SegLanguage);
                Transform realmBox = languageGroup != null ? DirectChildByName(languageGroup, SegRealmBox) : null;

                // ② 幂等：清掉本 HUD 旧注入钮（同语言组内；旧版兜底曾落 G:goPlayerInfo 下，一并清）
                if (languageGroup != null) DestroyDirectChild(languageGroup, ButtonName);
                DestroyDirectChild(goPlayer, ButtonName);

                // ②b 诊断开关：禁用 HUD 按钮时不注入并移除已有
                if (DiagSwitches.NoHudButton)
                {
                    RemoveInjected();
                    return true;   // 视作已处理，停止轮询
                }

                // ③ 模板圆钮：G:goPlayerInfo 直接子级按名直取（信件类优先）
                Button tpl = null;
                Transform tplGo = null;
                foreach (var name in TemplateNames)
                {
                    tplGo = DirectChildByName(goPlayer, name);
                    if (tplGo != null)
                    {
                        tpl = tplGo.GetComponent<Button>();
                        if (tpl != null) break;
                    }
                }
                if (tpl == null) tpl = goPlayer.GetComponentInChildren<Button>(true);
                if (tpl == null)
                {
                    ModMain.P("[MapMainContactButton] 未找到可克隆的圆钮模板，稍后重试");
                    return false;
                }

                var srcRt = tpl.GetComponent<RectTransform>();
                GameObject clone = UnityEngine.Object.Instantiate(tpl.gameObject);
                clone.name = ButtonName;
                var rt = clone.GetComponent<RectTransform>();
                if (rt == null) { ModMain.P("[MapMainContactButton] 模板无 RectTransform，跳过注入"); return false; }

                rt.localScale = srcRt.localScale;

                // ④ 落点：修为容器右缘外（用户需求）；最后一档兜底 = 模板正右侧一个身位
                if (realmBox != null && realmBox.parent != null)
                {
                    var arc = realmBox.GetComponent<RectTransform>();
                    Transform aparent = realmBox.parent;   // = LanguageGroup
                    rt.SetParent(aparent, false);
                    if (arc != null)
                    {
                        rt.anchorMin = arc.anchorMin;
                        rt.anchorMax = arc.anchorMax;
                        rt.pivot = arc.pivot;
                        rt.sizeDelta = srcRt.sizeDelta;
                        rt.anchoredPosition = new Vector2(
                            arc.anchoredPosition.x + arc.sizeDelta.x * 0.5f + srcRt.sizeDelta.x * 0.5f + RightGap,
                            arc.anchoredPosition.y);
                        ModMain.P("[MapMainContactButton] 修为锚定命中（容器=" + realmBox.name +
                                  "，文本区右缘外，间隙=" + RightGap + "）");
                    }
                    else
                    {
                        rt.anchoredPosition = srcRt.anchoredPosition + new Vector2(srcRt.sizeDelta.x + 12f, 0f);
                    }
                }
                else if (!finalAttempt)
                {
                    // 修为容器晚建：先不钉模板右侧（会挤进左环），等轮询
                    UnityEngine.Object.Destroy(clone);
                    ModMain.P("[MapMainContactButton] 修为容器未建出，等轮询（本次不注入）");
                    return false;
                }
                else
                {
                    // 修为容器仍缺失（最终档）：走模板右侧兜底
                    rt.SetParent(srcRt.parent, false);
                    rt.anchorMin = srcRt.anchorMin;
                    rt.anchorMax = srcRt.anchorMax;
                    rt.pivot = srcRt.pivot;
                    rt.sizeDelta = srcRt.sizeDelta;
                    rt.anchoredPosition = srcRt.anchoredPosition + new Vector2(srcRt.sizeDelta.x + 12f, 0f);
                    ModMain.P("[MapMainContactButton] 修为锚定未命中，走模板右侧（最终档兜底）");
                }

                // 克隆体残留的组机制组件销毁（保留 Button/Image/Text 视觉）
                NpcPanelButton.StripOperationItemPublic(clone);

                var label = clone.GetComponentInChildren<Text>(true);
                if (label != null) label.text = Label;
                // 游戏 HUD 文本用的是 TextMeshPro（实证：只找老版 Text 会改字失败）
                var tmpLabel = clone.GetComponentInChildren<TMPro.TextMeshProUGUI>(true);
                if (tmpLabel != null) tmpLabel.text = Label;

                var btn = clone.GetComponent<Button>() ?? clone.AddComponent<Button>();
                ClickUtils.Attach(btn, () =>
                {
                    try { ContactPanelOpener.Toggle(); }
                    catch (Exception e) { ModMain.P("[MapMainContactButton] toggle: " + e); }
                });

                ModMain.P("[MapMainContactButton] HUD 传音簿按钮已注入（模板=" + tpl.name +
                          "，父=" + rt.parent.name +
                          "，位置=(" + rt.anchoredPosition.x + "," + rt.anchoredPosition.y + ")" +
                          "，尺寸=(" + rt.rect.width.ToString("F0") + "×" + rt.rect.height.ToString("F0") + ")）");
                DumpClone(clone, tpl);   // 克隆体体检：悬停三件套为什么没有（09-13）
                _lastInjected = clone;
                _dot = null; _dotFill = null; _dotShown = false;   // HUD 重建：旧角标随旧钮一起销毁
                SetUnread(UnreadStore.Any);   // 注入时同步未读状态（角标）
                EnsureTick();                 // 帧驱动：未读变化即时亮/灭 + 呼吸
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[MapMainContactButton] inject: " + e);
                return false;
            }
        }

        /// <summary>
        /// 克隆体体检——游戏 HUD 按钮的「呼吸 / 鼠标悬停跳起 / 中文名标签」三件套
        /// **全归 `UIOperationGroup` + `UIOperationItem` 管**（类型定义 dump 实证，见 `docs/APPENDIX.md` D.3）：
        ///   · 跳起 = 组的 `guangbiao` / `guangbiaoPos` / `guangbiaoSpeed` + 项的 `showFocal`，
        ///     由 `GetFocal(item)` 搬到当前项（还有 `openRightFocal*` 一组方向参数）；
        ///   · 中文标签 = 组的 `autoTips` + `ChangeAutoTips()` + 项的 `isTips` / `values`；
        ///   · 悬停入口 = 项的 `ISwitchOnPointerEnter`/`ISwitchOnPointerExit`、`UIEventListener`、
        ///     `OnnEnterItem()`/`OnnExitItem()`。
        /// 而本类的克隆**把 `UIOperationItem`/`UIEventListener` 删了**（`StripOperationItemPublic`，
        /// 为规避"放进受管网格 → 可见但点不响 / 世界输入被门控"那条真机铁律），且**从未加入
        /// `operationItems`** → 组永远不会 `ActiveItem` 它 → 三件套一样都没有。
        /// 本函数把判据一次打全：克隆体根上还剩哪些组件、子节点名单（含疑似游戏"新"标记）、
        /// 以及父链上有没有 `UIOperationGroup`。改走"正式入组"还是"自绘"，看这两行再定。
        /// </summary>
        private static void DumpClone(GameObject clone, Button tpl)
        {
            try
            {
                var sb = new System.Text.StringBuilder("[MapMainContactButton] 克隆体体检：根组件=[");
                AppendComponentNames(clone, sb);
                sb.Append("] 模板根组件=[");
                if (tpl != null) AppendComponentNames(tpl.gameObject, sb);
                sb.Append("] 父链组=").Append(FindGroupName(clone.transform));
                ModMain.P(sb.ToString());

                var kids = new System.Text.StringBuilder("[MapMainContactButton] 克隆体子节点：");
                CollectKids(clone.transform, kids, 0);
                ModMain.P(kids.ToString());
            }
            catch (Exception e) { ModMain.P("[MapMainContactButton] DumpClone: " + e.Message); }
        }

        /// <summary>
        /// **真实**组件类型名。`c.GetType().Name` 在 IL2CPP 下恒为**声明类型**——
        /// `GetComponents&lt;Component&gt;()` 返回的是按 T 包装的代理，`GetType()` 给的是 `Component`
        /// 而不是运行时类。09-13 我的探针因此打出 7 个 `Component`，等于没打；
        /// 必须以 `GetIl2CppType().Name` 取运行时类名（`AbChatPanel.cs:196` 早就是这么写的）。
        /// ⚠ 同一个坑也压在 `FindGroupName` 与 `NpcPanelButton.StripOperationItem` 上（见各自注释）。
        /// </summary>
        private static string TypeName(Component c)
        {
            if (c == null) return "?";
            try
            {
                var t = c.GetIl2CppType();
                if (t != null && !string.IsNullOrEmpty(t.Name)) return t.Name;
            }
            catch { }
            try { return c.GetType().Name; } catch { return "?"; }
        }

        /// <summary>节点摘要：组件真名 + 图片(sprite/alpha) + 文本 + 尺寸——认「新」标记与 `Tip`/`Focal` 全靠它</summary>
        private static void DescribeNode(GameObject go, System.Text.StringBuilder sb)
        {
            var comps = go.GetComponents<Component>();
            sb.Append('(');
            for (int i = 0; i < comps.Length; i++)
                if (comps[i] != null) sb.Append(TypeName(comps[i])).Append(' ');
            sb.Append(')');
            try
            {
                var img = go.GetComponent<Image>();
                if (img != null)
                    sb.Append("{img=").Append(img.sprite != null ? img.sprite.name : "(无sprite)")
                      .Append(" a=").Append(img.color.a.ToString("0.00"))
                      .Append(img.enabled ? " on" : " OFF").Append('}');
            }
            catch { }
            try
            {
                var txt = go.GetComponent<Text>();
                if (txt != null) sb.Append("{txt=\"").Append(Trim(txt.text)).Append("\"}");
                var tmp = go.GetComponent<TMPro.TextMeshProUGUI>();
                if (tmp != null) sb.Append("{tmp=\"").Append(Trim(tmp.text)).Append("\"}");
            }
            catch { }
            try
            {
                var rt = go.GetComponent<RectTransform>();
                if (rt != null)
                    sb.Append('{').Append(rt.rect.width.ToString("F0")).Append('x')
                      .Append(rt.rect.height.ToString("F0")).Append('}');
            }
            catch { }
        }

        private static string Trim(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Length > 12 ? s.Substring(0, 12) : s;
        }

        /// <summary>组件名串接（**索引循环**，IL2CPP 下不 foreach 互操作集合）</summary>
        private static void AppendComponentNames(GameObject go, System.Text.StringBuilder sb)
        {
            var comps = go.GetComponents<Component>();
            for (int i = 0; i < comps.Length; i++)
                if (comps[i] != null) sb.Append(TypeName(comps[i])).Append(' ');
        }

        /// <summary>子节点名单（名字 + 自活 + 组件/图片/文本/尺寸），深度 ≤2、总量 ≤1200 字防刷屏</summary>
        private static void CollectKids(Transform t, System.Text.StringBuilder sb, int depth)
        {
            if (t == null || depth > 2 || sb.Length > 1200) return;
            int n = t.childCount;
            for (int i = 0; i < n; i++)
            {
                var c = t.GetChild(i);
                if (c == null) continue;
                sb.Append(c.name).Append(c.gameObject.activeSelf ? "[on]" : "[OFF]");
                DescribeNode(c.gameObject, sb);
                sb.Append(' ');
                CollectKids(c, sb, depth + 1);
            }
        }

        /// <summary>
        /// 父链 ≤10 级内找 `UIOperationGroup`（**按运行时类型名**）。找不到时把沿途组件的真名一并
        /// 打出来——"父链上到底有什么"比"没有组"更有用。
        /// ⚠ 09-13 之前这里用 `c.GetType().Name`（恒为 "Component"），**所以 `父链组=(无)` 可能是假阴性**，
        /// 修好后才能下结论。
        /// </summary>
        private static string FindGroupName(Transform t)
        {
            var seen = new System.Text.StringBuilder();
            try
            {
                Transform cur = t;
                for (int i = 0; i < 10 && cur != null; i++)
                {
                    var comps = cur.GetComponents<Component>();
                    for (int j = 0; j < comps.Length; j++)
                    {
                        var c = comps[j];
                        if (c == null) continue;
                        string tn = TypeName(c);
                        if (tn == "UIOperationGroup") return cur.name + "(UIOperationGroup)";
                        if (seen.Length < 220) seen.Append(cur.name).Append(':').Append(tn).Append(' ');
                    }
                    cur = cur.parent;
                }
            }
            catch { }
            return "(无；沿途=" + (seen.Length == 0 ? "空" : seen.ToString()) + ")";
        }

        /// <summary>自 node 上溯 ≤maxUp 级找名为 name 的祖先（找不到返回 null）</summary>
        private static Transform ClimbToNamed(Transform node, string name, int maxUp)
        {
            try
            {
                Transform cur = node;
                for (int i = 0; i <= maxUp && cur != null; i++)
                {
                    if (cur.name == name) return cur;
                    try { cur = cur.parent; } catch { return null; }
                }
            }
            catch { }
            return null;
        }

        /// <summary>自 root 起逐级直接子级下行（每级只扫直接子级，无递归）。缺任一级返回 null。</summary>
        private static Transform DescendTo(Transform root, params string[] segments)
        {
            try
            {
                Transform cur = root;
                for (int i = 0; i < segments.Length && cur != null; i++)
                {
                    cur = DirectChildByName(cur, segments[i]);
                }
                return cur;
            }
            catch { return null; }
        }

        /// <summary>只扫“直接子级”的按名查找（无递归）</summary>
        private static Transform DirectChildByName(Transform parent, string name)
        {
            try
            {
                if (parent == null || string.IsNullOrEmpty(name)) return null;
                int n = parent.childCount;
                for (int i = 0; i < n; i++)
                {
                    Transform c = null;
                    try { c = parent.GetChild(i); } catch { continue; }
                    if (c != null && c.name == name) return c;
                }
            }
            catch { }
            return null;
        }

        /// <summary>销毁指定父下的直接子级（幂等清理）</summary>
        private static void DestroyDirectChild(Transform parent, string name)
        {
            try
            {
                Transform t = DirectChildByName(parent, name);
                if (t != null) UnityEngine.Object.Destroy(t.gameObject);
            }
            catch { }
        }
    }
}
