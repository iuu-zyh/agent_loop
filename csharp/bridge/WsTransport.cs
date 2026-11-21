/// <summary>
/// 极简 RFC6455 WebSocket 客户端（TcpClient + NetworkStream，纯托管实现）。
///
/// 为什么不用 System.Net.WebSockets.ClientWebSocket：.NET Framework 版在 Windows 上委托原生
/// WebSocketProtocolComponent（独立原生线程 + 回调），在本游戏进程（IL2CPP + MelonLoader 的
/// mono）里首次 ConnectAsync 即卡死、随后原生崩溃——2026-09-06 登录期崩溃二分⑥实锤：
/// 停用 WS 线程后游戏正常，恢复即崩，且日志无线程任何 connected/error 输出。
/// mono 的 TcpClient/BSD socket 是 MelonLoader mod 的成熟路径（神识传音的 HTTP 同栈），
/// 故自实现握手与帧编解码；协议对端（Python websockets 标准库）与消息契约不变。
///
/// 支持子集（对本桥够用）：文本帧收发（客户端帧按 RFC 必须掩码）、Ping→Pong、Close、
/// 服务端分片（continuation）聚合、二进制帧按字节聚合容错。内部 _writeLock 保证
/// 接收线程回 Pong 与业务 Send 的并发写不交错（NetworkStream 单写者约束）。
/// </summary>
using System;
using System.IO;
using System.Net.Sockets;
using System.Text;

namespace AgentLoopBridge
{
    internal sealed class WsTransport : IDisposable
    {
        private TcpClient _tcp;
        private NetworkStream _stream;
        private readonly object _writeLock = new object();
        private readonly Random _maskRand = new Random();
        // 服务端分片聚合（控制帧可插在分片之间，不打断聚合）
        private readonly MemoryStream _frag = new MemoryStream();
        private int _fragOpcode = -1;

        /// <summary>连接是否可用（Send/收包前检查；等价旧 ClientWebSocket.State==Open）</summary>
        public bool IsOpen
        {
            get { return _stream != null && _tcp != null && _tcp.Connected; }
        }

        /// <summary>连接并完成 WebSocket 升级握手；失败抛异常（WsClient.RunLoop 的 catch/重试照旧）</summary>
        public void Connect(string host, int port, string path)
        {
            _tcp = new TcpClient();
            _tcp.Connect(host, port);
            _stream = _tcp.GetStream();
            Handshake(host, port, path);
        }

