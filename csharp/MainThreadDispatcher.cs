/// <summary>
/// 主线程调度器 —— 把后台线程（HttpListener）的请求转交游戏主线程（g.timer.OnUpdate）执行
///
/// 玩法：后台线程拿到 HTTP 请求后，构造一个 TaskItem { 要执行的委托, 结果容器 TCS } 塞进并发队列；
///       g.timer.Frame(OnUpdate, 1, true) 每帧在主线程从队列取一个执行，执行完用
///       TaskCompletionSource 通知后台线程把结果回写进 HTTP 响应。
///
/// 这样保证任何 g.world / g.conf 调用都在主线程，符合 IL2CPP 硬约束。
/// </summary>
using System;
using System.Collections.Concurrent;
using System.Threading.Tasks;

namespace AgentLoopBridge
{
    /// <summary>一个待主线程执行的任务：payload=实际要调用的游戏委托，完成时填充结果</summary>
    public class MainThreadJob
    {
        /// <summary>在主线程执行的实际调用（真正碰 g.world 的代码）</summary>
        public Func<object> Execute;
        /// <summary>执行结果（后台线程等待这个完成）</summary>
        public readonly TaskCompletionSource<object> TCS = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);

        public MainThreadJob(Func<object> execute)
        {
            Execute = execute;
        }
    }

    public class MainThreadDispatcher : IDisposable
    {
        private readonly ConcurrentQueue<MainThreadJob> _queue = new ConcurrentQueue<MainThreadJob>();
        private volatile bool _disposed;

        /// <summary>
        /// 后台线程调用：把任务入队。此方法本身在后台线程非阻塞（立即返回 job.Task），
        /// 调用方 await/等待 TCS 直到主线程真正执行完成。
        /// </summary>
        public Task<object> Enqueue(Func<object> execute)
        {
            var job = new MainThreadJob(execute);
            _queue.Enqueue(job);
            return job.TCS.Task;
        }

        /// <summary>
        /// 主线程每帧回调（由 g.timer.Frame 驱动）。一次只取一个任务执行，
        /// 避免单个请求阻塞主线程过久；剩余任务留到下一帧。
        /// </summary>
        public void OnUpdate()
        {
            if (_disposed) return;
            if (_queue.TryDequeue(out MainThreadJob job))
            {
                try
                {
                    job.TCS.TrySetResult(job.Execute());
                }
                catch (Exception e)
                {
                    // 必须留痕 本方法的返回值是一个 TaskCompletionSource，而调用方
                    // （WsClient 派发 UI 事件）**丢掉了那个 Task** —— 未观察的 Task 异常会被 .NET
                    // 静默忽略：既不崩、也不进日志。后果是 **UI 事件处理里任何异常都永久无声** ——
                    // 「NPC 回应中…」卡死的排查就卡在这里：`ChatPresenter.OnReplyEvent` 渲染
                    // 半途抛异常 → 后面的 `SetBusy(false)` 被跳过，而日志里一条异常都没有。
                    // 异常照旧回填 TCS（语义不变），但先打出来。
                    try { ModMain.P("[Dispatcher] 主线程任务异常: " + e); } catch { }
                    job.TCS.TrySetException(e);
                }
            }
        }

        public void Dispose()
        {
            _disposed = true;
            while (_queue.TryDequeue(out MainThreadJob job))
            {
                job.TCS.TrySetResult(null);
            }
        }
    }
}