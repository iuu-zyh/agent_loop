/// <summary>
/// Mod AB 混合加载器 —— 官方路径优先，直载兜底，回注官方索引。
///
/// 背景（探针实证）：
///  - 游戏对「已启用 mod」的 ModRes/AssetBundle 有自动索引，但受 mod 缓存状态
///    （ModMgr.isModCache）摆布——开发期反复换 DLL 会令缓存失效，g.res.Load 全 null，
///    且失败会往 allRes 写 null 缓存（同 key 会话内永久命中 null，勿反复试错）；
///  - 我们的 AB 是未加密标准 bundle，AssetBundle.LoadFromFile 直载完好（探针实证）；
///  - g.mod.GetModPathRoot("六位ID") 是官方「mod 根目录」API（大鬼工具等同款用法，
///    工坊/本地两种安装形态都成立）；
///  - g.res.allRes 是 Dictionary&lt;string, UnityEngine.Object&gt;（key→已加载对象直查表）。
///
/// 策略：直载全部 bundle → 把三个 UI 预制体以官方 key（"UI/UIChatAi" 等）注入
/// g.res.allRes → 面板层既有代码（AbContactPanel 的 g.res.Load、AbChatPanel/AbConfigPanel
/// 的 g.ui.OpenUI）零改动直接命中。工坊场景若官方索引已就绪，本注入等价为幂等覆盖
/// （key 归本 mod 独占，无碰撞）；依赖 bundle 已全部就位，实例化引用可解析。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class ModAbRes
    {
        private const string ModId = "Jgmg5L";   // 六位随机模组 ID（MOD_Jgmg5L.dll 同源）
        private static readonly string[] UiPrefabs = { "UIChatAi", "UIConfigAi", "UIContactAi" };

        private static readonly Dictionary<string, AssetBundle> _bundles =
            new Dictionary<string, AssetBundle>();
        private static bool _tried;

        /// <summary>Init 时调用一次：直载 AB 并把 UI 预制体注入 g.res 官方索引（幂等）。</summary>
        public static void PreloadAll()
        {
            if (_tried) return;
            _tried = true;
            try
            {
                int loaded = LoadBundlesFromDisk();
                if (loaded == 0) return;
                ModMain.P("[ModAbRes] 直载 bundle 数=" + loaded);

                int ok = 0;
                for (int i = 0; i < UiPrefabs.Length; i++)
                {
                    // 三件全部走 g.ui.OpenUI（迁移：对话/配置/聊天/通讯录统一交游戏 UI 管理器）：
                    // 游戏 UIMgr.CreateUI 会给实例 AddComponent<Canvas>()，预制体自带的独立 Canvas 会撞
                    // "Can't add component ... already added" → OpenUI 内部 NRE（真机实证）——
                    // 注入前剥掉自带 Canvas/射线/缩放，层级与射线交给游戏体系。
                    if (Inject(UiPrefabs[i], stripCanvas: true)) ok++;
                }
                ModMain.P("[ModAbRes] 注入 g.res.allRes： " + ok + "/" + UiPrefabs.Length);
            }
            catch (Exception e) { ModMain.P("[ModAbRes] PreloadAll 异常: " + e); }
        }

        /// <summary>
        /// 从磁盘递归直载全部 bundle 进缓存，返回成功数。**可重复调用**（会先清空缓存）。
        ///
        /// 为什么必须能重载（真机实测，别再退回"只载一次"）：
        ///   游戏在**读档 / 切场景**时会卸载全部 AssetBundle（AssetBundle.UnloadAllAssetBundles），
        ///   我们 Init 时缓存进 _bundles 的引用随即**全部变成 fake-null**。
        ///   而 Inject() 的第一行是 `... || ab == null) return false;` —— **静默返回、不打日志**，
        ///   于是上层只看到「预制体注入失败，本轮不创建」，面板再也开不出来，且日志里没有原因。
        ///   实测对照（同一份 DLL md5、同一份 AB 指纹）：
        ///     00:40 会话重注入「成功」、00:52 会话重注入「失败×6」——
        ///     差别只在"游戏卸 bundle"与"我们重注入"的先后，是个**一直存在的竞态**，
        ///     不是打包形态引入的。玩家读一次档就会踩到，属发布级必修。
        /// </summary>
        private static int LoadBundlesFromDisk()
        {
            _bundles.Clear();
            try
            {
                string root = ResolveRoot();
                if (root == null) { ModMain.P("[ModAbRes] 找不到 mod 根目录，跳过 AB 预载"); return 0; }
                string abDir = Path.Combine(root, "ModRes", "AssetBundle");
                if (!Directory.Exists(abDir)) { ModMain.P("[ModAbRes] 无 AB 目录: " + abDir); return 0; }

                // 递归 LoadFromFile 全部 bundle（依赖 bundle 一并就位；跳过 .manifest）
                int loaded = 0;
                foreach (var file in Directory.GetFiles(abDir, "*.ab", SearchOption.AllDirectories))
                {
                    try
                    {
                        var ab = AssetBundle.LoadFromFile(file);
                        if (ab != null)
                        {
                            _bundles[Path.GetFileNameWithoutExtension(file).ToLower()] = ab;
                            loaded++;
                        }
                    }
                    catch (Exception e) { ModMain.P("[ModAbRes] LoadFromFile " + file + ": " + e.Message); }
                }
                return loaded;
            }
            catch (Exception e) { ModMain.P("[ModAbRes] LoadBundlesFromDisk 异常: " + e); return 0; }
        }

        /// <summary>解析 mod 根目录：官方 GetModPathRoot 优先，路径拼接兜底；PortraitCache 等共用。</summary>
        internal static string ResolveRoot()
        {
            try
            {
                string r = g.mod.GetModPathRoot(ModId);
                if (!string.IsNullOrEmpty(r) && Directory.Exists(r)) return r;
            }
            catch { }
            try
            {
                string r = Path.Combine(ModMgr.pathModExportData, "Mod_" + ModId);
                if (Directory.Exists(r)) return r;
            }
            catch { }
            return null;
        }

        /// <summary>把 UI/&lt;name&gt; 与裸 &lt;name&gt; 双 key 注入 g.res.allRes（覆盖 null 缓存/重复注入均安全）。
        /// 双 key 原因：AbChatPanel 走 g.ui.OpenUI(new UITypeBase(name)) 后，OpenUI 内部按
        /// UITypeBase 名取预制体的路径拼接规则未实证（可能带/不带 "UI/" 前缀）——两个 key
        /// 都就位，无论哪条拼接路径都命中（迁移兜底）。
        /// stripCanvas：剥掉预制体根的自带 Canvas/GraphicRaycaster/CanvasScaler——游戏
        /// UIMgr.CreateUI 会自己给实例挂 Canvas，预制体自带会撞 "already added" → NRE
        /// （真机实证 16:3x）；DestroyImmediate 直接改 bundle 内存资产，同 key 后续取用即已剥离。
        /// 三件面板均走 OpenUI，恒开启（配置/通讯录迁移后无独立 Canvas 架构）。</summary>
        /// <summary>从缓存取 bundle；查不到、或对象已被游戏销毁（Unity 的 fake-null）都返回 false。</summary>
        private static bool TryGetBundle(string uiName, out AssetBundle ab)
        {
            ab = null;
            if (!_bundles.TryGetValue(uiName.ToLower(), out var found)) return false;
            try
            {
                if (found == null) return false;   // fake-null：底层对象已不在了
                ab = found;
                return true;
            }
            catch { return false; }                // 已回收的对象连判等都可能抛
        }

        private static bool Inject(string uiName, bool stripCanvas = false)
        {
            try
            {
                // 缓存失效就地重载
                // 旧写法只有这一行、且**静默 return false**：游戏读档/切场景会卸载全部 bundle，
                // 缓存里那份变成 fake-null → 这里直接返回 false，日志里一个字都没有，
                // 上层只能看到「预制体注入失败」，原因彻底不可见（本次排查就卡在这）。
                // 现在：查不到/已死 → 从磁盘重载一次再试；仍然不行才报错返回。
                if (!TryGetBundle(uiName, out var ab))
                {
                    int n = LoadBundlesFromDisk();
                    ModMain.P("[ModAbRes] bundle 缓存已失效（游戏卸载过 AB），重新直载 " + n + " 个后重试 " + uiName);
                    if (!TryGetBundle(uiName, out ab))
                    {
                        ModMain.P("[ModAbRes] 重新直载后仍取不到 bundle: " + uiName + "（AB 目录里没有这个 .ab？）");
                        return false;
                    }
                }
                GameObject prefab = null;
                string[] names = ab.GetAllAssetNames();
                for (int i = 0; i < names.Length; i++)
                {
                    if (names[i].EndsWith(".prefab"))
                    {
                        prefab = ab.LoadAsset<GameObject>(names[i]);
                        break;
                    }
                }
                if (prefab == null)
                {
                    // 同样别再静默：bundle 在、但里面没有 .prefab，是 AB 打错了（Unity 侧打包时资源没进 Resources/）
                    ModMain.P("[ModAbRes] bundle " + uiName + " 里没有 .prefab 资产，无法注入（资产名："
                              + string.Join(", ", names) + "）");
                    return false;
                }
                if (stripCanvas)
                {
                    // interop 下 GetComponent 仅泛型版可用（System.Type 版报 CS1503）。
                    // 定案：这里剥的是【预制体资产】自带的 Canvas 三件套——正确且必要
                    //   （否则实例会带两套 Canvas；实测本调用是命中的）。
                    // ✗ 反面教训：不要对 OpenUI 返回的【实例】再剥——实例上的 Canvas/GraphicRaycaster
                    //   是游戏 UIMgr 自己挂的（用于层级排序），销毁它会打乱整个 UI 层布局、
                    //   连累同层正在创建的剧情窗（世界输入失效事故）。
                    var canvas = prefab.GetComponent<Canvas>();
                    var ray = prefab.GetComponent<GraphicRaycaster>();
                    var scaler = prefab.GetComponent<CanvasScaler>();
                    bool any = canvas != null || ray != null || scaler != null;
                    if (canvas != null) UnityEngine.Object.DestroyImmediate(canvas);
                    if (ray != null) UnityEngine.Object.DestroyImmediate(ray);
                    if (scaler != null) UnityEngine.Object.DestroyImmediate(scaler);
                    ModMain.P("[ModAbRes] " + uiName + " 预制体资产 Canvas 三件套剥离: " +
                              (any ? "命中" : "未命中（该资产本就没有）"));
                }
                g.res.allRes["UI/" + uiName] = prefab;
                g.res.allRes[uiName] = prefab;
                return true;
            }
            catch (Exception e) { ModMain.P("[ModAbRes] 注入 " + uiName + ": " + e.Message); return false; }
        }

        /// <summary>
        /// 确保 UI 预制体仍在游戏的资源表里（**按需调用**，OpenUI 之前必须做）。
        ///
        /// 背景（/实证）：游戏在读档/进世界流程中会重建 `g.res.allRes`，
        /// 我们 Init 时注入的条目会丢 → 之后 `g.ui.OpenUI(new UITypeBase(name))` 内部
        /// `Instantiate(null)` 直接抛 `ArgumentException: The Object you want to instantiate is null`，
        /// 异常从游戏 UIMgr 内部往外炸，会打断游戏当时的 UI 流程。
        /// 丢失则按原样（含 Canvas 三件套剥离）重新注入。
        /// </summary>
        public static bool EnsureInjected(string uiName)
        {
            try
            {
                // 双 key 任一非空即视为就绪（与 Inject 的双 key 写入一致）
                for (int i = 0; i < 2; i++)
                {
                    object cur = null;
                    try { cur = g.res.allRes[i == 0 ? uiName : "UI/" + uiName]; } catch { }
                    if (cur is GameObject gko && gko != null) return true;
                }
                bool ok = Inject(uiName, stripCanvas: true);
                ModMain.P("[ModAbRes] 资源表丢失，重新注入 " + uiName + ": " + (ok ? "成功" : "失败"));
                return ok;
            }
            catch (Exception e)
            {
                ModMain.P("[ModAbRes] EnsureInjected " + uiName + ": " + e.Message);
                return false;
            }
        }
    }
}
