/// <summary>
/// 图片输入—— 「文本里的图片路径」→ 「随帧发走的图像附件」。
///
/// 定位：本文件是**唯一**做「取图字节」的地方，其余全是纯函数式解析。
/// 设计取舍：**走文件路径**，把三种剪贴板来源统一归一到「一个路径」。
///   ① `CF_HDROP`（资源管理器复制的文件）—— 路径本来就有，原样用。
///   ② 注册格式 `"PNG"`（Win11 截图工具）—— 剪贴板里就是一条完整 PNG 流，直接落盘。
///   ③ `CF_DIB`（老式工具的裸位图）—— **自己解像素、重新编码成 PNG** 再落盘。
///      ②③ 落 `%TEMP%\agent_loop_clip\` 后返回路径，于是下游只有「路径」一条代码路径。
///   剪贴板位图**不能落成 .bmp**：`ImageConversion.LoadImage` 只支持 PNG/JPG ——
///     文件合法也白搭，实机表现为预览条变成「带问号的占位图」。详见 `DibToPng` 注释。
///
/// 硬约束：
///   · **仅 Unity 主线程调用**（触 Texture2D / ImageConversion）。
///   · **绝不按类型分派 Texture**（`is RenderTexture`/`(Texture2D)` 必失败）——
///     见 PortraitCache.BlitToSmall 的长注释；本文件改用纯 CPU 行拷贝，连 Blit 都不用，
///     顺带彻底消除「Blit 方向翻转」这个只能靠肉眼看出来的隐患：
///     发给模型的图**用户在 UI 上看不到**，一旦上下颠倒就是静默错误，所以这里不做任何
///     依赖 GPU 采样约定的操作。GetPixels32/SetPixels32 的行序都是「行 0 = 底边」，
///     逐行原序搬运，**不可能翻转**。
///   · 大图必须降采样：Python 侧 websockets 默认 max_size=1MiB，超限会**静默断连**。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class ImageInput
    {
        /// <summary>降采样长边上限（像素）。1024 对「看清 UI 文字/人物」够用，体积可控。</summary>
        public const int MaxSide = 1024;
        /// <summary>单张编码后字节上限；超了先降 PNG→JPG(q85)。</summary>
        public const int MaxBytes = 1500 * 1024;
        /// <summary>单条消息最多几张（防一次糊 10 张把帧撑爆）。</summary>
        public const int MaxCount = 3;

        private static readonly string[] Exts = { ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp" };

        // ------------------------------------------------------------------
        // 入口 ①：剪贴板里的图（三种格式，统一归一成「一个文件路径」）
        // ------------------------------------------------------------------

        private const uint CF_HDROP = 15;
        private const uint CF_DIB = 8;

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool OpenClipboard(IntPtr hWndNewOwner);
        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool CloseClipboard();
        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr GetClipboardData(uint uFormat);
        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool IsClipboardFormatAvailable(uint format);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        private static extern uint RegisterClipboardFormatW(string lpszFormat);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr GlobalLock(IntPtr hMem);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GlobalUnlock(IntPtr hMem);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern UIntPtr GlobalSize(IntPtr hMem);
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern uint DragQueryFileW(IntPtr hDrop, uint iFile, StringBuilder lpszFile, uint cch);

        /// <summary>
        /// 剪贴板里的图 → **文件路径**（没有则空表）。三种来源按优先级：
        ///   ① `CF_HDROP` —— 资源管理器里复制的图片文件，原路径直接用；
        ///   ② 注册格式 `"PNG"` —— Win11 截图工具 / 现代截图软件：剪贴板里就是一条**完整 PNG 流**
        ///      （实测：940041 字节，签名 `89 50 4E 47…` 合法）；
        ///   ③ `CF_DIB` —— 老式工具的裸位图：自己解像素 → 重新编码成 PNG 落盘（不能产 .bmp）。
        ///
        /// ②③ 落盘到 `%TEMP%\agent_loop_clip\` 后返回路径 —— 于是下游（文本识别 / 预览 /
        /// 编码 / 发送）**只有「路径」这一条代码路径**，剪贴板来源对它完全透明。
        ///
        /// 实机教训 原实现只读 ①，而 `Win+Shift+S` 截图**只写 Bitmap/PNG、不写 CF_HDROP**
        /// （`HasFileDrop=False / HasImage=True`）→ 用户粘贴后**什么都没发生且无任何日志**。
        /// 故本函数现在**无论成败都留痕**（`how` 出参给调用方打日志），绝不静默。
        /// </summary>
        public static List<string> ClipboardImages(out string how)
        {
            var found = new List<string>();
            how = "未打开剪贴板";
            if (!OpenWithRetry()) return found;
            try
            {
                // ① 复制的文件
                if (IsClipboardFormatAvailable(CF_HDROP))
                {
                    var files = ReadHdrop();
                    for (int i = 0; i < files.Count; i++)
                        if (IsImagePath(files[i]) && File.Exists(files[i])) found.Add(files[i]);
                    if (found.Count > 0) { how = "CF_HDROP 文件×" + found.Count; return found; }
                }
                // ② 注册格式 "PNG"（首选：零解析，字节即成品）
                uint fmtPng = 0;
                try { fmtPng = RegisterClipboardFormatW("PNG"); } catch { }
                if (fmtPng != 0 && IsClipboardFormatAvailable(fmtPng))
                {
                    byte[] raw = ReadHglobal(fmtPng);
                    int n = TrimPng(raw);
                    if (n > 8 && raw[0] == 0x89 && raw[1] == 0x50 && raw[2] == 0x4E && raw[3] == 0x47)
                    {
                        string p = SaveTemp(raw, n, ".png");
                        if (p != null) { found.Add(p); how = "剪贴板 PNG 流 " + (n / 1024) + "KB"; return found; }
                        how = "PNG 流落盘失败";
                    }
                    else how = "PNG 格式字节非法（" + (raw == null ? 0 : raw.Length) + "B）";
                }
                else if (how == "未打开剪贴板") how = "无 CF_HDROP / 无 PNG / ";
                // ③ CF_DIB → 自己解像素 → PNG 落盘（**不产出 BMP**：LoadImage 只认 PNG/JPG）
                if (IsClipboardFormatAvailable(CF_DIB))
                {
                    byte[] dib = ReadHglobal(CF_DIB);
                    int pw, ph;
                    byte[] png = DibToPng(dib, MaxSide, out pw, out ph);
                    if (png != null)
                    {
                        string p = SaveTemp(png, png.Length, ".png");
                        if (p != null)
                        {
                            found.Add(p);
                            how = "剪贴板 DIB→PNG " + pw + "x" + ph + " " + (png.Length / 1024) + "KB";
                            return found;
                        }
                        how += "DIB 落盘失败";
                    }
                    else how += "DIB 解析失败";
                }
                else how += "无 CF_DIB";
                return found;
            }
            catch (Exception e)
            {
                how = "异常 " + e.Message;
                try { ModMain.P("[ImageInput] 读剪贴板: " + e.Message); } catch { }
                return found;
            }
            finally { try { CloseClipboard(); } catch { } }
        }

        private static bool OpenWithRetry()
        {
            // 剪贴板同一时刻只能被一个进程打开，偶发占用就重试（用户手速远慢于重试间隔）
            for (int i = 0; i < 5; i++) { try { if (OpenClipboard(IntPtr.Zero)) return true; } catch { } }
            return false;
        }

        private static List<string> ReadHdrop()
        {
            var list = new List<string>();
            var h = GetClipboardData(CF_HDROP);
            if (h == IntPtr.Zero) return list;
            uint n = DragQueryFileW(h, 0xFFFFFFFFu, null, 0);   // 0xFFFFFFFF = 问数量
            for (uint i = 0; i < n; i++)
            {
                uint len = DragQueryFileW(h, i, null, 0);
                if (len == 0 || len > 4096) continue;
                var sb = new StringBuilder((int)len + 2);
                if (DragQueryFileW(h, i, sb, (uint)sb.Capacity) == 0) continue;
                list.Add(sb.ToString());
            }
            return list;
        }

        /// <summary>取 HGLOBAL 剪贴板数据的全部字节（GlobalSize 可能含尾部填充，调用方自行裁剪）。</summary>
        private static byte[] ReadHglobal(uint fmt)
        {
            var h = GetClipboardData(fmt);
            if (h == IntPtr.Zero) return null;
            var p = GlobalLock(h);
            if (p == IntPtr.Zero) return null;
            try
            {
                ulong sz = 0;
                try { sz = GlobalSize(h).ToUInt64(); } catch { }
                if (sz == 0 || sz > 64UL * 1024 * 1024) return null;   // 上限 64MB，防异常巨块
                var buf = new byte[(int)sz];
                Marshal.Copy(p, buf, 0, (int)sz);
                return buf;
            }
            finally { try { GlobalUnlock(h); } catch { } }
        }

        /// <summary>把 PNG 裁到 IEND 数据块结束（去掉 HGLOBAL 的尾部填充，防解码器挑食）。返回有效长度。</summary>
        private static int TrimPng(byte[] b)
        {
            if (b == null) return 0;
            // IEND = 4 长度 + 4 类型 + 4 CRC；从尾部往前找最后一个 "IEND"
            for (int i = b.Length - 8; i >= 8; i--)
            {
                if (b[i] == (byte)'I' && b[i + 1] == (byte)'E' && b[i + 2] == (byte)'N' && b[i + 3] == (byte)'D')
                    return Math.Min(b.Length, i + 8);
            }
            return b.Length;
        }

        /// <summary>
        /// 把「Unity 解不了的图片字节」按**内容**归一成 PNG。目前只处理 BMP。
        ///
        /// 动机与 `DibToPng` 同源：`ImageConversion.LoadImage` 只支持 PNG/JPG，磁盘上的
        /// `.bmp` 直接喂进去必然失败 —— 而 `.bmp` 又在可识别后缀白名单里，于是会出现
        /// 「路径被认出来了、预览和发送却都读不出图」的半死状态。这里统一抹平。
        /// 按魔术字判定而不看扩展名：用户完全可能把 bmp 存成别的名字。
        /// 转换失败就原样返回，让后续的 LoadImage 去失败并留痕（不在这里吞掉错误）。
        /// </summary>
        private static byte[] NormalizeToPng(byte[] raw)
        {
            if (raw == null || raw.Length < 54) return raw;
            if (raw[0] != (byte)'B' || raw[1] != (byte)'M') return raw;
            int bfOff = BitConverter.ToInt32(raw, 10);
            if (bfOff < 14 || bfOff > raw.Length) return raw;
            var dib = new byte[raw.Length - 14];
            Buffer.BlockCopy(raw, 14, dib, 0, dib.Length);
            int pw, ph;
            byte[] png = DibToPng(dib, MaxSide, out pw, out ph);
            if (png == null) return raw;
            try { ModMain.P("[ImageInput] BMP 字节归一为 PNG " + pw + "x" + ph); } catch { }
            return png;
        }

        /// <summary>
        /// 裸 DIB → **PNG 字节**（自己把像素解成 Texture2D，再走已验证的 `EncodeToPNG`）。
        ///
        /// 为什么不再产出 BMP（实机教训）
        /// 原做法是「补一个 14 字节 `BITMAPFILEHEADER` 当 .bmp 落盘，交给解码器读」。那个 .bmp
        /// **文件本身完全合法**（离线用 PIL 验证、与源图逐像素一致），但
        /// **`ImageConversion.LoadImage` 只支持 PNG/JPG**，喂 BMP 解不出来 —— 实机表现就是
        /// 预览条渲染成一块「带问号的占位图」（Unity 解码失败的兜底图形）。
        /// 教训：**「我能生成合法文件」≠「目标解码器认这个格式」**；生成端自测通过之后，
        /// 仍必须单独核对**消费端**的格式支持面。
        ///
        /// 现在：自己按 DIB 头解像素 → `Texture2D` → `EncodeToPNG`（该 API 在本工程已由
        /// PortraitCache 实证可用）→ 落盘成 **.png**。于是下游（预览 `LoadThumb`、发送
        /// `Encode`）拿到的永远是 PNG，与剪贴板来源彻底解耦。
        ///
        /// 解析范围：32/24bpp（覆盖全部截图场景）+ 16bpp（按位域掩码归一）；1/4/8bpp 调色板图
        /// 不支持（截图不产生，遇到返回 null 并留痕）。按需**直接降采样解码**（`step`）：
        /// 省内存也省编码时间，产出的临时件本身就 ≤ maxSide。
        /// </summary>
        private static byte[] DibToPng(byte[] dib, int maxSide, out int outW, out int outH)
        {
            outW = 0; outH = 0;
            if (dib == null || dib.Length < 40) return null;
            int hdr = BitConverter.ToInt32(dib, 0);              // biSize
            if (hdr < 40 || hdr > dib.Length) return null;
            int width = BitConverter.ToInt32(dib, 4);
            int height = BitConverter.ToInt32(dib, 8);           // 负数 = 自顶向下
            int bpp = BitConverter.ToInt16(dib, 14);             // biBitCount
            int comp = BitConverter.ToInt32(dib, 16);            // biCompression
            int clrUsed = BitConverter.ToInt32(dib, 32);
            if (width <= 0 || height == 0 || width > 16384 || Math.Abs(height) > 16384) return null;
            bool topDown = height < 0;
            int hAbs = Math.Abs(height);
            if (bpp != 32 && bpp != 24 && bpp != 16)
            {
                try { ModMain.P("[ImageInput] DIB 位深不支持: " + bpp + "bpp（仅 32/24/16）"); } catch { }
                return null;
            }

            // 通道掩码：V4/V5 头（≥52）掩码在头内固定偏移；40 字节头 + BI_BITFIELDS 在后跟 3 个 DWORD；
            // BI_RGB 用默认（32bpp 即 BGRA）。**40 字节头 + comp=3 时那 12 字节不是像素** ——
            // 漏掉会让像素整体错位（差分验证：修正前与参照 PNG 抽样全不符，修正后 0/560 不符）。
            uint rM, gM, bM;
            if (hdr >= 52)
            {
                rM = BitConverter.ToUInt32(dib, 40); gM = BitConverter.ToUInt32(dib, 44); bM = BitConverter.ToUInt32(dib, 48);
            }
            else if (comp == 3)
            {
                if (hdr + 12 > dib.Length) return null;
                rM = BitConverter.ToUInt32(dib, hdr); gM = BitConverter.ToUInt32(dib, hdr + 4); bM = BitConverter.ToUInt32(dib, hdr + 8);
            }
            else if (bpp == 16) { rM = 0x7C00; gM = 0x03E0; bM = 0x001F; }   // BI_RGB 16bpp = 5-5-5
            else { rM = 0x00FF0000; gM = 0x0000FF00; bM = 0x000000FF; }

            int masks = (comp == 3 && hdr == 40) ? 12 : 0;
            int palette = (clrUsed != 0) ? clrUsed * 4 : 0;
            int pixOff = hdr + masks + palette;
            int stride = ((width * bpp + 31) / 32) * 4;          // 每行 4 字节对齐
            if (pixOff < 0 || pixOff + (long)stride * hAbs > dib.Length) return null;

            int step = Mathf.Max(1, Mathf.CeilToInt(Mathf.Max(width, hAbs) / (float)maxSide));
            int dw = Mathf.Max(1, width / step), dh = Mathf.Max(1, hAbs / step);
            var px = new Color32[dw * dh];
            int bpi = bpp / 8;
            for (int y = 0; y < dh; y++)
            {
                // DIB 默认自下而上（行 0 = 底边），与 Unity 的 GetPixels32/SetPixels32 同序 →
                // 逐行原序搬运即正立；只有自顶向下（height<0）才需要翻。
                int srcRow = topDown ? (hAbs - 1 - y * step) : (y * step);
                int p = pixOff + srcRow * stride;
                int drow = y * dw;
                for (int x = 0; x < dw; x++)
                {
                    int q = p + x * step * bpi;
                    uint v = (bpp == 24)
                        ? (uint)(dib[q] | (dib[q + 1] << 8) | (dib[q + 2] << 16))
                        : BitConverter.ToUInt32(dib, q);
                    // alpha 一律 255：截图 CF_DIB 的第 4 字节恒为 0，当 A 读会得到全透明图
                    px[drow + x] = new Color32(Ex5(v, rM), Ex5(v, gM), Ex5(v, bM), 255);
                }
            }

            var tex = new Texture2D(dw, dh, TextureFormat.RGBA32, false);
            try
            {
                tex.SetPixels32(px);
                tex.Apply(false, false);
                byte[] png = ImageConversion.EncodeToPNG(tex);
                if (png == null || png.Length == 0) return null;
                outW = dw; outH = dh;
                return png;
            }
            finally { UnityEngine.Object.Destroy(tex); }
        }

        /// <summary>按位域掩码取值并归一化到 8 位（兼容 --等非 8 位通道）。</summary>
        private static byte Ex5(uint v, uint mask)
        {
            if (mask == 0) return 255;
            int shift = 0;
            while (shift < 32 && ((mask >> shift) & 1u) == 0) shift++;
            if (shift >= 32) return 0;
            uint m = mask >> shift;
            uint val = (v & mask) >> shift;
            int bits = 0;
            for (uint t = m; t != 0; t >>= 1) bits++;
            if (bits == 8) return (byte)val;
            if (bits == 0) return 0;
            return (byte)(val * 255u / ((1u << bits) - 1u));
        }

        /// <summary>剪贴板图落盘成临时文件，让它走「路径」这条唯一下游通道。返回路径，失败 null。</summary>
        private static string SaveTemp(byte[] bytes, int len, string ext)
        {
            try
            {
                string dir = Path.Combine(Path.GetTempPath(), "agent_loop_clip");
                Directory.CreateDirectory(dir);
                string file = Path.Combine(dir,
                    "clip_" + DateTime.Now.ToString("yyyyMMdd_HHmmss_fff") + ext);
                using (var fs = new FileStream(file, FileMode.Create, FileAccess.Write))
                    fs.Write(bytes, 0, len);
                PruneTemp(dir);
                return file;
            }
            catch (Exception e)
            {
                try { ModMain.P("[ImageInput] 临时落盘失败: " + e.Message); } catch { }
                return null;
            }
        }

        /// <summary>保留最近 30 个临时件（截图连点会攒得快；Temp 目录别留垃圾）。</summary>
        private static void PruneTemp(string dir)
        {
            try
            {
                var files = new DirectoryInfo(dir).GetFiles("clip_*");
                if (files.Length <= 30) return;
                Array.Sort(files, (a, b) => b.LastWriteTimeUtc.CompareTo(a.LastWriteTimeUtc));
                for (int i = 30; i < files.Length; i++) { try { files[i].Delete(); } catch { } }
            }
            catch { }
        }

        /// <summary>
        /// 剪贴板里若放着「资源管理器复制的图片文件」，返回其路径（图片后缀 + 文件存在）。
        /// 保留此名字供旧调用点使用；新代码请用 <see cref="ClipboardImages"/>（含位图来源 +）。
        /// </summary>
        public static List<string> ClipboardImagePaths()
        {
            string how;
            var r = ClipboardImages(out how);
            if (r.Count > 0)
            {
                try { ModMain.P("[ImageInput] 剪贴板取图 " + r.Count + " 个（" + how + "），首个=" + Path.GetFileName(r[0])); } catch { }
            }
            return r;
        }

        // ------------------------------------------------------------------
        // 入口 ②：文本 → （去掉路径的正文, 图片路径表）
        // ------------------------------------------------------------------

        /// <summary>
        /// 把输入框文本拆成「正文」+「图片路径」。识别规则（按优先级）：
        ///   1. 整行（去空白、去成对引号）= 存在的图片文件路径；
        ///   2. 行内**成对引号段** = 存在的图片文件路径（Ctrl+V 多张时写的就是 `"p1" "p2"`；
        ///      也兼容手动贴的带引号路径）—— 去掉这些片段，其余保留为正文；
        ///   3. 行内从第一个盘符（`X:\`）/ UNC（`\\`）起、直到行尾 = 存在的图片文件路径
        ///      —— 覆盖「这张图里是谁 D:\a b\x.png」这种正文+路径同行；
        ///   4. 其余行原样保留为正文。
        /// 路径**允许含空格**（`屏幕截图 002402.png`），所以按行判定、绝不按空白切词。
        /// 「复制文件地址」给的是带引号路径（`"C:\..."`），规则 1/2 都会去引号。
        /// </summary>
        public static void SplitPaths(string text, out string cleanText, out List<string> paths)
        {
            paths = new List<string>();
            if (string.IsNullOrEmpty(text)) { cleanText = text ?? ""; return; }
            var keep = new List<string>();
            var lines = text.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n');
            for (int i = 0; i < lines.Length; i++)
            {
                string t = lines[i].Trim();
                if (t.Length == 0) continue;

                string p;
                if (TryAsImagePath(t, out p)) { AddUnique(paths, p); continue; }   // ① 整行就是路径

                t = TakeQuotedPaths(t, paths);                                    // ② 行内引号段
                if (t.Length == 0) continue;

                string cand = DriveTail(t);
                if (cand != null && TryAsImagePath(cand, out p))                  // ③ 盘符到尾
                {
                    string head = t.Substring(0, t.Length - cand.Length).Trim();
                    AddUnique(paths, p);
                    if (head.Length > 0) keep.Add(head);
                    continue;
                }
                keep.Add(t);
            }
            cleanText = string.Join("\n", keep.ToArray()).Trim();
        }

        /// <summary>摘掉行内所有「成对 ASCII 引号且内容是存在的图片路径」的片段，返回剩余文本。</summary>
        private static string TakeQuotedPaths(string line, List<string> paths)
        {
            var sb = new StringBuilder();
            int i = 0;
            while (i < line.Length)
            {
                if (line[i] != '"') { sb.Append(line[i]); i++; continue; }
                int close = line.IndexOf('"', i + 1);
                if (close < 0) { sb.Append(line[i]); i++; continue; }          // 落单引号：原样留
                string seg = line.Substring(i + 1, close - i - 1);
                string p;
                if (TryAsImagePath(seg, out p)) { AddUnique(paths, p); i = close + 1; continue; }
                sb.Append(line, i, close - i + 1);                             // 引号内不是图：整段留
                i = close + 1;
            }
            return sb.ToString().Trim();
        }

        /// <summary>把路径写成带引号形式（多张/含空格时用；Windows「复制文件地址」同款）。</summary>
        public static string QuotePath(string p) { return "\"" + p + "\""; }

        /// <summary>整串当路径判定：去成对引号 → 图片后缀 → 文件确实存在。</summary>
        private static bool TryAsImagePath(string raw, out string path)
        {
            path = null;
            if (string.IsNullOrEmpty(raw)) return false;
            string s = raw.Trim();
            if (s.Length >= 2 &&
                ((s[0] == '"' && s[s.Length - 1] == '"') || (s[0] == '“' && s[s.Length - 1] == '”')))
                s = s.Substring(1, s.Length - 2).Trim();
            if (!IsImagePath(s)) return false;
            try { if (!File.Exists(s)) return false; } catch { return false; }
            path = s;
            return true;
        }

        /// <summary>取行内第一个「盘符/UNC 起点」到行尾的子串；没有返回 null。</summary>
        private static string DriveTail(string line)
        {
            for (int i = 0; i + 2 < line.Length; i++)
            {
                char c = line[i];
                bool drive = ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'))
                             && line[i + 1] == ':' && (line[i + 2] == '\\' || line[i + 2] == '/');
                bool unc = c == '\\' && line[i + 1] == '\\';
                if (drive || unc) return line.Substring(i);
            }
            return null;
        }

        private static bool IsImagePath(string s)
        {
            if (string.IsNullOrEmpty(s)) return false;
            string e;
            try { e = Path.GetExtension(s); } catch { return false; }
            if (string.IsNullOrEmpty(e)) return false;
            e = e.ToLowerInvariant();
            for (int i = 0; i < Exts.Length; i++) if (Exts[i] == e) return true;
            return false;
        }

        private static void AddUnique(List<string> list, string p)
        {
            for (int i = 0; i < list.Count; i++)
                if (string.Equals(list[i], p, StringComparison.OrdinalIgnoreCase)) return;
            list.Add(p);
        }

        // ------------------------------------------------------------------
        // 编码：路径 → 附件（JSON）
        // ------------------------------------------------------------------

        /// <summary>账本/气泡里的占位文本。**Python 侧不再改写 text**，所以这里就是唯一来源 ——
        /// 本地回显与落盘历史必然一致（跨语言同一格式串，改动需两边同步）。</summary>
        public static string Placeholder(string name) { return "[图片：" + name + "]"; }

        /// <summary>
        /// 路径表 → 附件数组。每项 `{name, url, w, h, kb}`，`url` 是可直接喂 OpenAI
        /// `image_url.url` 的 data URL（llm_adapter 原样透传）。
        /// 失败/超量的进 errors（人类可读），调用方用系统行提示。
        /// </summary>
        public static List<JObject> Build(List<string> paths, out List<string> names, out List<string> errors)
        {
            names = new List<string>();
            errors = new List<string>();
            var list = new List<JObject>();
            if (paths == null) return list;
            for (int i = 0; i < paths.Count; i++)
            {
                string p = paths[i];
                string fn = p;
                try { fn = Path.GetFileName(p); } catch { }
                if (list.Count >= MaxCount) { errors.Add("最多 " + MaxCount + " 张，已忽略 " + fn); continue; }
                try
                {
                    string mime; byte[] payload; int w, h;
                    if (!Encode(p, out mime, out payload, out w, out h) || payload == null || payload.Length == 0)
                    { errors.Add("读图失败：" + fn); continue; }
                    list.Add(new JObject
                    {
                        ["name"] = fn,
                        ["url"] = "data:" + mime + ";base64," + Convert.ToBase64String(payload),
                        ["w"] = w,
                        ["h"] = h,
                        ["kb"] = payload.Length / 1024,
                    });
                    names.Add(fn);
                    try { ModMain.P("[ImageInput] 附件 " + fn + " → " + mime + " " + w + "x" + h + " " + (payload.Length / 1024) + "KB"); } catch { }
                }
                catch (Exception e)
                {
                    errors.Add(fn + "：" + e.Message);
                    try { ModMain.P("[ImageInput] 编码失败 " + fn + ": " + e.Message); } catch { }
                }
            }
            return list;
        }

        /// <summary>单张图 → (mime, 字节)。尺寸与体积都在预算内时**原字节直发**（零重编码零损失）。</summary>
        private static bool Encode(string path, out string mime, out byte[] payload, out int w, out int h)
        {
            mime = null; payload = null; w = 0; h = 0;
            byte[] disk = File.ReadAllBytes(path);
            if (disk.Length == 0) return false;
            byte[] raw = NormalizeToPng(disk);

            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                if (!ImageConversion.LoadImage(tex, raw)) return false;   // interop 静态方法（非扩展）
                w = tex.width; h = tex.height;
                bool needResize = w > MaxSide || h > MaxSide;

                if (!needResize && raw.Length <= MaxBytes)
                {
                    // 归一过（BMP→PNG）就不能照扩展名报 mime，否则 data URL 的声明与实际字节不符
                    mime = ReferenceEquals(raw, disk) ? MimeOf(path) : "image/png";
                    payload = raw;
                    return true;
                }

                Texture2D small = needResize ? Fit(tex, MaxSide) : tex;
                try
                {
                    byte[] png = ImageConversion.EncodeToPNG(small);
                    if (png != null && png.Length > 0 && png.Length <= MaxBytes)
                    {
                        mime = "image/png"; payload = png;
                    }
                    else
                    {
                        // UI 截图 PNG 也可能很大 → 退 JPEG（q85）。图里文字会略糊，但能发出去优先。
                        byte[] jpg = ImageConversion.EncodeToJPG(small, 85);
                        if (jpg == null || jpg.Length == 0) return false;
                        mime = "image/jpeg"; payload = jpg;
                    }
                    w = small.width; h = small.height;
                }
                finally
                {
                    if (needResize && small != null) UnityEngine.Object.Destroy(small);
                }
                return payload != null && payload.Length > 0;
            }
            finally
            {
                if (tex != null) UnityEngine.Object.Destroy(tex);
            }
        }

        /// <summary>解一张图并等比缩到长边 ≤ maxSide，供**输入框预览缩略图**用。调用方负责 Destroy。</summary>
        public static Texture2D LoadThumb(string path, int maxSide, out int w, out int h)
        {
            w = 0; h = 0;
            try
            {
                byte[] raw = NormalizeToPng(File.ReadAllBytes(path));
                if (raw == null || raw.Length == 0) return null;
                var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                if (!ImageConversion.LoadImage(tex, raw)) { UnityEngine.Object.Destroy(tex); return null; }
                w = tex.width; h = tex.height;
                if (tex.width <= maxSide && tex.height <= maxSide) return tex;
                var small = Fit(tex, maxSide);
                UnityEngine.Object.Destroy(tex);
                return small;
            }
            catch (Exception e)
            {
                try { ModMain.P("[ImageInput] 缩略图失败 " + path + ": " + e.Message); } catch { }
                return null;
            }
        }

        /// <summary>
        /// 等比缩到长边 ≤ maxSide。**纯 CPU 行拷贝**：最近邻取样、逐行原序搬运。
        /// 不用 Graphics.Blit 是刻意的 —— Blit 的方向依赖 Unity 的 `_MainTex_TexelSize.y`
        /// 符号约定，发给模型的图用户看不到，翻转了就是静默错误；这里行序恒定，不可能翻。
        /// </summary>
        private static Texture2D Fit(Texture2D src, int maxSide)
        {
            int sw = src.width, sh = src.height;
            float k = Mathf.Min(1f, (float)maxSide / Mathf.Max(sw, sh));
            int dw = Mathf.Max(1, Mathf.RoundToInt(sw * k));
            int dh = Mathf.Max(1, Mathf.RoundToInt(sh * k));

            Color32[] sp = src.GetPixels32();            // 隐式转换 = 逐元素复制（行 0 = 底边）
            var dp = new Color32[dw * dh];
            for (int y = 0; y < dh; y++)
            {
                int srow = Mathf.Min((int)(y / k), sh - 1) * sw;
                int drow = y * dw;
                for (int x = 0; x < dw; x++)
                    dp[drow + x] = sp[srow + Mathf.Min((int)(x / k), sw - 1)];
            }
            var dst = new Texture2D(dw, dh, TextureFormat.RGBA32, false);
            dst.SetPixels32(dp);                          // 隐式转换回 Il2CppStructArray
            dst.Apply(false, false);
            return dst;
        }

        private static string MimeOf(string path)
        {
            string e;
            try { e = Path.GetExtension(path).ToLowerInvariant(); } catch { return "image/png"; }
            if (e == ".jpg" || e == ".jpeg") return "image/jpeg";
            if (e == ".bmp") return "image/bmp";
            if (e == ".gif") return "image/gif";
            if (e == ".webp") return "image/webp";
            return "image/png";
        }
    }
}
