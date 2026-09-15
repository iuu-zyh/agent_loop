/// <summary>
/// 立绘像素缓存 —— 对话 UI 渲染成功后的快照，供通讯录等「单位未必在场」的 UI 复用。
///
/// 背景：PortraitModel.CreateTextureInModelData 要求 unit 在场（modelData 就绪），通讯录名单里
/// 大量 NPC 未进场时调用直接异常（事故根因，见 workbuddy memory）。本层把对话窗「必然
/// 成功」的渲染产物做成像素快照落盘，通讯录只读像素、零渲染 API 调用；无缓存保持占位圆。
///
/// 语义（最后已知立绘）：每次对话开窗渲染成功 → 延迟一帧（等模型渲完当帧）→ RT 读像素 →
/// 降采样 256² + 圆形烘焙（抗锯齿边）→ PNG 落盘 + 内存 LRU。换装/剧情换立绘在下次开对话时
/// 自然覆盖刷新；捕获寄生在本来就要做的渲染上，零额外渲染开销。
///
/// 资源账：PNG 50~150KB/张（盘）+ 256² RGBA32 ≈256KB/张（内存），LRU 上限 64 张 ≈16MB，
/// 淘汰只丢内存纹理（盘上 PNG 仍在，再用时懒读回）。
///
/// 用法：AbChatPanel.FillPortraits 成功后 RequestCapture(IdOf(unit), slot)；ChatWindow.Update 泵
/// PumpCapture()（帧延迟与槽位存活都在这层处理）；ContactPresenter.Update 泵 PumpLoad() +
/// TryGetSprite 命中即赋 overrideSprite，未命中 RequestLoad 排队。
/// 硬约束：仅 Unity 主线程调用（触 RenderTexture/文件 IO）。读像素不翻转 UV——RT 与 Texture2D
/// 同为 UV 原点左下，方向天然一致（冒烟如见倒立再补翻转）。
///
/// 全项目铁律（实证）
/// **不得对 Il2Cpp 方法/属性返回值做向下转型（`is 子类` / `as 子类` / `(子类)`）。**
/// 本机 Unhollower（MelonLoader 0.5.x；UnhollowerBaseLib 里**没有** Il2CppObjectPool）生成的
/// getter 一律 `newobj 声明类型(ptr)`，不做运行时类型解析 —— 返回值 CLR 类型恒为**声明类型**：
///   · `is 子类` 恒 false（静默走错分支）；
///   · `as 子类` 恒 null（静默）；
///   · `(子类)x` 抛 InvalidCastException「Specified cast is not valid.」。
/// 通解：只调**声明类型上就有**的成员；需要跨类型能力时改用接口式 API（如 Graphics.Blit(Texture,…)）、
/// 或先用 `IL2CPP.il2cpp_object_get_class(x.Pointer)` 拿真类再决定。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;   // Marshal.PtrToStringAnsi —— 读 il2cpp 的 const char*
using UnhollowerBaseLib;                // IL2CPP.il2cpp_object_get_class —— 诊断用「真实类名」
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class PortraitCache
    {
        private const int CaptureSize = 256;      // 快照边长（头像显示 40~80px，3 倍超采样余量）
        private const int MemCap = 64;            // 内存 LRU 上限（≈16MB）
        private const float CaptureDelay = 0.15f; // Fill 后延迟（等模型渲完当帧）
        private const int CaptureTries = 3;       // 槽位未就绪重试（每 0.2s 一次，3 次放弃）

        private class Entry
        {
            public Texture2D Tex;
            public Sprite Sprite;      // 懒创建（通讯录 Image.overrideSprite 用），随条目一起淘汰
            public float LastUse;
        }

        private class CaptureJob
        {
            public string Id;
            public RawImage Slot;
            public float Due;
            public int Tries;
        }

        private static readonly Dictionary<string, Entry> _mem = new Dictionary<string, Entry>();
        private static readonly List<CaptureJob> _captures = new List<CaptureJob>();
        private static readonly List<string> _loadQueue = new List<string>();
        private static string _dir;

        /// <summary>单位的缓存键（账本 unitID，字符串）；读取失败返回 null（调用方保持占位）。</summary>
        public static string IdOf(WorldUnitBase u)
        {
            try { return u.data.unitData.unitID; } catch { return null; }
        }

        // ------------------------------------------------------------------
        // 捕获（对话 UI 侧）
        // ------------------------------------------------------------------

        /// <summary>开窗渲染成功后调用：延迟 CaptureDelay 秒抓槽位 RT 快照落盘。幂等——同 id 重复
        /// 请求会重置到期时间（换 NPC 快速切换时旧任务自然作废）。</summary>
        public static void RequestCapture(string id, RawImage slot)
        {
            if (string.IsNullOrEmpty(id) || slot == null) return;
            try { ModMain.P("[PortraitCache] 排队快照 id=" + id); } catch { }
            for (int i = 0; i < _captures.Count; i++)
            {
                if (_captures[i].Id != id) continue;
                _captures[i].Slot = slot;
                _captures[i].Due = Time.unscaledTime + CaptureDelay;
                return;
            }
            _captures.Add(new CaptureJob { Id = id, Slot = slot, Due = Time.unscaledTime + CaptureDelay });
        }

        /// <summary>每帧泵（ChatWindow.Update 调）：处理到期捕获任务。槽位已销毁/多次未就绪即放弃（带原因留痕）。</summary>
        public static void PumpCapture()
        {
            for (int i = _captures.Count - 1; i >= 0; i--)
            {
                var j = _captures[i];
                if (Time.unscaledTime < j.Due) continue;
                bool done;
                string why = null;
                try
                {
                    if (j.Slot == null) { why = "槽位已销毁"; done = true; }
                    else
                    {
                        done = SafeCapture(j.Id, j.Slot, out why);
                    }
                }
                catch (Exception e)
                {
                    try { ModMain.P("[PortraitCache] capture " + j.Id + ": " + e.Message); } catch { }
                    done = true;   // 异常不重试（重试大概率同因）
                }
                if (!done && ++j.Tries < CaptureTries)
                {
                    j.Due = Time.unscaledTime + 0.2f;   // 未就绪（如 RT 尚未渲出）→ 稍后重试
                    continue;
                }
                if (!done)
                    try { ModMain.P("[PortraitCache] 放弃 " + j.Id + "（" + (why ?? "多次未就绪") + "）"); } catch { }
                _captures.RemoveAt(i);
            }
        }

        private static bool SafeCapture(string id, RawImage slot, out string why)
        {
            why = null;
            if (DiagSwitches.NoPortraits) { why = "noPortraits 开关"; return true; }

            // 铁律：slot.texture 的 CLR 类型**恒为声明基类 UnityEngine.Texture**，
            //   不得 `is RenderTexture` / `(Texture2D)` 向下转型（必抛 InvalidCastException）。
            //   实证与机理见 BlitToSmall 注释；native 指针是真的，Graphics.Blit 只吃声明类型 Texture。
            var tex = slot.texture;
            if (tex == null) { why = "槽位无纹理"; return false; }
            if (tex.width <= 0 || tex.height <= 0) { why = "纹理尺寸退化 " + tex.width + "x" + tex.height; return false; }

            Texture2D shot;
            try { shot = BlitToSmall(tex); }
            catch (Exception e) { why = "取像素异常 " + e.Message; return false; }
            if (shot == null) { why = "取像素失败"; return false; }
            CircleBake(shot);

            byte[] png = ImageConversion.EncodeToPNG(shot);   // interop 静态方法（非扩展）
            string file = Path.Combine(Dir(), SafeFileId(id) + ".png");
            File.WriteAllBytes(file, png);
            PutMem(id, shot);
            try
            {
                ModMain.P("[PortraitCache] 已缓存 " + id + " ← " + RealClassName(tex) + " " +
                          tex.width + "x" + tex.height + " → " + file + "（" + (png.Length / 1024) + "KB）");
            }
            catch { }
            return true;
        }

        /// <summary>native 真实 Il2Cpp 类名（诊断用）——代理的 CLR 类型是声明基类，只能这样取真名。
        ///
        /// 必须 Marshal.PtrToStringAnsi，**绝不能** IL2CPP.Il2CppStringToManaged
        /// `il2cpp_class_get_name` 返回的是**裸 `const char*`**（Unity IL2CPP 官方签名
        /// `const char* il2cpp_class_get_name(Il2CppClass*)`），而 `Il2CppStringToManaged` 的形参要的是
        /// **`Il2CppString*`（托管字符串对象）**。后者 IL 实证：
        ///     Il2CppStringToManaged(IntPtr):
        ///         call int32 IL2CPP::il2cpp_string_length(ptr)   ← 按「托管字符串对象」读 length 字段
        ///         call char* IL2CPP::il2cpp_string_chars(ptr)
        ///         newobj string::.ctor(char*, 0, length)
        /// 把 `char*` 喂进去 → 读到的「长度」其实是 C 串前 4 字节（"Rend" = 0x646E6552 ≈ 16.8 亿）
        /// → `new string(chars, 0, 16.8亿)` 越界读几个 GB → **访问违例硬崩，托管 catch 抓不到**
        /// （09-13 实机崩溃根因：日志停在 `OpenForUnit: 完成` 之后、`已缓存` 之前；
        ///   而 `MTpQXJ.png` 已成功落盘 62KB/256²，反证 GPU 取像素段完全无恙）。
        /// PtrToStringAnsi 逐字节读到 NUL 为止，无越界可能。</summary>
        private static string RealClassName(Texture tex)
        {
            try
            {
                var cls = IL2CPP.il2cpp_object_get_class(tex.Pointer);
                if (cls == IntPtr.Zero) return "?";
                var name = Marshal.PtrToStringAnsi(IL2CPP.il2cpp_class_get_name(cls));
                return string.IsNullOrEmpty(name) ? "?" : name;
            }
            catch { return "?"; }
        }

        /// <summary>任意 Texture → CaptureSize² 可读纹理（源居中方裁后交 GPU 双线性缩小）。
        ///
        /// 为什么不能按类型分派（`is RenderTexture` / `(Texture2D)`）
        /// 本机 Unhollower（MelonLoader 0.5.x；UnhollowerBaseLib **不存在** Il2CppObjectPool 类型）
        /// 生成的属性 getter 一律「new 声明类型(ptr)」，**不做运行时类型解析**。Mono.Cecil 读
        /// UnityEngine.UI.dll 的 RawImage::get_texture 原始 IL：
        ///     call  IL2CPP::il2cpp_runtime_invoke(...)
        ///     ldloc V_2 ; dup ; brtrue.s IL_0043
        ///     pop ; ldnull ; br IL_0048
        ///  IL_0043: newobj System.Void UnityEngine.Texture::.ctor(System.IntPtr)←
        /// 于是 `slot.texture` 的 CLR 类型**恒为 UnityEngine.Texture**：
        ///   · `tex is RenderTexture` 永远 false —— 哪怕 native 确实是 RenderTexture；
        ///   · `(Texture2D)tex` 直接 InvalidCastException「Specified cast is not valid.」。
        /// 这正是 09-13「通讯录头像恒占位、portrait_cache 一张 PNG 都没有」的根因
        /// （日志实证 `[PortraitCache] capture MTpQXJ: Specified cast is not valid.`）。
        /// Graphics.Blit 的形参就是 Texture，native 指针原样透传，与代理确切类型无关——
        /// 一条路径同时覆盖 RT / Texture2D / 其它 Texture 子类。
        ///
        /// 方向说明：ReadPixels 取的是我们自己这张临时 RT，行 0 = 底边，与源同为 UV 原点左下，
        /// 贴回 RawImage/Image 天然正立（若实机见倒立，只需在 Blit 后补一次垂直翻转）。</summary>
        private static Texture2D BlitToSmall(Texture src)
        {
            RenderTexture tmp = null;
            var prev = RenderTexture.active;
            try
            {
                tmp = RenderTexture.GetTemporary(CaptureSize, CaptureSize, 0, RenderTextureFormat.ARGB32);
                // 居中正方裁切：源本就是正方形时 scale=(1,1)、offset=(0,0)，与直通 Blit 完全等价
                float s = Mathf.Min(src.width, src.height);
                var scale = new Vector2(s / src.width, s / src.height);
                var offset = new Vector2((1f - scale.x) * 0.5f, (1f - scale.y) * 0.5f);
                Graphics.Blit(src, tmp, scale, offset);
                RenderTexture.active = tmp;
                var shot = new Texture2D(CaptureSize, CaptureSize, TextureFormat.RGBA32, false);
                shot.ReadPixels(new Rect(0, 0, CaptureSize, CaptureSize), 0, 0);
                shot.Apply(false, false);
                return shot;
            }
            finally
            {
                RenderTexture.active = prev;
                if (tmp != null) RenderTexture.ReleaseTemporary(tmp);
            }
        }

        /// <summary>原地圆形烘焙：外接圆外 alpha=0、圆边 1px 抗锯齿（不动 RGB，只切 alpha）。
        /// 缩放在 GPU（Blit）已完成，这里只做 alpha 遮罩，CPU 只过一遍 256²。</summary>
        private static void CircleBake(Texture2D t)
        {
            int size = t.width;
            Color32[] px = t.GetPixels32();   // 隐式转换 Il2CppArrayBase<T>.op_Implicit → T[]（IL 实证为逐元素复制）
            float r = size * 0.5f;
            for (int y = 0; y < size; y++)
            {
                float dy = y + 0.5f - r;
                int row = y * size;
                for (int x = 0; x < size; x++)
                {
                    float dx = x + 0.5f - r;
                    float edge = Mathf.Clamp01(r - Mathf.Sqrt(dx * dx + dy * dy) + 0.5f);   // 圆外 ≤0，圆心 ≥1，边 1px 过渡
                    if (edge >= 1f) continue;
                    var c = px[row + x];
                    c.a = (byte)(c.a * edge);
                    px[row + x] = c;
                }
            }
            t.SetPixels32(px);   // 隐式转换 T[] → Il2CppStructArray<T>
            t.Apply(false, false);
        }

        // ------------------------------------------------------------------
        // 消费（通讯录侧）
        // ------------------------------------------------------------------

        /// <summary>内存命中（不触发读盘）。命中即刷新 LRU。</summary>
        public static bool TryGet(string id, out Texture2D tex)
        {
            tex = null;
            if (string.IsNullOrEmpty(id)) return false;
            if (_mem.TryGetValue(id, out var e))
            {
                e.LastUse = Time.unscaledTime;
                if (e.Tex != null) { tex = e.Tex; return true; }
            }
            return false;
        }

        /// <summary>取可直接赋给 Image.overrideSprite 的圆形精灵（懒创建，随条目淘汰）。</summary>
        public static bool TryGetSprite(string id, out Sprite sprite)
        {
            sprite = null;
            if (!TryGet(id, out var tex)) return false;
            var e = _mem[id];
            if (e.Sprite == null)
                e.Sprite = Sprite.Create(tex, new Rect(0, 0, tex.width, tex.height),
                                         new Vector2(0.5f, 0.5f), 100f);
            sprite = e.Sprite;
            return true;
        }

        /// <summary>读盘排队（去重；磁盘无此 id 时静默移除——调用方保持占位即可）。</summary>
        public static void RequestLoad(string id)
        {
            if (string.IsNullOrEmpty(id)) return;
            if (_mem.ContainsKey(id) || _loadQueue.Contains(id)) return;
            _loadQueue.Add(id);
        }

        /// <summary>每帧泵（ContactPresenter.Update 调）：限流读盘（每帧 budget 张，LoadImage 1~3ms/张）。</summary>
        public static void PumpLoad(int budget = 2)
        {
            while (budget-- > 0 && _loadQueue.Count > 0)
            {
                string id = _loadQueue[0];
                _loadQueue.RemoveAt(0);
                if (_mem.ContainsKey(id)) continue;
                try
                {
                    string file = Path.Combine(Dir(), SafeFileId(id) + ".png");
                    if (!File.Exists(file)) continue;
                    var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                    if (ImageConversion.LoadImage(tex, File.ReadAllBytes(file))) PutMem(id, tex);   // interop 静态方法
                    else UnityEngine.Object.Destroy(tex);
                }
                catch (Exception e)
                {
                    try { ModMain.P("[PortraitCache] load " + id + ": " + e.Message); } catch { }
                }
            }
        }

        // ------------------------------------------------------------------
        // 内部
        // ------------------------------------------------------------------

        private static void PutMem(string id, Texture2D tex)
        {
            if (_mem.TryGetValue(id, out var e))
            {
                if (e.Tex != null) UnityEngine.Object.Destroy(e.Tex);
                if (e.Sprite != null) UnityEngine.Object.Destroy(e.Sprite);
                e.Tex = tex;
                e.Sprite = null;
                e.LastUse = Time.unscaledTime;
            }
            else
            {
                _mem[id] = new Entry { Tex = tex, LastUse = Time.unscaledTime };
            }
            Evict();
        }

        private static void Evict()
        {
            if (_mem.Count <= MemCap) return;
            string oldest = null;
            float t = float.MaxValue;
            foreach (var kv in _mem)
            {
                if (kv.Value.LastUse < t) { t = kv.Value.LastUse; oldest = kv.Key; }
            }
            if (oldest == null) return;
            var e = _mem[oldest];
            if (e.Tex != null) UnityEngine.Object.Destroy(e.Tex);
            if (e.Sprite != null) UnityEngine.Object.Destroy(e.Sprite);
            _mem.Remove(oldest);
        }

        private static string SafeFileId(string id)
        {
            var arr = id.ToCharArray();
            for (int i = 0; i < arr.Length; i++)
            {
                char c = arr[i];
                bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_';
                if (!ok) arr[i] = '_';
            }
            return new string(arr);
        }

        private static string Dir()
        {
            if (_dir != null) return _dir;
            try
            {
                string root = ModAbRes.ResolveRoot();
                if (!string.IsNullOrEmpty(root)) _dir = Path.Combine(root, "portrait_cache");
            }
            catch { }
            if (string.IsNullOrEmpty(_dir))
                _dir = Path.Combine(Application.persistentDataPath, "portrait_cache");   // 兜底：游戏可写目录
            try { Directory.CreateDirectory(_dir); } catch { }
            return _dir;
        }
    }
}
