/// <summary>
/// 对话窗位置/尺寸的**落盘层**（判据在 `WindowLayoutCore`，本类只做文件 IO 与缓存）。
///
/// 与 `NoticeStore` 同一套做法：首次访问读一次盘，之后全走内存；写盘先写 `.tmp` 再替换，
/// 失败只记一行日志、绝不影响开窗（**窗口位置记不住是小事，打不开窗是大事**）。
///
/// ── 文件位置与格式 ────────────────────────────────────────────────────────
/// `<数据根>\window_layout.json`（= `%USERPROFILE%\.agent_loop\window_layout.json`，与 `config.json` 同层）：
///     { "x": -120.0, "y": 0.0, "w": 1200.0, "h": 800.0 }
///
/// **故意不写进 `config.json`**：那个文件归 Python 的 `config_loader` 独占解释，
/// C# 侧 `ModConfigFile` 只读不写（它的文件头写着"只读，绝不写回"）。两边同时写一个
/// JSON 必然互相覆盖，且 Python 那边整份重写时会把我们塞的键丢掉。
///
/// ── 为什么 X/Y 用 float 而不是像素 ────────────────────────────────────────
/// 存的是画布单位（`BG.anchoredPosition`）。换分辨率后 CanvasScaler 的 scaleFactor 变了，
/// 但"窗口中心相对屏幕中心偏了多少画布单位"这个语义不变，所以**同一份数值在新分辨率下
/// 仍然落在合理位置**，应用时再按当前视口夹一次（`DragCore.ClampToViewport`）即可。
/// </summary>
using System;
using System.IO;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class WindowLayoutStore
    {
        private static bool _loaded;
        private static WindowLayout _cur = WindowLayout.None;

        /// <summary>落盘路径；数据根解析不到返回 null（调用方降级：不存不读，位置只在会话内有效）。</summary>
        internal static string FilePath()
        {
            try
            {
                string d = ModPaths.DataRoot;
                return string.IsNullOrEmpty(d) ? null : Path.Combine(d, WindowLayoutCore.FileName);
            }
            catch { return null; }
        }

        /// <summary>当前快照（首次调用读盘，之后全走内存）。</summary>
        internal static WindowLayout Current
        {
            get { EnsureLoaded(); return _cur; }
        }

        private static void EnsureLoaded()
        {
            if (_loaded) return;
            _loaded = true;                 // 先置位：加载途中绝不重入
            string path = FilePath();
            if (string.IsNullOrEmpty(path)) return;
            try
            {
                if (!File.Exists(path)) return;         // 首次安装：保持 None（= 出厂位置与尺寸）
                string txt = File.ReadAllText(path);
                if (string.IsNullOrWhiteSpace(txt)) return;
                var o = JObject.Parse(txt);

                float x = 0f, y = 0f, w = 0f, h = 0f;
                bool hasPos = TryFloat(o, "x", out x) & TryFloat(o, "y", out y);
                bool hasSize = TryFloat(o, "w", out w) & TryFloat(o, "h", out h);

                // ⚠ 不能写 `hasPos = hasPos && SanitizePos(..., out ox, out oy)`：
                //   短路求值时 out 变量可能根本没被赋值（CS0165）。分开写。
                float ox = 0f, oy = 0f;
                if (hasPos) hasPos = WindowLayoutCore.SanitizePos(x, y, out ox, out oy);

                // 尺寸只做"数值合法 + 不小于下限"的粗筛；**上限要等应用时拿到视口才知道**，
                // 所以这里不夹（见 `WindowLayoutCore.SanitizeSize` 的 maxW/maxH 参数）。
                float ow = WindowLayoutCore.DefaultW, oh = WindowLayoutCore.DefaultH;
                if (hasSize) hasSize = WindowLayoutCore.SanitizeSize(w, h, 0f, 0f, out ow, out oh);

                _cur = new WindowLayout
                {
                    X = ox, Y = oy, W = ow, H = oh,
                    HasPos = hasPos, HasSize = hasSize,
                };
                ModMain.P("[WindowLayout] 已读取：" + (hasPos ? ("位置 " + ox.ToString("F0") + "," + oy.ToString("F0")) : "位置=无")
                          + " " + (hasSize ? ("尺寸 " + ow.ToString("F0") + "x" + oh.ToString("F0")) : "尺寸=无")
                          + "（" + path + "）");
            }
            catch (Exception e)
            {
                // 文件坏了：当没存过（回出厂位置）。比"静默用垃圾数把窗摆到屏幕外"安全。
                ModMain.P("[WindowLayout] 读取失败，按出厂版式处理：" + e.Message);
                _cur = WindowLayout.None;
            }
        }

        private static bool TryFloat(JObject o, string key, out float v)
        {
            v = 0f;
            try
            {
                JToken t = o[key];
                if (t == null || t.Type == JTokenType.Null) return false;
                v = t.Value<float>();
                return WindowLayoutCore.Finite(v);
            }
            catch { return false; }
        }

        /// <summary>
        /// 记下当前位置/尺寸并落盘。**只在真变化时写**（见 `WindowLayoutCore.PosChanged`）。
        /// `hasSize=false` 时不写尺寸（A 阶段只有位置会变；尺寸留给 B 阶段）。
        /// 返回是否真的写了盘（诊断用）。
        /// </summary>
        internal static bool Save(float x, float y, float w, float h, bool hasSize)
        {
            // 整体兜住：本层任何异常都不许冒到调用方（调用方是每帧的 UI 主流程）
            try
            {
                EnsureLoaded();
                bool posDiff = !_cur.HasPos || WindowLayoutCore.PosChanged(_cur.X, _cur.Y, x, y);
                bool sizeDiff = hasSize && (!_cur.HasSize
                                            || WindowLayoutCore.SizeChanged(_cur.W, _cur.H, w, h));
                if (!posDiff && !sizeDiff) return false;      // 没动过：一个字节都不写

                var next = new WindowLayout
                {
                    X = x, Y = y,
                    W = hasSize ? w : _cur.W,
                    H = hasSize ? h : _cur.H,
                    HasPos = true,
                    HasSize = hasSize || _cur.HasSize,
                };
                if (!Write(next)) return false;               // 盘没写成：内存**不动**（下次再试）
                _cur = next;
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[WindowLayout] Save 异常（位置本次仍生效，下次进游戏回出厂版式）：" + e.Message);
                return false;
            }
        }

        private static bool Write(WindowLayout l)
        {
            string path = FilePath();
            if (string.IsNullOrEmpty(path))
            {
                ModMain.P("[WindowLayout] 未落盘：数据根解析不到（位置只在本次游戏内有效）");
                return false;
            }
            try
            {
                var o = new JObject
                {
                    ["x"] = Math.Round(l.X, 1),
                    ["y"] = Math.Round(l.Y, 1),
                };
                if (l.HasSize)
                {
                    o["w"] = Math.Round(l.W, 1);
                    o["h"] = Math.Round(l.H, 1);
                }
                string tmp = path + ".tmp";
                File.WriteAllText(tmp, o.ToString(Newtonsoft.Json.Formatting.Indented));
                if (File.Exists(path))
                {
                    try { File.Replace(tmp, path, null); }
                    catch { File.Delete(path); File.Move(tmp, path); }
                }
                else File.Move(tmp, path);
                ModMain.P("[WindowLayout] 已落盘：位置 " + l.X.ToString("F0") + "," + l.Y.ToString("F0")
                          + (l.HasSize ? (" 尺寸 " + l.W.ToString("F0") + "x" + l.H.ToString("F0")) : ""));
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[WindowLayout] 落盘失败（本次仍生效，下次进游戏回出厂版式）：" + e.Message);
                return false;
            }
        }

        /// <summary>诊断/测试用：清掉内存缓存，强制下次重新读盘。</summary>
        internal static void Invalidate()
        {
            _loaded = false;
            _cur = WindowLayout.None;
        }
    }
}
