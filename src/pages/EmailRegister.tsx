import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  cancelGoEmailBatch,
  getEmailRegistrationJob,
  getGoEmailBatch,
  startEmailRegistrationJob,
  startGoEmailBatch,
  type EmailRegistrationSnapshot,
  type GoEmailBatchResponse,
} from '../api/extract';
import { BusyNotice } from '../components/BusyNotice';

type EmailResourceProvider = 'icloud_api' | 'outlook_token' | 'icloud_privacy' | 'forwarded_domain' | 'cfworker_admin_api';

const sample = [
  'email@example.com----https://mail.example.com/show/token/email',
  'email@example.com----code:https://mail.example.com/api/code/token/email----mail:https://mail.example.com/api/mail/token/email',
].join('\n');

function isRunning(status?: string): boolean {
  return status === 'pending' || status === 'running';
}

export function EmailRegister() {
  const [mailboxText, setMailboxText] = useState('');
  const [useEmailResourcePool, setUseEmailResourcePool] = useState(false);
  const [emailResourceProvider, setEmailResourceProvider] = useState<EmailResourceProvider>('icloud_api');
  const [emailResourceCount, setEmailResourceCount] = useState(1);
  const [mailboxProxy, setMailboxProxy] = useState('');
  const [registrationProxies, setRegistrationProxies] = useState('');
  const [useProxyResourcePool, setUseProxyResourcePool] = useState(false);
  const [proxySeedRegion, setProxySeedRegion] = useState('JP');
  const [proxySeedTtl, setProxySeedTtl] = useState(10);
  const [proxySeedProtocol, setProxySeedProtocol] = useState<'socks5' | 'http' | 'https'>('socks5');
  const [proxyResourceCount, setProxyResourceCount] = useState(0);
  const [retryAttempts, setRetryAttempts] = useState(2);
  const [concurrency, setConcurrency] = useState(1);
  const [protocolBackend, setProtocolBackend] = useState<'python' | 'go'>('python');
  const [goWorkerUrl, setGoWorkerUrl] = useState('');
  const [headed, setHeaded] = useState(false);
  const [password, setPassword] = useState('');
  const [otpTimeout, setOtpTimeout] = useState(200);
  const [pollInterval, setPollInterval] = useState(3);
  const [jobId, setJobId] = useState('');
  const [snapshot, setSnapshot] = useState<EmailRegistrationSnapshot | null>(null);
  const [goBatchCount, setGoBatchCount] = useState(100);
  const [goBatchMaxConcurrent, setGoBatchMaxConcurrent] = useState(50);
  const [goBatchSnapshot, setGoBatchSnapshot] = useState<GoEmailBatchResponse | null>(null);
  const [goBatchWorking, setGoBatchWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const running = isRunning(snapshot?.status);
  const goBatchData = goBatchSnapshot?.snapshot || {};
  const goBatchRunning = Boolean(goBatchSnapshot?.batch_id && goBatchData.done !== true);
  const progress = useMemo(() => {
    if (!snapshot || snapshot.total <= 0) return '未开始';
    return `${snapshot.completed}/${snapshot.total}，成功 ${snapshot.success}，失败 ${snapshot.failed}`;
  }, [snapshot]);

  const refreshJob = useCallback(async (id: string) => {
    const next = await getEmailRegistrationJob(id);
    setSnapshot(next);
    return next;
  }, []);

  const refreshGoBatch = useCallback(async (id: string) => {
    const next = await getGoEmailBatch(id, goWorkerUrl);
    setGoBatchSnapshot(next);
    return next;
  }, [goWorkerUrl]);

  useEffect(() => {
    if (!jobId || !running) return undefined;
    const timer = window.setInterval(() => {
      void refreshJob(jobId).catch((err) => {
        setError(err instanceof Error ? err.message : '刷新注册任务失败');
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId, refreshJob, running]);

  useEffect(() => {
    if (!goBatchSnapshot?.batch_id || !goBatchRunning) return undefined;
    const timer = window.setInterval(() => {
      void refreshGoBatch(goBatchSnapshot.batch_id).catch((err) => {
        setError(err instanceof Error ? err.message : 'Go batch refresh failed');
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [goBatchRunning, goBatchSnapshot?.batch_id, refreshGoBatch]);

  const handleStart = useCallback(async () => {
    if (!mailboxText.trim() && !useEmailResourcePool) {
      setError('请先填写邮箱接码数据。');
      return;
    }
    setError(null);
    setSnapshot(null);
    try {
      const created = await startEmailRegistrationJob({
        mailbox_text: mailboxText,
        mailbox_proxy: mailboxProxy,
        use_email_resource_pool: useEmailResourcePool,
        email_resource_provider: emailResourceProvider,
        email_resource_count: emailResourceCount,
        registration_proxies: registrationProxies,
        use_proxy_resource_pool: useProxyResourcePool,
        proxy_resource_provider: 'proxy_seed',
        proxy_resource_count: proxyResourceCount,
        proxy_seed_region: proxySeedRegion,
        proxy_seed_ttl: proxySeedTtl,
        proxy_seed_protocol: proxySeedProtocol,
        registration_retry_attempts: retryAttempts,
        concurrency,
        email_protocol_backend: protocolBackend,
        go_email_protocol_url: protocolBackend === 'go' ? goWorkerUrl : '',
        headed,
        chatgpt_password: password,
        email_otp_timeout: otpTimeout,
        email_otp_poll_interval: pollInterval,
      });
      setJobId(created.job_id);
      await refreshJob(created.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '启动邮箱注册任务失败');
    }
  }, [concurrency, emailResourceCount, emailResourceProvider, goWorkerUrl, headed, mailboxProxy, mailboxText, otpTimeout, password, pollInterval, protocolBackend, proxyResourceCount, proxySeedProtocol, proxySeedRegion, proxySeedTtl, refreshJob, registrationProxies, retryAttempts, useEmailResourcePool, useProxyResourcePool]);

  const handleStartGoBatch = useCallback(async () => {
    setError(null);
    setGoBatchWorking(true);
    try {
      const started = await startGoEmailBatch({
        count: goBatchCount,
        max_concurrent: goBatchMaxConcurrent,
        go_email_protocol_url: goWorkerUrl,
        mailbox_provider: emailResourceProvider,
        proxy_seed_region: proxySeedRegion,
        proxy_seed_ttl: proxySeedTtl,
        email_otp_timeout: Math.max(60, Math.min(240, otpTimeout)),
        go_batch_timeout_seconds: Math.max(120, Math.min(1800, otpTimeout + 90)),
        email_tries: 5,
        skip_phone: true,
      });
      setGoBatchSnapshot(started);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Go batch start failed');
    } finally {
      setGoBatchWorking(false);
    }
  }, [emailResourceProvider, goBatchCount, goBatchMaxConcurrent, goWorkerUrl, otpTimeout, proxySeedRegion, proxySeedTtl]);

  const handleCancelGoBatch = useCallback(async () => {
    if (!goBatchSnapshot?.batch_id) return;
    setError(null);
    setGoBatchWorking(true);
    try {
      const cancelled = await cancelGoEmailBatch(goBatchSnapshot.batch_id, goWorkerUrl);
      setGoBatchSnapshot(cancelled);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Go batch cancel failed');
    } finally {
      setGoBatchWorking(false);
    }
  }, [goBatchSnapshot?.batch_id, goWorkerUrl]);

  return (
    <div className="space-y-5">
      <BusyNotice
        active={running}
        label="邮箱注册任务运行中"
        detail="后端正在调用注册执行器，成功的账号会自动写入账号库。"
      />

      <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">邮箱注册</h2>
            <p className="mt-1 text-sm text-gray-500">
              使用邮箱地址和接码地址注册账号，完成后自动保存 AT 到账号库。
            </p>
          </div>
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
            需要后端可加载参考注册器；未配置时会返回明确错误，不影响其他功能。
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Go 批量注册</h3>
            <p className="mt-1 text-xs text-gray-500">适合大量邮箱注册任务，调用 Go worker 执行批量控制；少量调试可继续使用下方普通注册。</p>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-gray-600">数量</span>
              <input
                type="number"
                min={1}
                max={5000}
                value={goBatchCount}
                onChange={(event) => setGoBatchCount(Number(event.target.value) || 1)}
                disabled={goBatchWorking || goBatchRunning}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-gray-600">并发</span>
              <input
                type="number"
                min={1}
                max={5000}
                value={goBatchMaxConcurrent}
                onChange={(event) => setGoBatchMaxConcurrent(Number(event.target.value) || 1)}
                disabled={goBatchWorking || goBatchRunning}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
            <button
              type="button"
              onClick={() => void handleStartGoBatch()}
              disabled={goBatchWorking || goBatchRunning}
              className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600 disabled:bg-gray-300"
            >
              {goBatchWorking || goBatchRunning ? '运行中' : '启动批量'}
            </button>
            <button
              type="button"
              onClick={() => void handleCancelGoBatch()}
              disabled={goBatchWorking || !goBatchRunning}
              className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:text-gray-300"
            >
              取消
            </button>
          </div>
        </div>
        {goBatchSnapshot && (
          <div className="mt-3 grid gap-2 rounded-md border border-gray-100 bg-gray-50 p-3 text-xs text-gray-600 sm:grid-cols-4 lg:grid-cols-8">
            <div>批次 <span className="font-mono text-gray-900">{goBatchSnapshot.batch_id || '-'}</span></div>
            <div>排队 <span className="font-mono text-gray-900">{String(goBatchData.queued ?? '-')}</span></div>
            <div>运行 <span className="font-mono text-gray-900">{String(goBatchData.running ?? '-')}</span></div>
            <div>等 OTP <span className="font-mono text-gray-900">{String(goBatchData.waiting_for_otp ?? '-')}</span></div>
            <div>成功 <span className="font-mono text-emerald-700">{String(goBatchData.succeeded ?? '-')}</span></div>
            <div>失败 <span className="font-mono text-rose-700">{String(goBatchData.failed ?? '-')}</span></div>
            <div>已取消 <span className="font-mono text-gray-900">{String(goBatchData.cancelled ?? '-')}</span></div>
            <div>完成 <span className="font-mono text-gray-900">{String(goBatchData.done ?? false)}</span></div>
          </div>
        )}
      </section>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_360px]">
        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="mb-3 rounded-lg border border-gray-100 bg-gray-50 p-3">
            <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
              <input
                type="checkbox"
                checked={useEmailResourcePool}
                onChange={(event) => setUseEmailResourcePool(event.target.checked)}
                disabled={running}
              />
              使用邮箱资源池
            </label>
            {useEmailResourcePool && (
              <label className="mt-3 block max-w-xs">
                <span className="mb-1.5 block text-xs font-medium text-gray-600">Email provider</span>
                <select
                  value={emailResourceProvider}
                  onChange={(event) => setEmailResourceProvider(event.target.value as EmailResourceProvider)}
                  disabled={running}
                  className="mb-3 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                >
                  <option value="icloud_api">iCloud API</option>
                  <option value="outlook_token">Outlook Token</option>
                  <option value="icloud_privacy">iCloud Privacy</option>
                  <option value="forwarded_domain">Forwarded Domain</option>
                  <option value="cfworker_admin_api">CFWorker Mail</option>
                </select>
                <span className="mb-1.5 block text-xs font-medium text-gray-600">租用邮箱数量</span>
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={emailResourceCount}
                  onChange={(event) => setEmailResourceCount(Number(event.target.value) || 1)}
                  disabled={running}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </label>
            )}
          </div>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-gray-700">邮箱接码数据</span>
            <textarea
              value={mailboxText}
              onChange={(event) => setMailboxText(event.target.value)}
              rows={16}
              placeholder={sample}
              disabled={running}
              className="max-h-[58vh] w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
            />
          </label>
        </div>

        <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-gray-700">注册 IP 池</span>
            <textarea
              value={registrationProxies}
              onChange={(event) => setRegistrationProxies(event.target.value)}
              rows={5}
              placeholder="一行一个代理，支持 http://user:pass@host:port / host:port:user:pass"
              disabled={running}
              className="max-h-40 w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
            />
          </label>

          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
            <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
              <input
                type="checkbox"
                checked={useProxyResourcePool}
                onChange={(event) => setUseProxyResourcePool(event.target.checked)}
                disabled={running}
              />
              使用资源池代理 Seed
            </label>
            {useProxyResourcePool && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-gray-600">国家/地区</span>
                  <input
                    value={proxySeedRegion}
                    onChange={(event) => setProxySeedRegion(event.target.value.toUpperCase())}
                    placeholder="JP / US / VN"
                    disabled={running}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-gray-600">会话时长</span>
                  <input
                    type="number"
                    min={1}
                    max={1440}
                    value={proxySeedTtl}
                    onChange={(event) => setProxySeedTtl(Number(event.target.value) || 10)}
                    disabled={running}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-gray-600">协议</span>
                  <select
                    value={proxySeedProtocol}
                    onChange={(event) => setProxySeedProtocol(event.target.value as 'socks5' | 'http' | 'https')}
                    disabled={running}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                  >
                    <option value="socks5">SOCKS5</option>
                    <option value="http">HTTP</option>
                    <option value="https">HTTPS</option>
                  </select>
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-gray-600">生成数量</span>
                  <input
                    type="number"
                    min={0}
                    max={500}
                    value={proxyResourceCount}
                    onChange={(event) => setProxyResourceCount(Number(event.target.value) || 0)}
                    disabled={running}
                    placeholder="0=自动"
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                  />
                </label>
              </div>
            )}
          </div>

          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-gray-700">接码代理</span>
            <input
              value={mailboxProxy}
              onChange={(event) => setMailboxProxy(event.target.value)}
              placeholder="可选，仅用于请求接码 URL"
              disabled={running}
              className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">Protocol backend</span>
              <select
                value={protocolBackend}
                onChange={(event) => setProtocolBackend(event.target.value as 'python' | 'go')}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              >
                <option value="python">Python</option>
                <option value="go">Go Worker</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">Go worker URL</span>
              <input
                value={goWorkerUrl}
                onChange={(event) => setGoWorkerUrl(event.target.value)}
                disabled={running || goBatchRunning}
                placeholder="http://127.0.0.1:18765"
                className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">并发</span>
              <input
                type="number"
                min={1}
                max={8}
                value={concurrency}
                onChange={(event) => setConcurrency(Number(event.target.value) || 1)}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">失败换 IP 次数</span>
              <input
                type="number"
                min={1}
                max={5}
                value={retryAttempts}
                onChange={(event) => setRetryAttempts(Number(event.target.value) || 1)}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">验证码超时</span>
              <input
                type="number"
                min={30}
                max={1200}
                value={otpTimeout}
                onChange={(event) => setOtpTimeout(Number(event.target.value) || 200)}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">轮询间隔</span>
              <input
                type="number"
                min={1}
                max={30}
                value={pollInterval}
                onChange={(event) => setPollInterval(Number(event.target.value) || 3)}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
            <label className="flex items-end gap-2 pb-2 text-sm text-gray-700">
              <input type="checkbox" checked={headed} onChange={(event) => setHeaded(event.target.checked)} disabled={running} />
              显示浏览器
            </label>
          </div>

          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-gray-700">统一密码</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="留空则自动生成"
              disabled={running}
              className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
            />
          </label>

          <button
            type="button"
            onClick={() => void handleStart()}
            disabled={running || (!mailboxText.trim() && !useEmailResourcePool)}
            className="w-full rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-gray-800 disabled:bg-gray-300"
          >
            {running ? '注册中...' : '开始邮箱注册'}
          </button>
        </div>
      </section>

      {error && <div className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

      {snapshot && (
        <section className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">任务结果</h3>
            <div className="mt-3 space-y-2 text-sm text-gray-600">
              <div>任务：<span className="font-mono text-xs">{snapshot.job_id}</span></div>
              <div>状态：{snapshot.status}</div>
              <div>进度：{progress}</div>
              {snapshot.error && <div className="text-rose-600">{snapshot.error}</div>}
            </div>
            <div className="mt-4 space-y-2">
              {snapshot.items.map((item) => (
                <div key={`${item.email}-${item.account_id || item.error || ''}`} className={`rounded-md border px-3 py-2 text-xs ${item.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700'}`}>
                  <div className="font-medium">{item.email}</div>
                  <div className="mt-1 break-all">{item.ok ? item.account_id || '已写入账号库' : item.error}</div>
                  <div className="mt-1 text-[11px] opacity-80">
                    代理 {item.proxy_label || 'direct'} · 尝试 {item.attempts || 1} 次
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">运行日志</h3>
            <div className="mt-3 max-h-[44vh] overflow-y-auto rounded-md bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-100">
              {snapshot.logs.length === 0 ? (
                <div className="text-gray-400">暂无日志</div>
              ) : (
                snapshot.logs.map((log, index) => (
                  <div key={`${log.timestamp}-${index}`} className={log.level === 'error' ? 'text-rose-300' : 'text-gray-100'}>
                    [{new Date(log.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}] {log.message}
                  </div>
                ))
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
