import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';
import QRCode from 'qrcode';
import { useExtractJobs } from '../hooks/useExtractJob';
import {
  testProxyChain,
  type ExtractJobResponse,
  type ProxyChainTestResult,
  type StartExtractOptions,
} from '../api/extract';

const STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  running: '提取中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'border-amber-200 bg-amber-50 text-amber-700',
  running: 'border-sky-200 bg-sky-50 text-sky-700',
  completed: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  failed: 'border-rose-200 bg-rose-50 text-rose-700',
  cancelled: 'border-gray-200 bg-gray-50 text-gray-600',
};

const COUNTRY_OPTIONS = ['IN', 'VN', 'US', 'NL', 'JP', 'KR', 'BR', 'DE', 'FR', 'GB'];
const PROXY_STAGES = ['checkout', 'promotion', 'provider', 'approve'] as const;
const CUSTOM_PROXY_STAGES = ['checkout', 'promotion'] as const;
const PROXY_STAGE_LABELS: Record<(typeof PROXY_STAGES)[number], string> = {
  checkout: '下单',
  promotion: '优惠',
  provider: '支付',
  approve: '确认',
};
const STORAGE_KEY_PROXY = 'upiscan_extract_proxy';

type ProxyStage = (typeof PROXY_STAGES)[number];
type PaymentMethod = 'upi' | 'ideal' | 'momo' | 'kakao';
type ProxySourceMode = 'builtin' | 'custom';
type AudioContextRef = MutableRefObject<AudioContext | null>;

interface SavedProxyState {
  paymentMethod?: PaymentMethod;
  proxySourceMode?: ProxySourceMode;
  proxyChainMode: string;
  manualRegions: Record<ProxyStage, string>;
  customExportProxy: string;
  customProxyText?: string;
  customProxyTexts?: Partial<Record<ProxyStage, string>>;
}

interface PaymentMethodOption {
  value: PaymentMethod;
  label: string;
  route: string;
}

const PAYMENT_METHODS: PaymentMethodOption[] = [
  { value: 'upi', label: 'UPI', route: 'JP / IN' },
  { value: 'ideal', label: 'iDEAL', route: 'JP / NL' },
  { value: 'momo', label: 'MoMo', route: 'VN / VND' },
  { value: 'kakao', label: 'Kakao', route: 'KR / VN / KR' },
];

function loadProxyState(): SavedProxyState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PROXY);
    return raw ? (JSON.parse(raw) as SavedProxyState) : null;
  } catch {
    return null;
  }
}

function buildProxyChain(
  mode: string,
  manualRegions: Record<ProxyStage, string>,
  paymentMethod: PaymentMethod,
): Record<string, string> | undefined {
  if (mode === 'default') return undefined;
  if (mode === 'india') return { checkout: 'JP', promotion: 'IN', provider: 'IN', approve: 'IN' };
  if (mode === 'ideal') return { checkout: 'JP', promotion: 'NL', provider: 'NL', approve: 'NL' };
  if (mode === 'momo') return { checkout: 'VN', promotion: 'VN', provider: 'VN', approve: 'VN' };
  if (mode === 'kakao') return { checkout: 'KR', promotion: 'VN', provider: 'KR', approve: 'KR' };
  if (mode === 'manual') return { ...manualRegions };
  if (paymentMethod === 'ideal') return { checkout: 'JP', promotion: 'NL', provider: 'NL', approve: 'NL' };
  if (paymentMethod === 'momo') return { checkout: 'VN', promotion: 'VN', provider: 'VN', approve: 'VN' };
  if (paymentMethod === 'kakao') return { checkout: 'KR', promotion: 'VN', provider: 'KR', approve: 'KR' };
  return undefined;
}

