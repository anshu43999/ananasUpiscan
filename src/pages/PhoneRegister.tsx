import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getPhoneRegistrationJob,
  startPhoneRegistrationJob,
  type PhoneRegistrationSnapshot,
} from '../api/extract';
import { BusyNotice } from '../components/BusyNotice';

type SmsProvider = 'user_phone_url' | 'resource_pool' | 'herosms' | 'smsbower' | 'sms_activate';

const sample = [
  '+15551234567|https://sms.example.com/latest?phone=15551234567',
  '+15557654321----https://sms.example.com/latest?phone=15557654321',
].join('\n');

const providerOptions: Array<{ id: SmsProvider; label: string; hint: string }> = [
  { id: 'user_phone_url', label: '自备手机号 URL', hint: '每行一个手机号和短信轮询 URL' },
  { id: 'resource_pool', label: '注册手机号池', hint: '从资源池租用号码并自动回写状态' },
  { id: 'herosms', label: 'HeroSMS', hint: '使用 HeroSMS/SMS-Activate 兼容接口租号' },
  { id: 'smsbower', label: 'SMSBower', hint: '需要配置 maxPrice，支持 providerIds' },
  { id: 'sms_activate', label: 'SMS-Activate', hint: '使用 sms-activate.guru API' },
];

function isRunning(status?: string): boolean {
  return status === 'pending' || status === 'running';
}

