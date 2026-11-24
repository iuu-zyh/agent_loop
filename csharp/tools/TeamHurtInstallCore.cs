namespace AgentLoopBridge
{
    internal sealed class TeamHurtInstallCore
    {
        internal const int MaxAttempts = 3;
        internal const double RetrySeconds = 1;
        internal int Attempts { get; private set; }
        internal bool Installed { get; private set; }
        internal bool Fatal { get; private set; }
        internal bool Stopped { get { return Installed || Fatal || Attempts >= MaxAttempts; } }
        private double _nextAttempt;

        internal bool TryBegin(bool ready, double now)
        {
            if (!ready || Stopped || now < _nextAttempt) return false;
            Attempts++;
            _nextAttempt = now + RetrySeconds;
            return true;
        }

        internal void Succeed() { Installed = true; }
        internal void Fail(bool fatal) { Fatal = fatal; }

        internal void Reset()
        {
            Attempts = 0;
            Installed = false;
            Fatal = false;
            _nextAttempt = 0;
        }
    }
}