function configFromProxyChain(
  chain: Record<string, string> | undefined,
  customExportProxy: string,
  paymentMethod: PaymentMethod,
): Record<string, unknown> | undefined {
  const config: Record<string, unknown> = {};
  if (paymentMethod === 'ideal') {
    config.checkout_country = 'NL';
    config.billing_country = 'NL';
    config.provider_country = chain?.provider || 'NL';
    config.provider_country_label = chain?.provider || 'NL';
    config.browser_locale = 'nl-NL';
    config.elements_locale = 'nl';
    config.browser_timezone = 'Europe/Amsterdam';
  }
  if (paymentMethod === 'momo') {
    config.checkout_country = 'VN';
    config.billing_country = 'VN';
    config.provider_country = chain?.provider || 'VN';
    config.provider_country_label = chain?.provider || 'VN';
    config.browser_locale = 'vi-VN';
    config.elements_locale = 'vi';
    config.browser_timezone = 'Asia/Ho_Chi_Minh';
    config.promo_mode = 'off';
  }
  if (paymentMethod === 'kakao') {
    config.checkout_country = chain?.checkout || 'KR';
    config.billing_country = chain?.provider || 'KR';
    config.provider_country = chain?.provider || 'KR';
    config.provider_country_label = chain?.provider || 'KR';
    config.promotion_countries = [chain?.promotion || 'VN'];
    config.kakao_promotion_country = chain?.promotion || 'VN';
    config.browser_locale = 'ko-KR';
    config.elements_locale = 'ko';
    config.browser_timezone = 'Asia/Seoul';
  }
  if (chain?.checkout) config.bootstrap_country = chain.checkout;
  if (chain?.promotion) config.promotion_countries = [chain.promotion];
  if (chain?.provider) {
    config.provider_country = chain.provider;
    if (paymentMethod === 'upi') config.billing_country = chain.provider;
  }
  if (customExportProxy.trim()) config.pre_proxy = customExportProxy.trim();
  return Object.keys(config).length ? config : undefined;
}

function parseProxySeeds(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));
}

function buildProxySeedChains(
  proxyTexts: Record<ProxyStage, string>,
  paymentMethod: PaymentMethod,
): Array<Record<string, string>> {
  const checkout = parseProxySeeds(proxyTexts.checkout);
  const promotion = parseProxySeeds(proxyTexts.promotion);
  if (!checkout.length || !promotion.length) return [];

  const total = Math.max(checkout.length, promotion.length);
  return Array.from({ length: total }, (_, index) => {
    const firstProxy = checkout[index % checkout.length];
    const secondProxy = promotion[index % promotion.length];
    if (paymentMethod === 'kakao') {
      return { checkout: firstProxy, promotion: secondProxy, provider: firstProxy };
    }
    return { checkout: firstProxy, promotion: secondProxy, provider: secondProxy };
  });
}

function customProxyStageLabel(paymentMethod: PaymentMethod, stage: (typeof CUSTOM_PROXY_STAGES)[number]): string {
  if (paymentMethod === 'ideal') return stage === 'checkout' ? 'JP 代理' : 'NL 代理';
  if (paymentMethod === 'momo') return stage === 'checkout' ? 'VN checkout 代理' : 'VN init 代理';
  if (paymentMethod === 'kakao') return stage === 'checkout' ? 'KR 代理' : 'VN 代理';
  return stage === 'checkout' ? 'JP 代理' : 'IN 代理';
}

function customProxyEmptyText(paymentMethod: PaymentMethod): string {
  if (paymentMethod === 'ideal') return '需要 JP 与 NL 两段代理';
  if (paymentMethod === 'momo') return '需要两段 VN 代理';
  if (paymentMethod === 'kakao') return '需要 KR 与 VN 两段代理';
  return '需要 JP 与 IN 两段代理';
}

