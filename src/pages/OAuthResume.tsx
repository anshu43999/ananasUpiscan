import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getOAuthResumeJob,
  listAccounts,
  startOAuthResumeJob,
  type AccountLibraryItem,
  type OAuthResumeSnapshot,
} from '../api/extract';
import { BusyNotice } from '../components/BusyNotice';

type BindSmsProvider = '' | 'user_phone_url' | 'bind_user_phone_url' | 'herosms' | 'smsbower' | 'sms_activate';
type EmailResourceProvider = 'icloud_api' | 'outlook_token' | 'icloud_privacy' | 'forwarded_domain' | 'cfworker_admin_api';

const resumeSample = JSON.stringify(
  {
    email: 'user@example.com',
    password: 'ChatGPTPassword',
    browser_storage_state_path: 'data/registered_accounts/storage_xxx.json',
    account_id: 'optional-account-id',
    plan_type: 'free',
  },
  null,
  2,
);

const mailboxSample = [
  'user@example.com----https://mail.example.com/show/token/user',
  'bind@example.com----code:https://mail.example.com/api/code/token/bind----mail:https://mail.example.com/api/mail/token/bind',
].join('\n');

function isRunning(status?: string): boolean {
  return status === 'pending' || status === 'running';
}

function parseAccountIds(value: string): number[] {
  const ids = value
    .split(/[\s,，;；]+/)
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isInteger(item) && item > 0);
  return Array.from(new Set(ids));
}

function statusTone(status: string): string {
  if (status === 'completed') return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (status === 'failed') return 'text-rose-700 bg-rose-50 border-rose-200';
  if (status === 'running' || status === 'pending') return 'text-sky-700 bg-sky-50 border-sky-200';
  return 'text-gray-700 bg-gray-50 border-gray-200';
}

