import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getOAuthResumeJob,
  listAccounts,
  startOAuthResumeJob,
  type AccountLibraryItem,
  type OAuthResumeSnapshot,
} from '../api/extract';
import { BusyNotice } from '../components/BusyNotice';

type OAuthMode = 'resume' | 'email_otp' | 'phone_bind';
type BindSmsProvider = '' | 'user_phone_url' | 'bind_user_phone_url' | 'herosms' | 'smsbower' | 'sms_activate';
type EmailResourceProvider = 'icloud_api' | 'outlook_token' | 'icloud_privacy' | 'forwarded_domain' | 'cfworker_admin_api';

const resumeSample = JSON.stringify(
  {
    email: 'user@example.com',
    browser_storage_state_path: 'data/registered_accounts/storage_xxx.json',
    browser_storage_state_source: 'chatgpt_session_token',
    password: 'optional-only-if-login-page-appears',
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

const modeCards: Array<{
  id: OAuthMode;
  title: string;
  subtitle: string;
  help: string;
  action: string;
}> = [
  {
    id: 'resume',
    title: '基础 OAuth 续跑',
    subtitle: '恢复会话并更新 token',
    help: '适合账号已经有 browser storage state，只需要继续 OAuth 授权并把 access_token、refresh_token、id_token 回写账号库。',
    action: '开始基础续跑',
  },
  {
    id: 'email_otp',
    title: '邮箱 OTP / 邮箱绑定',
    subtitle: '处理邮箱验证码或 add_email',
    help: '适合 OAuth 流程进入邮箱验证码、绑定邮箱或需要接码邮箱时使用。可以手动粘贴接码行，也可以从资源池租用邮箱。',
    action: '开始邮箱续跑',
  },
  {
    id: 'phone_bind',
    title: '手机号绑定',
    subtitle: '处理 add_phone 和短信接码',
    help: '适合 OAuth 流程要求绑定手机号时使用。可以用绑定手机号资源池、自备接码 URL 或 HeroSMS、SMSBower、SMS-Activate。',
    action: '开始手机号绑定',
  },
];

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
  if (status === 'completed') return 'border-emerald-200 bg-emerald-50 text-emerald-800';
  if (status === 'failed') return 'border-rose-200 bg-rose-50 text-rose-800';
  if (status === 'running' || status === 'pending') return 'border-sky-200 bg-sky-50 text-sky-800';
  return 'border-gray-200 bg-gray-50 text-gray-700';
}

function emailProviderLabel(provider: EmailResourceProvider): string {
  const labels: Record<EmailResourceProvider, string> = {
    icloud_api: 'iCloud API',
    outlook_token: 'Outlook Token',
    icloud_privacy: 'iCloud Privacy',
    forwarded_domain: 'Forwarded Domain',
    cfworker_admin_api: 'CFWorker Mail',
  };
  return labels[provider];
}

export function OAuthResume() {
  const [activeMode, setActiveMode] = useState<OAuthMode>('resume');
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
  const activeCard = modeCards.find((card) => card.id === activeMode) ?? modeCards[0];
  const bindUsesResourcePool = bindSmsProvider === 'bind_user_phone_url';
  const showEmailPanel = activeMode === 'email_otp' || activeMode === 'phone_bind';
  const showPhonePanel = activeMode === 'phone_bind';
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
        setError(err instanceof Error ? err.message : '刷新 OAuth 任务失败');
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId, refreshJob, running]);

  useEffect(() => {
    if (snapshot?.status === 'completed') {
      void refreshAccounts().catch(() => undefined);
    }
  }, [refreshAccounts, snapshot?.status]);

  const appendAccountId = useCallback((id: number) => {
    setAccountIdsText((current) => {
      const ids = parseAccountIds(current);
      if (!ids.includes(id)) ids.push(id);
      return ids.join(', ');
    });
  }, []);

  const validateBeforeStart = useCallback((): string | null => {
    if (accountIds.length === 0 && !resumeJson.trim()) {
      return '请先选择账号库 ID，或粘贴 resume JSON。';
    }
    if (activeMode === 'email_otp' && !bindEmailUseResourcePool && !bindEmailText.trim()) {
      return '邮箱 OTP 模式需要填写邮箱接码数据，或启用邮箱资源池。';
    }
    if (activeMode === 'phone_bind' && !bindSmsProvider) {
      return '手机号绑定模式需要选择短信来源。';
    }
    if (activeMode === 'phone_bind' && bindSmsProvider === 'user_phone_url' && !bindSmsPhoneUrls.trim()) {
      return '自备手机号 URL 模式需要填写手机号接码数据。';
    }
    if (activeMode === 'phone_bind' && !bindUsesResourcePool && bindSmsProvider && bindSmsProvider !== 'user_phone_url' && !bindSmsApiKey.trim()) {
      return '短信服务商模式需要填写 API Key。';
    }
    return null;
  }, [
    accountIds.length,
    activeMode,
    bindEmailText,
    bindEmailUseResourcePool,
    bindSmsApiKey,
    bindSmsPhoneUrls,
    bindSmsProvider,
    bindUsesResourcePool,
    resumeJson,
  ]);

  const handleStart = useCallback(async () => {
    const validationError = validateBeforeStart();
    if (validationError) {
      setError(validationError);
      return;
    }
    const includeEmail = activeMode === 'email_otp' || (activeMode === 'phone_bind' && (bindEmailUseResourcePool || Boolean(bindEmailText.trim())));
    const includePhone = activeMode === 'phone_bind';
    setError(null);
    setSnapshot(null);
    try {
      const created = await startOAuthResumeJob({
        oauth_mode: activeMode,
        account_ids: accountIds,
        resume_json: resumeJson,
        bind_email_text: includeEmail ? bindEmailText : '',
        mailbox_proxy: includeEmail ? mailboxProxy : '',
        bind_email_use_resource_pool: includeEmail ? bindEmailUseResourcePool : false,
        bind_email_resource_provider: bindEmailResourceProvider,
        bind_sms_provider: includePhone ? bindSmsProvider : '',
        bind_use_resource_pool: includePhone ? bindUsesResourcePool : false,
        bind_resource_provider: 'bind_user_phone_url',
        bind_sms_phone_urls: includePhone ? bindSmsPhoneUrls : '',
        bind_sms_proxy: includePhone ? bindSmsProxy : '',
        bind_sms_api_key: includePhone ? bindSmsApiKey : '',
        bind_sms_service: includePhone ? bindSmsService : '',
        bind_sms_country: includePhone ? bindSmsCountry : '',
        bind_herosms_api_key: includePhone && bindSmsProvider === 'herosms' ? bindSmsApiKey : '',
        bind_herosms_service: includePhone && bindSmsProvider === 'herosms' ? bindSmsService : '',
        bind_herosms_country: includePhone && bindSmsProvider === 'herosms' ? bindSmsCountry : '',
        bind_herosms_max_price: includePhone && bindSmsProvider === 'herosms' && bindSmsMaxPrice.trim() ? Number(bindSmsMaxPrice) : null,
        bind_smsbower_api_key: includePhone && bindSmsProvider === 'smsbower' ? bindSmsApiKey : '',
        bind_smsbower_service: includePhone && bindSmsProvider === 'smsbower' ? bindSmsService : '',
        bind_smsbower_country: includePhone && bindSmsProvider === 'smsbower' ? bindSmsCountry : '',
        bind_smsbower_max_price: includePhone && bindSmsProvider === 'smsbower' && bindSmsMaxPrice.trim() ? Number(bindSmsMaxPrice) : null,
        bind_smsbower_min_price: includePhone && bindSmsProvider === 'smsbower' && bindSmsMinPrice.trim() ? Number(bindSmsMinPrice) : null,
        bind_smsbower_provider_ids: includePhone && bindSmsProvider === 'smsbower' ? bindSmsProviderIds : '',
        bind_sms_activate_api_key: includePhone && bindSmsProvider === 'sms_activate' ? bindSmsApiKey : '',
        bind_sms_activate_country: includePhone && bindSmsProvider === 'sms_activate' ? bindSmsCountry : '',
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
      setError(err instanceof Error ? err.message : '启动 OAuth 任务失败');
    }
  }, [
    accountIds,
    activeMode,
    allowPageFallback,
    bindEmailResourceProvider,
    bindEmailText,
    bindEmailUseResourcePool,
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
    validateBeforeStart,
  ]);

  return (
    <div className="space-y-5">
      <BusyNotice
        active={running}
        label={`${activeCard.title}运行中`}
        detail="后端正在恢复浏览器登录态、执行 OAuth 流程，并按结果回写账号库。"
      />

      <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 bg-gray-950 px-5 py-4 text-white">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">OAuth 工具台</h2>
              <p className="mt-1 max-w-4xl text-sm text-gray-300">
                这里已经拆成三个独立入口。先选择要处理的问题，再填写对应资源；基础续跑不会再要求短信或邮箱配置。
              </p>
            </div>
            {snapshot && (
              <div className={`rounded-md border px-3 py-2 text-xs ${statusTone(snapshot.status)}`}>
                <div className="font-semibold">状态：{snapshot.status}</div>
                <div className="mt-0.5">进度：{progress}</div>
              </div>
            )}
          </div>
        </div>

        <div className="border-b border-gray-200 bg-gray-50 p-4">
          <div className="grid gap-3 lg:grid-cols-3">
            {modeCards.map((card) => {
              const active = card.id === activeMode;
              return (
                <button
                  key={card.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setActiveMode(card.id)}
                  disabled={running}
                  className={`min-h-32 rounded-lg border px-4 py-3 text-left transition ${
                    active
                      ? 'border-gray-950 bg-white shadow-sm ring-2 ring-gray-950'
                      : 'border-gray-200 bg-white hover:border-gray-400'
                  } disabled:cursor-not-allowed disabled:opacity-60`}
                >
                  <span className="block text-sm font-semibold text-gray-950">{card.title}</span>
                  <span className="mt-1 block text-xs font-medium text-gray-500">{card.subtitle}</span>
                  <span className="mt-3 block text-xs leading-5 text-gray-600">{card.help}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid gap-4 p-4 2xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)_380px]">
          <div className="space-y-4">
            <section className="rounded-lg border border-gray-200 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">账号来源</h3>
                  <p className="mt-1 text-xs text-gray-500">
                    推荐从账号库选择带 session_json 的账号；也可以直接粘贴 resume JSON。启动前会先校验 AT 是否有效。
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setAccountIdsText('')}
                    disabled={running || !accountIdsText}
                    className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                  >
                    清空选择
                  </button>
                  <button
                    type="button"
                    onClick={() => void refreshAccounts()}
                    disabled={running}
                    className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                  >
                    刷新账号
                  </button>
                </div>
              </div>

              <input
                value={accountIdsText}
                onChange={(event) => setAccountIdsText(event.target.value)}
                placeholder="账号库 ID，例如：12, 15, 18"
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />

              <div className="mt-3 max-h-48 overflow-y-auto rounded-md border border-gray-100 bg-gray-50 p-2">
                {accounts.length === 0 ? (
                  <div className="px-2 py-4 text-center text-xs text-gray-500">暂无带 session_json 的账号</div>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-2">
                    {accounts.slice(0, 60).map((account) => (
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
            </section>

            <section className="rounded-lg border border-gray-200 p-4">
              <div className="mb-3">
                <h3 className="text-sm font-semibold text-gray-900">直接粘贴 resume JSON</h3>
                <p className="mt-1 text-xs text-gray-500">
                  适合临时运行未导入账号库的数据。需要包含 email 或 phone_number，并提供 browser_storage_state_path。
                </p>
              </div>
              <textarea
                value={resumeJson}
                onChange={(event) => setResumeJson(event.target.value)}
                rows={11}
                placeholder={resumeSample}
                disabled={running}
                className="max-h-[42vh] w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </section>
          </div>

          <div className="space-y-4">
            <section className="rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-semibold text-gray-900">当前功能：{activeCard.title}</h3>
              <p className="mt-2 text-xs leading-5 text-gray-600">{activeCard.help}</p>
              <div className="mt-4 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
                {activeMode === 'resume' && '只执行 OAuth 续跑与 token 回写，不提交邮箱接码和短信接码配置。'}
                {activeMode === 'email_otp' && '遇到邮箱验证码或绑定邮箱时，会使用下方接码配置获取验证码。'}
                {activeMode === 'phone_bind' && '遇到 add_phone 时，会使用下方短信配置完成手机号绑定。'}
              </div>
            </section>

            {showEmailPanel && (
              <section className="rounded-lg border border-gray-200 p-4">
                <div className="mb-3">
                  <h3 className="text-sm font-semibold text-gray-900">
                    {activeMode === 'phone_bind' ? '邮箱 OTP 备用' : '邮箱 OTP / 绑定邮箱'}
                  </h3>
                  <p className="mt-1 text-xs text-gray-500">
                    {activeMode === 'phone_bind'
                      ? '手机号绑定前如果又触发邮箱 OTP，可以启用这里的邮箱接码；不需要可留空。'
                      : '可以粘贴接码数据，也可以从资源池租用邮箱。'}
                  </p>
                </div>
                <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
                  <input
                    type="checkbox"
                    checked={bindEmailUseResourcePool}
                    onChange={(event) => setBindEmailUseResourcePool(event.target.checked)}
                    disabled={running}
                  />
                  使用邮箱资源池
                </label>
                {bindEmailUseResourcePool && (
                  <select
                    value={bindEmailResourceProvider}
                    onChange={(event) => setBindEmailResourceProvider(event.target.value as EmailResourceProvider)}
                    disabled={running}
                    className="mt-3 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
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
                  className="mt-3 max-h-56 w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                />
                <div className="mt-2 text-xs text-gray-500">
                  当前资源池类型：{emailProviderLabel(bindEmailResourceProvider)}。接码代理在右侧运行参数里填写。
                </div>
              </section>
            )}

            {showPhonePanel && (
              <section className="rounded-lg border border-gray-200 p-4">
                <div className="mb-3">
                  <h3 className="text-sm font-semibold text-gray-900">手机号绑定</h3>
                  <p className="mt-1 text-xs text-gray-500">只有 OAuth 流程遇到 add_phone 时才会租号或读取接码 URL。</p>
                </div>
                <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-1">
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">短信来源</span>
                    <select
                      value={bindSmsProvider}
                      onChange={(event) => setBindSmsProvider(event.target.value as BindSmsProvider)}
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
                    >
                      <option value="">请选择短信来源</option>
                      <option value="user_phone_url">自备手机号 URL</option>
                      <option value="bind_user_phone_url">绑定手机号池</option>
                      <option value="herosms">HeroSMS</option>
                      <option value="smsbower">SMSBower</option>
                      <option value="sms_activate">SMS-Activate</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">接码代理</span>
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
                    将从“资源池 / 绑定手机号池”租用号码，并在成功、冷却、禁用状态间自动回写。
                  </div>
                )}

                {bindSmsProvider === 'user_phone_url' && (
                  <label className="mt-3 block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">手机号接码数据</span>
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
                  <div className="mt-3 grid gap-3 md:grid-cols-2 2xl:grid-cols-1">
                    <label className="block md:col-span-2 2xl:col-span-1">
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
                        <label className="block md:col-span-2 2xl:col-span-1">
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
              </section>
            )}
          </div>

          <aside className="space-y-4">
            <section className="rounded-lg border border-gray-200 bg-gray-50 p-4">
              <h3 className="text-sm font-semibold text-gray-900">运行与代理</h3>
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

                {showEmailPanel && (
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">接码代理</span>
                    <input
                      value={mailboxProxy}
                      onChange={(event) => setMailboxProxy(event.target.value)}
                      placeholder="可选，仅用于请求邮箱接码 URL"
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                    />
                  </label>
                )}

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

                {showEmailPanel && (
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
                )}

                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">覆盖密码</span>
                  <input
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="可选；只有续跑跳到密码页时才需要"
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
                  {running ? '任务运行中...' : activeCard.action}
                </button>
              </div>
            </section>

            {snapshot && (
              <section className={`rounded-lg border p-4 ${statusTone(snapshot.status)}`}>
                <div className="text-sm font-semibold">任务状态：{snapshot.status}</div>
                <div className="mt-2 break-all text-xs">任务 ID：<span className="font-mono">{snapshot.job_id}</span></div>
                <div className="mt-1 text-xs">进度：{progress}</div>
                {snapshot.error && <div className="mt-2 break-all text-xs">{snapshot.error}</div>}
              </section>
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