function defaultManualRegions(paymentMethod: PaymentMethod): Record<ProxyStage, string> {
  if (paymentMethod === 'ideal') return { checkout: 'JP', promotion: 'NL', provider: 'NL', approve: 'NL' };
  if (paymentMethod === 'momo') return { checkout: 'VN', promotion: 'VN', provider: 'VN', approve: 'VN' };
  if (paymentMethod === 'kakao') return { checkout: 'KR', promotion: 'VN', provider: 'KR', approve: 'KR' };
  return { checkout: 'JP', promotion: 'IN', provider: 'IN', approve: 'IN' };
}

function defaultBillingCountry(paymentMethod: PaymentMethod): string {
  if (paymentMethod === 'ideal') return 'NL';
  if (paymentMethod === 'momo') return 'VN';
  if (paymentMethod === 'kakao') return 'KR';
  return 'IN';
}

function routeText(paymentMethod: PaymentMethod): string {
  if (paymentMethod === 'ideal') return 'JP checkout / NL iDEAL';
  if (paymentMethod === 'momo') return 'VN checkout / VN Stripe init';
  if (paymentMethod === 'kakao') return 'KR checkout / VN update / KR Kakao';
  return 'JP checkout / IN UPI';
}

function getNotificationAudioContext(ref: AudioContextRef): AudioContext | null {
  if (typeof window === 'undefined') return null;
  if (ref.current) return ref.current;

  const AudioContextCtor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return null;

  ref.current = new AudioContextCtor();
  return ref.current;
}

function primeResultSound(ref: AudioContextRef): void {
  const ctx = getNotificationAudioContext(ref);
  if (!ctx || ctx.state !== 'suspended') return;
  void ctx.resume().catch(() => undefined);
}

