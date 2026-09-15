using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

public static class P {
    private static readonly string[] Exts = { ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp" };

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

        public static string QuotePath(string p) { return "\"" + p + "\""; }

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

}