        /// <summary>HTTP Upgrade 握手：发出请求 → 读到 101 → 校验 Sec-WebSocket-Accept（防协议不匹配）</summary>
        private void Handshake(string host, int port, string path)
        {
            string key = Convert.ToBase64String(Guid.NewGuid().ToByteArray());   // 16 字节 → 24 字符
            string req =
                "GET " + path + " HTTP/1.1\r\n" +
                "Host: " + host + ":" + port + "\r\n" +
                "Upgrade: websocket\r\n" +
                "Connection: Upgrade\r\n" +
                "Sec-WebSocket-Key: " + key + "\r\n" +
                "Sec-WebSocket-Version: 13\r\n\r\n";
            byte[] reqBytes = Encoding.ASCII.GetBytes(req);
            _stream.Write(reqBytes, 0, reqBytes.Length);

            string resp = ReadUntilHeaders();
            if (resp.IndexOf(" 101", StringComparison.Ordinal) < 0)
                throw new IOException("WebSocket 握手失败: " + (resp.Split('\n')[0] ?? "").Trim());
            string expect = Convert.ToBase64String(System.Security.Cryptography.SHA1.Create()
                .ComputeHash(Encoding.ASCII.GetBytes(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")));
            if (!resp.Contains(expect))
                throw new IOException("WebSocket 握手 Accept 校验失败");
        }

        /// <summary>逐字节读到空行（HTTP 头结束），返回头文本</summary>
        private string ReadUntilHeaders()
        {
            var sb = new StringBuilder();
            while (true)
            {
                int b = _stream.ReadByte();
                if (b < 0) throw new IOException("握手时连接被关闭");
                sb.Append((char)b);
                if (sb.Length >= 4 && sb[sb.Length - 4] == '\r' && sb[sb.Length - 3] == '\n'
                    && sb[sb.Length - 2] == '\r' && sb[sb.Length - 1] == '\n')
                    return sb.ToString();
                if (sb.Length > 64 * 1024) throw new IOException("握手响应异常");
            }
        }

        /// <summary>
        /// 读一条完整消息（阻塞）。返回消息文本；对端关闭（Close 帧或断线）返回 null。
        /// 内部处理 Ping→Pong、Close→回包、分片聚合。
        /// </summary>
        public string ReadMessage()
        {
            while (true)
            {
                int b0 = _stream.ReadByte(); if (b0 < 0) return null;
                int b1 = _stream.ReadByte(); if (b1 < 0) return null;
                bool fin = (b0 & 0x80) != 0;
                int opcode = b0 & 0x0F;
                bool masked = (b1 & 0x80) != 0;          // 服务端帧应为不掩码；若掩码也照样解
                long len = b1 & 0x7F;
                if (len == 126) len = ReadLen(2);
                else if (len == 127) len = ReadLen(8);
                byte[] mask = masked ? ReadExact(4) : null;
                byte[] payload = len > 0 ? ReadExact((int)len) : new byte[0];
                if (mask != null)
                    for (int i = 0; i < payload.Length; i++) payload[i] ^= mask[i & 3];

                switch (opcode)
                {
                    case 0x8:   // Close：回 Close 并视为断开
                        try { SendFrame(0x8, payload.Length <= 125 ? payload : new byte[0]); } catch { }
                        Close();
                        return null;
                    case 0x9:   // Ping → Pong（同 payload）；控制帧不打断分片聚合
                        SendFrame(0xA, payload);
                        continue;
                    case 0xA:   // Pong
                        continue;
                    case 0x0:   // continuation
                    case 0x1:   // text
                    case 0x2:   // binary（本桥全为 JSON 文本；容错按字节聚合）
                        if (opcode != 0x0)
                        {
                            if (_fragOpcode != -1) _frag.SetLength(0);   // 上条未收完就来新帧：容错丢弃
                            _fragOpcode = opcode;
                        }
                        if (payload.Length > 0) _frag.Write(payload, 0, payload.Length);
                        if (fin)
                        {
                            _fragOpcode = -1;
                            string text = Encoding.UTF8.GetString(_frag.ToArray());
                            _frag.SetLength(0);
                            return text;
                        }
                        continue;
                    default:    // 未知帧忽略
                        continue;
                }
            }
        }

        /// <summary>发送文本帧（内部加锁，供业务线程与接收线程回 Pong 并发调用）</summary>
        public void SendText(string text)
        {
            SendFrame(0x1, Encoding.UTF8.GetBytes(text));
        }

        /// <summary>写一帧（客户端帧必须掩码）</summary>
        private void SendFrame(int opcode, byte[] payload)
        {
            lock (_writeLock)
            {
                var s = _stream;
                if (s == null) throw new IOException("not connected");
                byte[] mask = new byte[4];
                lock (_maskRand) _maskRand.NextBytes(mask);

                var head = new MemoryStream();
                head.WriteByte((byte)(0x80 | opcode));
                int len = payload.Length;
                if (len < 126) head.WriteByte((byte)(0x80 | len));
                else if (len <= 0xFFFF)
                {
                    head.WriteByte((byte)(0x80 | 126));
                    head.WriteByte((byte)(len >> 8));
                    head.WriteByte((byte)len);
                }
                else
                {
                    head.WriteByte((byte)(0x80 | 127));
                    for (int i = 7; i >= 0; i--) head.WriteByte((byte)(((long)len >> (8 * i)) & 0xFF));
                }
                head.Write(mask, 0, 4);

                byte[] headBytes = head.ToArray();
                byte[] masked = new byte[len];
                for (int i = 0; i < len; i++) masked[i] = (byte)(payload[i] ^ mask[i & 3]);

                s.Write(headBytes, 0, headBytes.Length);
                if (len > 0) s.Write(masked, 0, len);
                s.Flush();
            }
        }

        private long ReadLen(int bytes)
        {
            byte[] b = ReadExact(bytes);
            long v = 0;
            for (int i = 0; i < bytes; i++) v = (v << 8) | b[i];
            return v;
        }

        private byte[] ReadExact(int count)
        {
            byte[] buf = new byte[count];
            int off = 0;
            while (off < count)
            {
                int n = _stream.Read(buf, off, count - off);
                if (n <= 0) throw new IOException("socket closed");
                off += n;
            }
            return buf;
        }

        public void Close()
        {
            try { _stream?.Close(); } catch { }
            try { _tcp?.Close(); } catch { }
            _stream = null;
            _tcp = null;
        }

        public void Dispose() { Close(); }
    }
}