export function PhoneRegister() {
  const [smsProvider, setSmsProvider] = useState<SmsProvider>('user_phone_url');
  const [phoneText, setPhoneText] = useState('');
  const [providerCount, setProviderCount] = useState(1);
  const [smsApiKey, setSmsApiKey] = useState('');
  const [smsService, setSmsService] = useState('dr');
  const [smsCountry, setSmsCountry] = useState('');
  const [smsMaxPrice, setSmsMaxPrice] = useState('');
  const [smsMinPrice, setSmsMinPrice] = useState('');
  const [smsProviderIds, setSmsProviderIds] = useState('');
  const [reusePhone, setReusePhone] = useState(true);
  const [phoneSuccessMax, setPhoneSuccessMax] = useState(3);
  const [registrationProxies, setRegistrationProxies] = useState('');
  const [useProxyResourcePool, setUseProxyResourcePool] = useState(false);
  const [proxySeedRegion, setProxySeedRegion] = useState('JP');
  const [proxySeedTtl, setProxySeedTtl] = useState(10);
  const [proxySeedProtocol, setProxySeedProtocol] = useState<'socks5' | 'http' | 'https'>('socks5');
  const [proxyResourceCount, setProxyResourceCount] = useState(0);
  const [smsProxy, setSmsProxy] = useState('');
  const [countryCode, setCountryCode] = useState('1');
  const [countryName, setCountryName] = useState('United States');
  const [retryAttempts, setRetryAttempts] = useState(2);
  const [concurrency, setConcurrency] = useState(1);
  const [headed, setHeaded] = useState(false);
  const [password, setPassword] = useState('');
  const [smsTimeout, setSmsTimeout] = useState(180);
  const [pollInterval, setPollInterval] = useState(3);
  const [jobId, setJobId] = useState('');
  const [snapshot, setSnapshot] = useState<PhoneRegistrationSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const running = isRunning(snapshot?.status);
  const usesResourcePool = smsProvider === 'resource_pool';
  const usesProvider = smsProvider !== 'user_phone_url';
  const progress = useMemo(() => {
    if (!snapshot || snapshot.total <= 0) return '未开始';
    return `${snapshot.completed}/${snapshot.total}，成功 ${snapshot.success}，失败 ${snapshot.failed}`;
  }, [snapshot]);

  const refreshJob = useCallback(async (id: string) => {
    const next = await getPhoneRegistrationJob(id);
    setSnapshot(next);
    return next;
  }, []);

  useEffect(() => {
    if (!jobId || !running) return undefined;
    const timer = window.setInterval(() => {
      void refreshJob(jobId).catch((err) => {
        setError(err instanceof Error ? err.message : '刷新手机注册任务失败');
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId, refreshJob, running]);

  const handleStart = useCallback(async () => {
    if (!usesProvider && !phoneText.trim()) {
      setError('请先填写手机号接码数据。');
      return;
    }
    if (usesProvider && !usesResourcePool && !smsApiKey.trim()) {
      setError('请先填写短信服务商 API Key。');
      return;
    }
    if (smsProvider === 'smsbower' && !smsMaxPrice.trim()) {
      setError('SMSBower 需要填写 maxPrice。');
      return;
    }
    setError(null);
    setSnapshot(null);
    try {
      const numericMaxPrice = smsMaxPrice.trim() ? Number(smsMaxPrice) : null;
      const numericMinPrice = smsMinPrice.trim() ? Number(smsMinPrice) : null;
      const created = await startPhoneRegistrationJob({
        phone_text: phoneText,
        sms_provider: smsProvider,
        use_resource_pool: usesResourcePool,
        resource_provider: 'user_phone_url',
        provider_count: providerCount,
        sms_proxy: smsProxy,
        sms_api_key: smsApiKey,
        sms_service: smsService,
        sms_country: smsCountry,
        sms_activate_api_key: smsProvider === 'sms_activate' ? smsApiKey : '',
        sms_activate_country: smsProvider === 'sms_activate' ? smsCountry : '',
        herosms_api_key: smsProvider === 'herosms' ? smsApiKey : '',
        herosms_service: smsProvider === 'herosms' ? smsService : '',
        herosms_country: smsProvider === 'herosms' ? smsCountry : '',
        herosms_max_price: smsProvider === 'herosms' ? numericMaxPrice : null,
        register_reuse_phone_to_max: reusePhone,
        register_phone_success_max: phoneSuccessMax,
        smsbower_api_key: smsProvider === 'smsbower' ? smsApiKey : '',
        smsbower_service: smsProvider === 'smsbower' ? smsService : '',
        smsbower_country: smsProvider === 'smsbower' ? smsCountry : '',
        smsbower_max_price: smsProvider === 'smsbower' ? numericMaxPrice : null,
        smsbower_min_price: smsProvider === 'smsbower' ? numericMinPrice : null,
        smsbower_provider_ids: smsProviderIds,
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
        country_code: countryCode,
        country_name: countryName,
        sms_timeout: smsTimeout,
        sms_poll_interval: pollInterval,
      });
      setJobId(created.job_id);
      await refreshJob(created.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '启动手机注册任务失败');
    }
  }, [
    concurrency,
    countryCode,
    countryName,
    headed,
    password,
    phoneSuccessMax,
    phoneText,
    pollInterval,
    providerCount,
    refreshJob,
    registrationProxies,
    retryAttempts,
    reusePhone,
    smsApiKey,
    smsCountry,
    smsMaxPrice,
    smsMinPrice,
    smsProvider,
    smsProviderIds,
    smsProxy,
    smsService,
    smsTimeout,
    usesProvider,
    usesResourcePool,
    proxyResourceCount,
    proxySeedProtocol,
    proxySeedRegion,
    proxySeedTtl,
    useProxyResourcePool,
  ]);

  return (
    <div className="space-y-5">
      <BusyNotice
        active={running}
        label="手机注册任务运行中"
        detail="后端正在执行浏览器注册、租号/轮询短信验证码，成功账号会自动写入账号库。"
      />

      <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">手机注册</h2>
            <p className="mt-1 text-sm text-gray-500">
              支持自备手机号接码 URL，也支持 HeroSMS、SMSBower、SMS-Activate 服务商租号。
            </p>
          </div>
          <div className="rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-800">
            服务商 API Key 只随本次任务提交到本地后端，不会写入账号库。
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_420px]">
        <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div>
            <span className="mb-2 block text-sm font-medium text-gray-700">短信来源</span>
            <div className="grid gap-2 sm:grid-cols-2">
              {providerOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setSmsProvider(option.id)}
                  disabled={running}
                  className={`rounded-lg border px-3 py-2 text-left ${
                    smsProvider === option.id
                      ? 'border-gray-900 bg-gray-900 text-white'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <span className="block text-sm font-semibold">{option.label}</span>
                  <span className={`mt-1 block text-xs ${smsProvider === option.id ? 'text-gray-300' : 'text-gray-500'}`}>{option.hint}</span>
                </button>
              ))}
            </div>
          </div>

          {!usesProvider ? (
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">手机号接码数据</span>
              <textarea
                value={phoneText}
                onChange={(event) => setPhoneText(event.target.value)}
                rows={14}
                placeholder={sample}
                disabled={running}
                className="max-h-[58vh] w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
          ) : usesResourcePool ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
              <h3 className="text-sm font-semibold text-emerald-900">从资源池取号</h3>
              <p className="mt-1 text-sm text-emerald-700">
                当前任务会从“资源池 / 注册手机号池”租用号码。成功后标记为已使用，失败会按错误进入冷却或禁用。
              </p>
              <label className="mt-4 block max-w-xs">
                <span className="mb-1.5 block text-sm font-medium text-emerald-900">租用数量</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={providerCount}
                  onChange={(event) => setProviderCount(Number(event.target.value) || 1)}
                  disabled={running}
                  className="w-full rounded-md border border-emerald-200 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </label>
            </div>
          ) : (
            <div className="grid gap-4 rounded-lg border border-gray-100 bg-gray-50 p-4 lg:grid-cols-2">
              <label className="block lg:col-span-2">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">短信服务商 API Key</span>
                <input
                  value={smsApiKey}
                  onChange={(event) => setSmsApiKey(event.target.value)}
                  placeholder="服务商 API Key"
                  disabled={running}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">租号数量</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={providerCount}
                  onChange={(event) => setProviderCount(Number(event.target.value) || 1)}
                  disabled={running}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">服务编号</span>
                <input
                  value={smsService}
                  onChange={(event) => setSmsService(event.target.value)}
                  placeholder="OpenAI/ChatGPT 默认 dr"
                  disabled={running}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">国家编号</span>
                <input
                  value={smsCountry}
                  onChange={(event) => setSmsCountry(event.target.value)}
                  placeholder="例如 187 / 52 / 73"
                  disabled={running}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">maxPrice</span>
                <input
                  value={smsMaxPrice}
                  onChange={(event) => setSmsMaxPrice(event.target.value)}
                  placeholder={smsProvider === 'smsbower' ? '必填' : '可选'}
                  disabled={running}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                />
              </label>
              {smsProvider === 'smsbower' && (
                <>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">minPrice</span>
                    <input
                      value={smsMinPrice}
                      onChange={(event) => setSmsMinPrice(event.target.value)}
                      placeholder="可选"
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">providerIds</span>
                    <input
                      value={smsProviderIds}
                      onChange={(event) => setSmsProviderIds(event.target.value)}
                      placeholder="逗号分隔，可选"
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                    />
                  </label>
                </>
              )}
              {smsProvider === 'herosms' && (
                <div className="grid gap-2 lg:col-span-2">
                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    <input type="checkbox" checked={reusePhone} onChange={(event) => setReusePhone(event.target.checked)} disabled={running} />
                    允许复用号码直到达到成功次数
                  </label>
                  <label className="block max-w-xs">
                    <span className="mb-1.5 block text-sm font-medium text-gray-700">号码成功次数上限</span>
                    <input
                      type="number"
                      min={0}
                      max={20}
                      value={phoneSuccessMax}
                      onChange={(event) => setPhoneSuccessMax(Number(event.target.value) || 0)}
                      disabled={running}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                    />
                  </label>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-gray-700">注册 IP 池</span>
            <textarea
              value={registrationProxies}
              onChange={(event) => setRegistrationProxies(event.target.value)}
              rows={5}
              placeholder="一行一个代理，失败后自动切换下一条"
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
              value={smsProxy}
              onChange={(event) => setSmsProxy(event.target.value)}
              placeholder="可选，仅用于请求接码/服务商 API"
              disabled={running}
              className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">国家拨号码</span>
              <input
                value={countryCode}
                onChange={(event) => setCountryCode(event.target.value)}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">国家名称</span>
              <input
                value={countryName}
                onChange={(event) => setCountryName(event.target.value)}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
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
              <span className="mb-1.5 block text-sm font-medium text-gray-700">短信超时</span>
              <input
                type="number"
                min={30}
                max={1200}
                value={smsTimeout}
                onChange={(event) => setSmsTimeout(Number(event.target.value) || 180)}
                disabled={running}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
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
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-3">
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
            <label className="flex items-end gap-2 pb-2 text-sm text-gray-700">
              <input type="checkbox" checked={headed} onChange={(event) => setHeaded(event.target.checked)} disabled={running} />
              显示浏览器
            </label>
          </div>

          <button
            type="button"
            onClick={() => void handleStart()}
            disabled={running || (!usesProvider && !phoneText.trim()) || (usesProvider && !usesResourcePool && !smsApiKey.trim())}
            className="w-full rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-gray-800 disabled:bg-gray-300"
          >
            {running ? '注册中...' : '开始手机注册'}
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
                <div key={`${item.phone}-${item.account_id || item.error || ''}`} className={`rounded-md border px-3 py-2 text-xs ${item.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700'}`}>
                  <div className="font-medium">{item.phone}</div>
                  <div className="mt-1 break-all">{item.ok ? item.account_id || item.email || '已写入账号库' : item.error}</div>
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