export function OAuthResume() {
  const [accountIdsText, setAccountIdsText] = useState('');
  const [resumeJson, setResumeJson] = useState('');
  const [bindEmailText, setBindEmailText] = useState('');
  const [bindEmailUseResourcePool, setBindEmailUseResourcePool] = useState(false);
  const [bindEmailResourceProvider, setBindEmailResourceProvider] = useState<EmailResourceProvider>('icloud_api');
  const [mailboxProxy, setMailboxProxy] = useState('');
  const [bindSmsProvider, setBindSmsProvider] = useState<BindSmsProvider>('');
  const [bindSmsPhoneUrls, setBindSmsPhoneUrls] = useState('');
  const [bindSmsApiKey, setBindSmsApiKey] = useState('');
  const [bindSmsProxy, setBindSmsProxy] = useState('');
  const [bindSmsService, setBindSmsService] = useState('dr');
  const [bindSmsCountry, setBindSmsCountry] = useState('');
  const [bindSmsMaxPrice, setBindSmsMaxPrice] = useState('');
  const [bindSmsMinPrice, setBindSmsMinPrice] = useState('');
  const [bindSmsProviderIds, setBindSmsProviderIds] = useState('');
  const [registrationProxies, setRegistrationProxies] = useState('');
  const [useProxyResourcePool, setUseProxyResourcePool] = useState(false);
  const [proxySeedRegion, setProxySeedRegion] = useState('JP');
  const [proxySeedTtl, setProxySeedTtl] = useState(10);
  const [proxySeedProtocol, setProxySeedProtocol] = useState<'socks5' | 'http' | 'https'>('socks5');
  const [proxyResourceCount, setProxyResourceCount] = useState(0);
  const [retryAttempts, setRetryAttempts] = useState(2);
  const [concurrency, setConcurrency] = useState(1);
  const [headed, setHeaded] = useState(false);
  const [allowPageFallback, setAllowPageFallback] = useState(true);
  const [password, setPassword] = useState('');
  const [otpTimeout, setOtpTimeout] = useState(200);
  const [pollInterval, setPollInterval] = useState(3);
  const [accounts, setAccounts] = useState<AccountLibraryItem[]>([]);
  const [jobId, setJobId] = useState('');
  const [snapshot, setSnapshot] = useState<OAuthResumeSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const running = isRunning(snapshot?.status);
  const accountIds = useMemo(() => parseAccountIds(accountIdsText), [accountIdsText]);
  const bindUsesResourcePool = bindSmsProvider === 'bind_user_phone_url';
  const progress = useMemo(() => {
    if (!snapshot || snapshot.total <= 0) return '未开始';
    return `${snapshot.completed}/${snapshot.total}，成功 ${snapshot.success}，失败 ${snapshot.failed}`;
  }, [snapshot]);

  const refreshAccounts = useCallback(async () => {
    const response = await listAccounts({ status: 'active', limit: 300 });
    setAccounts(response.items.filter((account) => account.has_session_json));
  }, []);

  const refreshJob = useCallback(async (id: string) => {
    const next = await getOAuthResumeJob(id);
    setSnapshot(next);
    return next;
  }, []);

  useEffect(() => {
    void refreshAccounts().catch(() => undefined);
  }, [refreshAccounts]);

  useEffect(() => {
    if (!jobId || !running) return undefined;
    const timer = window.setInterval(() => {
      void refreshJob(jobId).catch((err) => {
        setError(err instanceof Error ? err.message : '刷新 OAuth 续跑任务失败');
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId, refreshJob, running]);

  const handleStart = useCallback(async () => {
    if (accountIds.length === 0 && !resumeJson.trim()) {
      setError('请填写账号库 ID，或粘贴 resume JSON。');
      return;
    }
    setError(null);
    setSnapshot(null);
    try {
      const created = await startOAuthResumeJob({
        account_ids: accountIds,
        resume_json: resumeJson,
        bind_email_text: bindEmailText,
        mailbox_proxy: mailboxProxy,
        bind_email_use_resource_pool: bindEmailUseResourcePool,
        bind_email_resource_provider: bindEmailResourceProvider,
        bind_sms_provider: bindSmsProvider,
        bind_use_resource_pool: bindUsesResourcePool,
        bind_resource_provider: 'bind_user_phone_url',
        bind_sms_phone_urls: bindSmsPhoneUrls,
        bind_sms_proxy: bindSmsProxy,
        bind_sms_api_key: bindSmsApiKey,
        bind_sms_service: bindSmsService,
        bind_sms_country: bindSmsCountry,
        bind_herosms_api_key: bindSmsProvider === 'herosms' ? bindSmsApiKey : '',
        bind_herosms_service: bindSmsProvider === 'herosms' ? bindSmsService : '',
        bind_herosms_country: bindSmsProvider === 'herosms' ? bindSmsCountry : '',
        bind_herosms_max_price: bindSmsProvider === 'herosms' && bindSmsMaxPrice.trim() ? Number(bindSmsMaxPrice) : null,
        bind_smsbower_api_key: bindSmsProvider === 'smsbower' ? bindSmsApiKey : '',
        bind_smsbower_service: bindSmsProvider === 'smsbower' ? bindSmsService : '',
        bind_smsbower_country: bindSmsProvider === 'smsbower' ? bindSmsCountry : '',
        bind_smsbower_max_price: bindSmsProvider === 'smsbower' && bindSmsMaxPrice.trim() ? Number(bindSmsMaxPrice) : null,
        bind_smsbower_min_price: bindSmsProvider === 'smsbower' && bindSmsMinPrice.trim() ? Number(bindSmsMinPrice) : null,
        bind_smsbower_provider_ids: bindSmsProviderIds,
        bind_sms_activate_api_key: bindSmsProvider === 'sms_activate' ? bindSmsApiKey : '',
        bind_sms_activate_country: bindSmsProvider === 'sms_activate' ? bindSmsCountry : '',
        registration_proxies: registrationProxies,
        use_proxy_resource_pool: useProxyResourcePool,
        proxy_resource_provider: 'proxy_seed',
        proxy_resource_count: proxyResourceCount,
        proxy_seed_region: proxySeedRegion,
        proxy_seed_ttl: proxySeedTtl,
        proxy_seed_protocol: proxySeedProtocol,
        registration_retry_attempts: retryAttempts,
        concurrency,
        headed,
        chatgpt_password: password,
        email_otp_timeout: otpTimeout,
        email_otp_poll_interval: pollInterval,
        allow_page_fallback: allowPageFallback,
      });
      setJobId(created.job_id);
      await refreshJob(created.job_id);
      await refreshAccounts();
    } catch (err) {
      setError(err instanceof Error ? err.message : '启动 OAuth 续跑任务失败');
    }
  }, [
    accountIds,
    allowPageFallback,
    bindEmailText,
    bindEmailUseResourcePool,
    bindEmailResourceProvider,
    bindSmsApiKey,
    bindSmsCountry,
    bindSmsMaxPrice,
    bindSmsMinPrice,
    bindSmsPhoneUrls,
    bindSmsProvider,
    bindSmsProviderIds,
    bindSmsProxy,
    bindSmsService,
    bindUsesResourcePool,
    concurrency,
    headed,
    mailboxProxy,
    otpTimeout,
    password,
    pollInterval,
    proxyResourceCount,
    proxySeedProtocol,
    proxySeedRegion,
    proxySeedTtl,
    refreshAccounts,
    refreshJob,
    registrationProxies,
    resumeJson,
    retryAttempts,
    useProxyResourcePool,
  ]);

  const appendAccountId = useCallback((id: number) => {
    setAccountIdsText((current) => {
      const ids = parseAccountIds(current);
      if (!ids.includes(id)) ids.push(id);
      return ids.join(', ');
    });
  }, []);

  return (
    <div className="space-y-5">
      <BusyNotice
        active={running}
        label="OAuth 绑定/续跑任务运行中"
        detail="后端正在恢复浏览器登录态、完成 OAuth 授权，并把新的 token 回写账号库。"
      />

      <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 bg-gray-950 px-5 py-4 text-white">
          <h2 className="text-base font-semibold">OAuth 绑定 / resume 续跑</h2>
          <p className="mt-1 max-w-3xl text-sm text-gray-300">
            使用注册阶段保存的 browser storage state 恢复会话，执行 OAuth 授权，成功后保存 access_token、refresh_token、id_token 到账号库 session_json。
          </p>
        </div>
        <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="space-y-4">
            <div className="rounded-lg border border-gray-200 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">账号库续跑</h3>
                  <p className="mt-1 text-xs text-gray-500">填写账号库 ID，系统会读取该账号保存的 session_json 和 storage_state。</p>
                </div>
                <button
                  type="button"
                  onClick={() => void refreshAccounts()}
                  className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
                >
                  刷新账号
                </button>
              </div>
              <input
                value={accountIdsText}
                onChange={(event) => setAccountIdsText(event.target.value)}
                placeholder="例如：12, 15, 18"
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
              <div className="mt-3 max-h-48 overflow-y-auto rounded-md border border-gray-100 bg-gray-50 p-2">
                {accounts.length === 0 ? (
                  <div className="px-2 py-4 text-center text-xs text-gray-500">暂无带 session_json 的账号</div>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                    {accounts.slice(0, 40).map((account) => (
                      <button
                        type="button"
                        key={account.id}
                        onClick={() => appendAccountId(account.id)}
                        disabled={running}
                        className="min-w-0 rounded-md border border-gray-200 bg-white px-3 py-2 text-left text-xs hover:border-emerald-300 hover:bg-emerald-50 disabled:opacity-60"
                      >
                        <span className="block font-semibold text-gray-900">#{account.id} {account.email || account.account_id || account.account_key}</span>
                        <span className="mt-0.5 block truncate text-gray-500">{account.source || 'unknown'} · {account.health_status}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <label className="block rounded-lg border border-gray-200 p-4">
              <span className="mb-1.5 block text-sm font-semibold text-gray-900">直接粘贴 resume JSON</span>
              <textarea
                value={resumeJson}
                onChange={(event) => setResumeJson(event.target.value)}
                rows={12}
                placeholder={resumeSample}
                disabled={running}
                className="max-h-[44vh] w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>

            <label className="block rounded-lg border border-gray-200 p-4">
              <span className="mb-1.5 block text-sm font-semibold text-gray-900">邮箱 OTP / 绑定邮箱接码数据</span>
              <span className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-700">
                <input
                  type="checkbox"
                  checked={bindEmailUseResourcePool}
                  onChange={(event) => setBindEmailUseResourcePool(event.target.checked)}
                  disabled={running}
                />
                使用邮箱资源池
              </span>
              {bindEmailUseResourcePool && (
                <select
                  value={bindEmailResourceProvider}
                  onChange={(event) => setBindEmailResourceProvider(event.target.value as EmailResourceProvider)}
                  disabled={running}
                  className="mb-3 w-full max-w-xs rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                >
                  <option value="icloud_api">iCloud API</option>
                  <option value="outlook_token">Outlook Token</option>
                  <option value="icloud_privacy">iCloud Privacy</option>
                  <option value="forwarded_domain">Forwarded Domain</option>
                  <option value="cfworker_admin_api">CFWorker Mail</option>
                </select>
              )}
              <textarea
                value={bindEmailText}
                onChange={(event) => setBindEmailText(event.target.value)}
                rows={6}
                placeholder={mailboxSample}
                disabled={running}
                className="max-h-56 w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
              <span className="mt-2 block text-xs text-gray-500">如果 OAuth 流程触发邮箱验证码或 add_email，会使用这里的邮箱接码接口轮询验证码。</span>
            </label>
          </div>

            <div className="rounded-lg border border-gray-200 p-4">
              <div className="mb-3">
                <h3 className="text-sm font-semibold text-gray-900">手机号绑定</h3>
                <p className="mt-1 text-xs text-gray-500">
                  默认不启用。只有 OAuth 续跑遇到 add_phone 时才会租号或读取这里的手机号接码 URL。
                </p>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">绑定短信来源</span>
                  <select
                    value={bindSmsProvider}
                    onChange={(event) => setBindSmsProvider(event.target.value as BindSmsProvider)}
                    disabled={running}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                  >
                    <option value="">不自动绑定手机号</option>
                    <option value="user_phone_url">自备手机号 URL</option>
                    <option value="bind_user_phone_url">绑定手机号池</option>
                    <option value="herosms">HeroSMS</option>
                    <option value="smsbower">SMSBower</option>
                    <option value="sms_activate">SMS-Activate</option>
                  </select>
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">绑定接码代理</span>
                  <input
                    value={bindSmsProxy}
                    onChange={(event) => setBindSmsProxy(event.target.value)}
                    placeholder="可选，仅用于短信服务商或接码 URL"
                    disabled={running || !bindSmsProvider}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                  />
                </label>
              </div>

              {bindUsesResourcePool && (
                <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-800">
                  OAuth 续跑遇到 add_phone 时会从“资源池 / 绑定手机号池”租用号码，并在成功、冷却、禁用状态间自动回写。
                </div>
              )}

              {bindSmsProvider === 'user_phone_url' && (
                <label className="mt-3 block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">绑定手机号接码数据</span>
                  <textarea
                    value={bindSmsPhoneUrls}
                    onChange={(event) => setBindSmsPhoneUrls(event.target.value)}
                    rows={4}
                    placeholder="+15551234567|https://sms.example.com/latest?phone=15551234567"
                    disabled={running}
                    className="max-h-40 w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                  />
                </label>
              )}

              {bindSmsProvider && bindSmsProvider !== 'user_phone_url' && !bindUsesResourcePool && (
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <label className="block md:col-span-2">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">短信服务商 API Key</span>
                    <input
                      value={bindSmsApiKey}
                      onChange={(event) => setBindSmsApiKey(event.target.value)}
                      placeholder="服务商 API Key"
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">服务编号</span>
                    <input
                      value={bindSmsService}
                      onChange={(event) => setBindSmsService(event.target.value)}
                      placeholder="默认 dr"
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">国家编号</span>
                    <input
                      value={bindSmsCountry}
                      onChange={(event) => setBindSmsCountry(event.target.value)}
                      placeholder="例如 187 / 52 / 73"
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">maxPrice</span>
                    <input
                      value={bindSmsMaxPrice}
                      onChange={(event) => setBindSmsMaxPrice(event.target.value)}
                      placeholder={bindSmsProvider === 'smsbower' ? 'SMSBower 必填' : '可选'}
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                    />
                  </label>
                  {bindSmsProvider === 'smsbower' && (
                    <>
                      <label className="block">
                        <span className="mb-1.5 block text-sm font-medium text-gray-700">minPrice</span>
                        <input
                          value={bindSmsMinPrice}
                          onChange={(event) => setBindSmsMinPrice(event.target.value)}
                          placeholder="可选"
                          disabled={running}
                          className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                        />
                      </label>
                      <label className="block md:col-span-2">
                        <span className="mb-1.5 block text-sm font-medium text-gray-700">providerIds</span>
                        <input
                          value={bindSmsProviderIds}
                          onChange={(event) => setBindSmsProviderIds(event.target.value)}
                          placeholder="逗号分隔，可选"
                          disabled={running}
                          className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                        />
                      </label>
                    </>
                  )}
                </div>
              )}
            </div>

          <aside className="space-y-4">
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
              <h3 className="text-sm font-semibold text-gray-900">运行参数</h3>
              <div className="mt-4 space-y-3">
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">OAuth IP 池</span>
                  <textarea
                    value={registrationProxies}
                    onChange={(event) => setRegistrationProxies(event.target.value)}
                    rows={5}
                    placeholder="一行一个代理；留空则使用 resume 内代理或直连"
                    disabled={running}
                    className="max-h-40 w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                  />
                </label>

                <div className="rounded-lg border border-gray-200 bg-white p-3">
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
                    className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                  />
                </label>

                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">并发</span>
                    <input
                      type="number"
                      min={1}
                      max={6}
                      value={concurrency}
                      onChange={(event) => setConcurrency(Number(event.target.value) || 1)}
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
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
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                    />
                  </label>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">OTP 超时</span>
                    <input
                      type="number"
                      min={30}
                      max={1200}
                      value={otpTimeout}
                      onChange={(event) => setOtpTimeout(Number(event.target.value) || 200)}
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">轮询间隔</span>
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={pollInterval}
                      onChange={(event) => setPollInterval(Number(event.target.value) || 3)}
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                    />
                  </label>
                </div>

                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">覆盖密码</span>
                  <input
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="留空使用账号库或 resume JSON 中的密码"
                    disabled={running}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                  />
                </label>

                <div className="grid gap-2 text-sm text-gray-700">
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={headed} onChange={(event) => setHeaded(event.target.checked)} disabled={running} />
                    显示浏览器
                  </label>
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={allowPageFallback} onChange={(event) => setAllowPageFallback(event.target.checked)} disabled={running} />
                    JSON 流程失败时允许页面 fallback
                  </label>
                </div>

                <button
                  type="button"
                  onClick={() => void handleStart()}
                  disabled={running || (accountIds.length === 0 && !resumeJson.trim())}
                  className="w-full rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-gray-800 disabled:bg-gray-300"
                >
                  {running ? 'OAuth 续跑中...' : '开始 OAuth 绑定/续跑'}
                </button>
              </div>
            </div>

            {snapshot && (
              <div className={`rounded-lg border p-4 ${statusTone(snapshot.status)}`}>
                <div className="text-sm font-semibold">任务状态：{snapshot.status}</div>
                <div className="mt-2 text-xs">任务 ID：<span className="font-mono">{snapshot.job_id}</span></div>
                <div className="mt-1 text-xs">进度：{progress}</div>
                {snapshot.error && <div className="mt-2 break-all text-xs">{snapshot.error}</div>}
              </div>
            )}
          </aside>
        </div>
      </section>

      {error && <div className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

      {snapshot && (
        <section className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
          <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">回写结果</h3>
            <div className="mt-4 space-y-2">
              {snapshot.items.length === 0 ? (
                <div className="rounded-md bg-gray-50 px-3 py-6 text-center text-xs text-gray-500">等待结果...</div>
              ) : (
                snapshot.items.map((item, index) => (
                  <div
                    key={`${item.email || item.account_id || index}-${item.error || ''}`}
                    className={`rounded-md border px-3 py-2 text-xs ${item.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700'}`}
                  >
                    <div className="font-medium">{item.email || item.account_id || `任务 ${index + 1}`}</div>
                    <div className="mt-1 break-all">{item.ok ? item.account_id || '已写入账号库' : item.error}</div>
                    <div className="mt-1 text-[11px] opacity-80">代理 {item.proxy_label || 'direct'} · 尝试 {item.attempts || 1} 次</div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">运行日志</h3>
            <div className="mt-3 max-h-[52vh] overflow-y-auto rounded-md bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-100">
              {snapshot.logs.length === 0 ? (
                <div className="text-gray-400">暂无日志</div>
              ) : (
                snapshot.logs.map((log, index) => (
                  <div key={`${log.timestamp}-${index}`} className={log.level === 'error' ? 'text-rose-300' : log.level === 'warn' ? 'text-amber-200' : 'text-gray-100'}>
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