function scheduleResultTone(
  ctx: AudioContext,
  startAt: number,
  frequency: number,
  duration: number,
  volume: number,
): void {
  const oscillator = ctx.createOscillator();
  const gain = ctx.createGain();
  oscillator.type = 'sine';
  oscillator.frequency.setValueAtTime(frequency, startAt);
  gain.gain.setValueAtTime(0.0001, startAt);
  gain.gain.exponentialRampToValueAtTime(volume, startAt + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
  oscillator.connect(gain);
  gain.connect(ctx.destination);
  oscillator.start(startAt);
  oscillator.stop(startAt + duration + 0.03);
}

function playResultSound(ref: AudioContextRef): void {
  const ctx = getNotificationAudioContext(ref);
  if (!ctx) return;

  const play = () => {
    const startAt = ctx.currentTime + 0.02;
    scheduleResultTone(ctx, startAt, 880, 0.15, 0.08);
    scheduleResultTone(ctx, startAt + 0.17, 1174.66, 0.2, 0.07);
  };

  if (ctx.state === 'suspended') {
    void ctx.resume().then(play).catch(() => undefined);
    return;
  }
  play();
}

interface ExtractJobCardProps {
  job: ExtractJobResponse;
  onCancel: (jobId: string) => void;
  onRemove: (jobId: string) => void;
}

function ExtractJobCard({ job, onCancel, onRemove }: ExtractJobCardProps) {
  const [logsExpanded, setLogsExpanded] = useState(true);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!job.result?.url) {
      setQrDataUrl(null);
      return;
    }
    let active = true;
    QRCode.toDataURL(job.result.url, { width: 220, margin: 1 })
      .then((value) => {
        if (active) setQrDataUrl(value);
      })
      .catch(() => {
        if (active) setQrDataUrl(null);
      });
    return () => {
      active = false;
    };
  }, [job.result?.url]);

  const handleCopyUrl = useCallback(async () => {
    if (!job.result?.url) return;
    await navigator.clipboard.writeText(job.result.url);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }, [job.result?.url]);

  const canCancel = job.status === 'pending' || job.status === 'running';
  const canRemove = job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled';

  return (
    <article className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-900">任务结果</h3>
            <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${STATUS_COLORS[job.status] || STATUS_COLORS.pending}`}>
              {STATUS_LABELS[job.status] || job.status}
            </span>
          </div>
          <p className="mt-1 truncate font-mono text-xs text-gray-400">{job.job_id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canCancel && (
            <button
              type="button"
              onClick={() => onCancel(job.job_id)}
              className="rounded-md border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
            >
              取消
            </button>
          )}
          {canRemove && (
            <button
              type="button"
              onClick={() => onRemove(job.job_id)}
              className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-500 hover:bg-gray-50"
            >
              移除
            </button>
          )}
        </div>
      </div>

      {(job.status === 'pending' || job.status === 'running') && (
        <div className="mt-4 flex items-center gap-3 rounded-md bg-sky-50 px-3 py-2 text-sm text-sky-700">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
          正在提炼支付链接...
        </div>
      )}

      {job.status === 'completed' && job.result?.url && (
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
          <div className="min-w-0">
            <label className="mb-1.5 block text-xs font-medium text-gray-500">支付链接</label>
            <div className="flex gap-2">
              <code className="min-w-0 flex-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-700">
                {job.result.url}
              </code>
              <button
                type="button"
                onClick={handleCopyUrl}
                className="h-10 shrink-0 rounded-md border border-emerald-200 px-3 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
              >
                {copied ? '已复制' : '复制'}
              </button>
            </div>
          </div>
          {qrDataUrl && (
            <div className="flex justify-center rounded-md border border-gray-200 bg-white p-2">
              <img src={qrDataUrl} alt="支付二维码" className="h-48 w-48" />
            </div>
          )}
        </div>
      )}

      {job.status === 'failed' && (
        <div className="mt-4 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {job.error || '提取失败'}
        </div>
      )}

      {job.logs.length > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setLogsExpanded((value) => !value)}
            className="text-xs font-medium text-gray-500 hover:text-gray-700"
          >
            {logsExpanded ? '隐藏日志' : '显示日志'} ({job.logs.length})
          </button>
          {logsExpanded && (
            <div className="mt-2 max-h-80 space-y-1 overflow-y-auto rounded-md bg-gray-950 p-3">
              {job.logs.map((log, index) => (
                <div
                  key={`${log.timestamp}-${index}`}
                  className={`font-mono text-xs ${
                    log.level === 'error'
                      ? 'text-rose-300'
                      : log.level === 'warn'
                        ? 'text-amber-300'
                        : 'text-gray-300'
                  }`}
                >
                  <span className="text-gray-500">[{new Date(log.timestamp).toLocaleTimeString()}]</span>{' '}
                  {log.message}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export function LinkExtract() {
  const { jobs, loading, error, activeCount, submit, cancel, remove, clearFinished } = useExtractJobs();
  const resultSoundJobRef = useRef<Set<string>>(new Set());
  const resultAudioContextRef = useRef<AudioContext | null>(null);

  const [accessToken, setAccessToken] = useState('');
  const [sessionToken, setSessionToken] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('upi');
  const [billingCountry, setBillingCountry] = useState('IN');
  const [captureDiagnostics, setCaptureDiagnostics] = useState(false);
  const [proxySourceMode, setProxySourceMode] = useState<ProxySourceMode>('builtin');
  const [proxyChainMode, setProxyChainMode] = useState('default');
  const [manualRegions, setManualRegions] = useState<Record<ProxyStage, string>>(defaultManualRegions('upi'));
  const [customProxyTexts, setCustomProxyTexts] = useState<Record<ProxyStage, string>>({
    checkout: '',
    promotion: '',
    provider: '',
    approve: '',
  });
  const [customExportProxy, setCustomExportProxy] = useState('');
  const [testResult, setTestResult] = useState<ProxyChainTestResult | null>(null);
  const [testLoading, setTestLoading] = useState(false);

  const proxyChain = useMemo(
    () => buildProxyChain(proxyChainMode, manualRegions, paymentMethod),
    [manualRegions, paymentMethod, proxyChainMode],
  );

  useEffect(() => {
    const saved = loadProxyState();
    if (!saved) return;
    if (saved.paymentMethod) {
      setPaymentMethod(saved.paymentMethod);
      setBillingCountry(defaultBillingCountry(saved.paymentMethod));
    }
    setProxySourceMode(saved.proxySourceMode || 'builtin');
    setProxyChainMode(saved.proxyChainMode);
    setManualRegions((current) => ({ ...current, ...saved.manualRegions }));
    setCustomProxyTexts((current) => ({
      ...current,
      ...(saved.customProxyTexts || {}),
      checkout: saved.customProxyTexts?.checkout || saved.customProxyText || current.checkout,
    }));
    setCustomExportProxy(saved.customExportProxy || '');
  }, []);

  const handlePaymentMethodChange = useCallback((value: PaymentMethod) => {
    setPaymentMethod(value);
    setBillingCountry(defaultBillingCountry(value));
    setManualRegions(defaultManualRegions(value));
    setProxyChainMode((current) => {
      if (current === 'manual') return current;
      return 'default';
    });
    setTestResult(null);
  }, []);

  const customProxyCount = buildProxySeedChains(customProxyTexts, paymentMethod).length;
  const canSubmit = !!accessToken.trim() && (proxySourceMode !== 'custom' || customProxyCount > 0);
  const fixedBillingCountry = paymentMethod === 'ideal' || paymentMethod === 'momo' || paymentMethod === 'kakao';
  const hasFinishedJobs = jobs.some(
    (item) => item.status === 'completed' || item.status === 'failed' || item.status === 'cancelled',
  );

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      const token = accessToken.trim();
      if (!token) return;
      primeResultSound(resultAudioContextRef);
      const proxySeedChains = proxySourceMode === 'custom' ? buildProxySeedChains(customProxyTexts, paymentMethod) : [];
      if (proxySourceMode === 'custom' && proxySeedChains.length === 0) return;

      const options: StartExtractOptions = {
        access_token: token,
        session_token: sessionToken.trim() || undefined,
        payment_method: paymentMethod,
        billing_country: fixedBillingCountry ? defaultBillingCountry(paymentMethod) : proxyChain?.provider || billingCountry,
        proxy_seed_chains: proxySeedChains.length ? proxySeedChains : undefined,
        capture_diagnostics: captureDiagnostics,
        config: configFromProxyChain(proxyChain, customExportProxy, paymentMethod),
      };

      await submit(options);
    },
    [
      accessToken,
      billingCountry,
      captureDiagnostics,
      customExportProxy,
      customProxyTexts,
      fixedBillingCountry,
      paymentMethod,
      proxyChain,
      proxySourceMode,
      sessionToken,
      submit,
    ],
  );

  const handleSaveProxy = useCallback(() => {
    const state: SavedProxyState = {
      paymentMethod,
      proxySourceMode,
      proxyChainMode,
      manualRegions,
      customProxyTexts,
      customExportProxy,
    };
    localStorage.setItem(STORAGE_KEY_PROXY, JSON.stringify(state));
  }, [customExportProxy, customProxyTexts, manualRegions, paymentMethod, proxyChainMode, proxySourceMode]);

  const handleClearProxy = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY_PROXY);
  }, []);

  const handleTestProxy = useCallback(async () => {
    if (!proxyChain) {
      setTestResult({ success: false, error: '请先选择代理链路。' });
      return;
    }
    setTestLoading(true);
    setTestResult(null);
    try {
      const result = await testProxyChain(proxyChain);
      setTestResult(result);
    } catch (e: unknown) {
      setTestResult({
        success: false,
        error: e instanceof Error ? e.message : '代理测试失败',
      });
    } finally {
      setTestLoading(false);
    }
  }, [proxyChain]);

  useEffect(() => {
    jobs.forEach((item) => {
      if (item.status !== 'completed' || !item.result?.url || resultSoundJobRef.current.has(item.job_id)) return;
      resultSoundJobRef.current.add(item.job_id);
      playResultSound(resultAudioContextRef);
    });
  }, [jobs]);

  const handleClearFinishedJobs = useCallback(() => {
    const finishedIds = new Set(
      jobs
        .filter((item) => item.status === 'completed' || item.status === 'failed' || item.status === 'cancelled')
        .map((item) => item.job_id),
    );
    clearFinished();
    finishedIds.forEach((jobId) => {
      resultSoundJobRef.current.delete(jobId);
    });
  }, [clearFinished, jobs]);

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-gray-950">支付链接提取</h2>
            <p className="mt-1 text-sm text-gray-500">后端并发执行提炼任务，前端实时接收 WebSocket 日志。</p>
          </div>
          {jobs.length > 0 && (
            <span className="rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-700">
              运行中 {activeCount} / 总计 {jobs.length}
            </span>
          )}
        </div>

        <div className="mt-5 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {PAYMENT_METHODS.map((item) => {
            const active = paymentMethod === item.value;
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => handlePaymentMethodChange(item.value)}
                className={`h-20 rounded-lg border px-4 text-left transition-colors ${
                  active
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
                    : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50'
                }`}
              >
                <span className="block text-sm font-semibold">{item.label}</span>
                <span className="mt-1 block text-xs text-gray-500">{item.route}</span>
              </button>
            );
          })}
        </div>
      </section>

      <form onSubmit={handleSubmit} className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="space-y-5 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-gray-700">Access Token</span>
              <textarea
                value={accessToken}
                onChange={(event) => setAccessToken(event.target.value)}
                rows={4}
                required
                placeholder="粘贴 access_token，或包含 accessToken 的导出 JSON"
                className="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
              />
            </label>

            <div className="space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">账单国家</span>
                <select
                  value={billingCountry}
                  onChange={(event) => setBillingCountry(event.target.value)}
                  disabled={fixedBillingCountry}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50 disabled:text-gray-400"
                >
                  {COUNTRY_OPTIONS.map((country) => (
                    <option key={country} value={country}>{country}</option>
                  ))}
                </select>
              </label>

              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={captureDiagnostics}
                  onChange={(event) => setCaptureDiagnostics(event.target.checked)}
                  className="rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
                />
                保存 HTTP 诊断日志
              </label>
            </div>
          </div>

          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-gray-700">Session Token Cookie（可选）</span>
            <input
              type="password"
              value={sessionToken}
              onChange={(event) => setSessionToken(event.target.value)}
              placeholder="__Secure-next-auth.session-token"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
            />
          </label>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="space-y-3">
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">代理来源</span>
                <select
                  value={proxySourceMode}
                  onChange={(event) => {
                    setProxySourceMode(event.target.value as ProxySourceMode);
                    setTestResult(null);
                  }}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                >
                  <option value="builtin">服务器 proxy_seeds.txt</option>
                  <option value="custom">本次任务自定义代理</option>
                </select>
              </label>

              {proxySourceMode === 'custom' && (
                <div className="space-y-3">
                  {CUSTOM_PROXY_STAGES.map((stage) => (
                    <label key={stage} className="block">
                      <span className="mb-1 block text-xs font-medium text-gray-500">
                        {customProxyStageLabel(paymentMethod, stage)}
                      </span>
                      <textarea
                        value={customProxyTexts[stage]}
                        onChange={(event) =>
                          setCustomProxyTexts((current) => ({
                            ...current,
                            [stage]: event.target.value,
                          }))
                        }
                        rows={4}
                        placeholder={'HOST:PORT:USER:PASS\nHOST:PORT@USER:PASS\nUSER:PASS:HOST:PORT\nUSER:PASS@HOST:PORT'}
                        className="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                      />
                    </label>
                  ))}
                  <div className="text-xs text-gray-500">
                    {customProxyCount > 0 ? `已组合 ${customProxyCount} 组代理链` : customProxyEmptyText(paymentMethod)}
                  </div>
                </div>
              )}
            </section>

            <section className="space-y-3">
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">国家链路</span>
                <select
                  value={proxyChainMode}
                  onChange={(event) => {
                    setProxyChainMode(event.target.value);
                    setTestResult(null);
                  }}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                >
                  <option value="default">后端默认链路</option>
                  {paymentMethod === 'upi' && <option value="india">JP checkout / IN UPI provider</option>}
                  {paymentMethod === 'ideal' && <option value="ideal">JP checkout / NL iDEAL provider</option>}
                  {paymentMethod === 'momo' && <option value="momo">VN checkout / VN Stripe init</option>}
                  {paymentMethod === 'kakao' && <option value="kakao">KR checkout / VN update / KR Kakao</option>}
                  <option value="manual">手动选择国家</option>
                </select>
              </label>

              <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
                {routeText(paymentMethod)}
              </div>

              {proxyChainMode === 'manual' && (
                <div className="grid grid-cols-2 gap-2">
                  {PROXY_STAGES.map((stage) => (
                    <label key={stage} className="text-xs text-gray-500">
                      <span className="mb-1 block">{PROXY_STAGE_LABELS[stage]}</span>
                      <select
                        value={manualRegions[stage]}
                        onChange={(event) =>
                          setManualRegions((current) => ({
                            ...current,
                            [stage]: event.target.value,
                          }))
                        }
                        className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                      >
                        {COUNTRY_OPTIONS.map((country) => (
                          <option key={country} value={country}>{country}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
              )}
            </section>
          </div>
        </section>

        <aside className="space-y-5">
          <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <div className="space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">前置代理</span>
                <input
                  value={customExportProxy}
                  onChange={(event) => setCustomExportProxy(event.target.value)}
                  placeholder="socks5://127.0.0.1:7890"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                />
              </label>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleTestProxy}
                  disabled={testLoading || !proxyChain}
                  className="rounded-md border border-emerald-200 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300"
                >
                  {testLoading ? '测试中...' : '测试链路'}
                </button>
                <button
                  type="button"
                  onClick={handleSaveProxy}
                  className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
                >
                  保存代理
                </button>
                <button
                  type="button"
                  onClick={handleClearProxy}
                  className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-400 hover:bg-gray-50"
                >
                  清除保存
                </button>
              </div>

              {testResult && (
                <div className={`rounded-md px-3 py-2 text-xs ${testResult.success ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                  {testResult.success
                    ? `代理链路可用（${testResult.latency_ms ?? 0} ms）`
                    : testResult.error || '代理测试失败'}
                </div>
              )}
            </div>
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <div className="space-y-3">
              <button
                type="submit"
                disabled={loading || !canSubmit}
                className="h-11 w-full rounded-lg bg-emerald-600 text-sm font-semibold text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-gray-300"
              >
                {loading ? '提交中...' : activeCount > 0 ? '再开一个提取任务' : '开始提取'}
              </button>
              {hasFinishedJobs && (
                <button
                  type="button"
                  onClick={handleClearFinishedJobs}
                  className="h-10 w-full rounded-lg border border-gray-200 text-sm font-medium text-gray-500 hover:bg-gray-50"
                >
                  清理已结束
                </button>
              )}
              {error && <p className="text-sm text-rose-600">{error}</p>}
            </div>
          </section>
        </aside>
      </form>

      {jobs.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-800">任务队列</h3>
            <span className="text-xs text-gray-500">后端并发上限由 UPISCAN_WORKERS 控制</span>
          </div>
          {jobs.map((item) => (
            <ExtractJobCard
              key={item.job_id}
              job={item}
              onCancel={(jobId) => void cancel(jobId)}
              onRemove={(jobId) => {
                remove(jobId);
                resultSoundJobRef.current.delete(jobId);
              }}
            />
          ))}
        </section>
      )}
    </div>
  );
}
