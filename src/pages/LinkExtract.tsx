import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';
import QRCode from 'qrcode';
import { useExtractJobs } from '../hooks/useExtractJob';
import {
  submitPublisherCheckout,
  testProxyChain,
  type ExtractJobResponse,
  type ProxyChainTestResult,
  type StartExtractOptions,
} from '../api/extract';
import { getApiKey, setApiKey } from '../api/client';

const STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  running: '提取中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800',
  running: 'bg-blue-100 text-blue-800',
  completed: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
  cancelled: 'bg-gray-100 text-gray-700',
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
const STORAGE_KEY_PUBLISHER = 'upiscan_publisher_handoff';
const PUBLISHER_UPSTREAM_DEFAULT = 'https://foarge.com/api/publisher/v1';

type ProxyStage = (typeof PROXY_STAGES)[number];
type PaymentMethod = 'upi' | 'ideal' | 'momo' | 'kakao';
type ProxySourceMode = 'builtin' | 'custom';
type PublisherStatus = 'idle' | 'submitting' | 'success' | 'error';
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

interface SavedPublisherState {
  enabled: boolean;
  apiBase: string;
  taskId: string;
  autoSubmit: boolean;
}

function loadProxyState(): SavedProxyState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PROXY);
    return raw ? (JSON.parse(raw) as SavedProxyState) : null;
  } catch {
    return null;
  }
}

function loadPublisherState(): SavedPublisherState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PUBLISHER);
    return raw ? (JSON.parse(raw) as SavedPublisherState) : null;
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
  if (mode === 'india') {
    return { checkout: 'JP', promotion: 'IN', provider: 'IN', approve: 'IN' };
  }
  if (mode === 'ideal') {
    return { checkout: 'JP', promotion: 'NL', provider: 'NL', approve: 'NL' };
  }
  if (mode === 'momo') {
    return { checkout: 'VN', promotion: 'VN', provider: 'VN', approve: 'VN' };
  }
  if (mode === 'kakao') {
    return { checkout: 'KR', promotion: 'VN', provider: 'KR', approve: 'KR' };
  }
  if (mode === 'manual') return { ...manualRegions };
  if (paymentMethod === 'ideal') {
    return { checkout: 'JP', promotion: 'NL', provider: 'NL', approve: 'NL' };
  }
  if (paymentMethod === 'momo') {
    return { checkout: 'VN', promotion: 'VN', provider: 'VN', approve: 'VN' };
  }
  if (paymentMethod === 'kakao') {
    return { checkout: 'KR', promotion: 'VN', provider: 'KR', approve: 'KR' };
  }
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
  if (paymentMethod === 'ideal') {
    return stage === 'checkout'
      ? 'JP 代理（前段 / 创建 checkout）'
      : 'NL 代理（iDEAL provider）';
  }
  if (paymentMethod === 'momo') {
    return stage === 'checkout'
      ? 'VN 代理（创建 checkout）'
      : 'VN 代理（Stripe init / MoMo 检测）';
  }
  if (paymentMethod === 'kakao') {
    return stage === 'checkout'
      ? 'KR 代理（checkout / Stripe / Kakao / approve）'
      : 'VN 代理（checkout/update 优惠阶段）';
  }
  return stage === 'checkout'
    ? 'JP 代理（创建 checkout）'
    : 'IN 代理（UPI provider / approve）';
}

function customProxyEmptyText(paymentMethod: PaymentMethod): string {
  if (paymentMethod === 'ideal') {
    return '请分别输入 JP 代理和 NL 代理；NL 会用于 iDEAL provider';
  }
  if (paymentMethod === 'momo') {
    return '请分别输入两组 VN 代理；第一组创建 checkout，第二组做 Stripe init 与 MoMo 检测';
  }
  if (paymentMethod === 'kakao') {
    return '请分别输入 KR 代理和 VN 代理；KR 用于 checkout/provider/approve，VN 用于 checkout/update';
  }
  return '请分别输入 JP 代理和 IN 代理；JP 创建 checkout，IN 用于 UPI provider / approve';
}

function defaultManualRegions(paymentMethod: PaymentMethod): Record<ProxyStage, string> {
  if (paymentMethod === 'ideal') {
    return { checkout: 'JP', promotion: 'NL', provider: 'NL', approve: 'NL' };
  }
  if (paymentMethod === 'momo') {
    return { checkout: 'VN', promotion: 'VN', provider: 'VN', approve: 'VN' };
  }
  if (paymentMethod === 'kakao') {
    return { checkout: 'KR', promotion: 'VN', provider: 'KR', approve: 'KR' };
  }
  return { checkout: 'JP', promotion: 'IN', provider: 'IN', approve: 'IN' };
}

function defaultBillingCountry(paymentMethod: PaymentMethod): string {
  if (paymentMethod === 'ideal') return 'NL';
  if (paymentMethod === 'momo') return 'VN';
  if (paymentMethod === 'kakao') return 'KR';
  return 'IN';
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

interface PublisherJobState {
  status: PublisherStatus;
  message: string;
}

interface ExtractJobCardProps {
  job: ExtractJobResponse;
  publisherState?: PublisherJobState;
  publisherEnabled: boolean;
  canSubmitPublisher: boolean;
  onCancel: (jobId: string) => void;
  onRemove: (jobId: string) => void;
  onSubmitPublisher: (job: ExtractJobResponse) => void;
}

function ExtractJobCard({
  job,
  publisherState,
  publisherEnabled,
  canSubmitPublisher,
  onCancel,
  onRemove,
  onSubmitPublisher,
}: ExtractJobCardProps) {
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
  const publisherSubmitting = publisherState?.status === 'submitting';

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">任务结果</h3>
          <p className="font-mono text-xs text-gray-400">{job.job_id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_COLORS[job.status] || STATUS_COLORS.pending}`}>
            {STATUS_LABELS[job.status] || job.status}
          </span>
          {canCancel && (
            <button
              type="button"
              onClick={() => onCancel(job.job_id)}
              className="rounded border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
            >
              取消
            </button>
          )}
          {canRemove && (
            <button
              type="button"
              onClick={() => onRemove(job.job_id)}
              className="rounded border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-500 hover:bg-gray-50"
            >
              移除
            </button>
          )}
        </div>
      </div>

      {(job.status === 'pending' || job.status === 'running') && (
        <div className="flex items-center gap-3 text-sm text-gray-500">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-purple-500 border-t-transparent" />
          正在提炼支付链接...
        </div>
      )}

      {job.status === 'completed' && job.result?.url && (
        <div className="grid gap-4 lg:grid-cols-[1fr_240px]">
          <div className="rounded-lg bg-gray-50 p-4">
            <label className="mb-2 block text-xs font-medium text-gray-500">支付链接</label>
            <div className="flex gap-2">
              <code className="min-w-0 flex-1 break-all rounded border border-gray-200 bg-white px-3 py-2 text-xs text-gray-700">
                {job.result.url}
              </code>
              <button
                type="button"
                onClick={handleCopyUrl}
                className="shrink-0 rounded border border-purple-200 px-3 py-2 text-xs font-medium text-purple-700 hover:bg-purple-50"
              >
                {copied ? '已复制' : '复制'}
              </button>
            </div>
            {publisherEnabled && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => onSubmitPublisher(job)}
                  disabled={!canSubmitPublisher || publisherSubmitting}
                  className="rounded border border-purple-200 bg-white px-3 py-1.5 text-xs font-medium text-purple-700 hover:bg-purple-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300"
                >
                  {publisherSubmitting ? '提交中...' : '提交支付长链'}
                </button>
                {publisherState?.message && (
                  <span
                    className={`rounded px-2 py-1 text-xs ${
                      publisherState.status === 'error'
                        ? 'bg-red-50 text-red-700'
                        : publisherState.status === 'success'
                          ? 'bg-green-50 text-green-700'
                          : 'bg-blue-50 text-blue-700'
                    }`}
                  >
                    {publisherState.message}
                  </span>
                )}
              </div>
            )}
          </div>
          {qrDataUrl && (
            <div className="flex justify-center rounded-lg border border-gray-200 bg-white p-3">
              <img src={qrDataUrl} alt="支付二维码" className="h-52 w-52" />
            </div>
          )}
        </div>
      )}

      {job.status === 'failed' && (
        <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {job.error || '提取失败'}
        </div>
      )}

      {job.logs.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setLogsExpanded((value) => !value)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            {logsExpanded ? '隐藏日志' : '显示日志'} ({job.logs.length})
          </button>
          {logsExpanded && (
            <div className="mt-2 max-h-80 space-y-1 overflow-y-auto rounded-lg bg-gray-950 p-3">
              {job.logs.map((log, index) => (
                <div
                  key={`${log.timestamp}-${index}`}
                  className={`font-mono text-xs ${
                    log.level === 'error'
                      ? 'text-red-300'
                      : log.level === 'warn'
                        ? 'text-yellow-300'
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
    </div>
  );
}

export function LinkExtract() {
  const { jobs, loading, error, activeCount, submit, cancel, remove, clearFinished } = useExtractJobs();
  const autoPublisherJobRef = useRef<Set<string>>(new Set());
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
  const [jobAccessTokens, setJobAccessTokens] = useState<Record<string, string>>({});
  const [publisherEnabled, setPublisherEnabled] = useState(false);
  const [publisherApiBase, setPublisherApiBase] = useState(PUBLISHER_UPSTREAM_DEFAULT);
  const [publisherApiKey, setPublisherApiKey] = useState(getApiKey() || '');
  const [publisherTaskId, setPublisherTaskId] = useState('');
  const [publisherAutoSubmit, setPublisherAutoSubmit] = useState(false);
  const [publisherNotice, setPublisherNotice] = useState('');
  const [publisherByJob, setPublisherByJob] = useState<Record<string, PublisherJobState>>({});

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

  useEffect(() => {
    const saved = loadPublisherState();
    if (!saved) return;
    setPublisherEnabled(saved.enabled);
    setPublisherApiBase(
      saved.apiBase && !saved.apiBase.startsWith('/api/')
        ? saved.apiBase
        : PUBLISHER_UPSTREAM_DEFAULT,
    );
    setPublisherTaskId(saved.taskId || '');
    setPublisherAutoSubmit(saved.autoSubmit);
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
        payment_page_mode: 'custom',
        language: 'auto',
        billing_country: paymentMethod === 'ideal' || paymentMethod === 'momo' || paymentMethod === 'kakao'
          ? defaultBillingCountry(paymentMethod)
          : proxyChain?.provider || billingCountry,
        proxy_chain: proxyChain,
        proxy_seed_chains: proxySeedChains.length ? proxySeedChains : undefined,
        custom_export_proxy: customExportProxy.trim() || undefined,
        capture_diagnostics: captureDiagnostics,
        config: configFromProxyChain(proxyChain, customExportProxy, paymentMethod),
      };

      const jobId = await submit(options);
      if (jobId) {
        setJobAccessTokens((current) => ({ ...current, [jobId]: token }));
      }
    },
    [
      accessToken,
      billingCountry,
      captureDiagnostics,
      customExportProxy,
      customProxyTexts,
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

  const handleSavePublisher = useCallback(() => {
    if (publisherApiKey.trim()) setApiKey(publisherApiKey.trim());
    const state: SavedPublisherState = {
      enabled: publisherEnabled,
      apiBase: publisherApiBase.trim() || PUBLISHER_UPSTREAM_DEFAULT,
      taskId: publisherTaskId.trim(),
      autoSubmit: publisherAutoSubmit,
    };
    localStorage.setItem(STORAGE_KEY_PUBLISHER, JSON.stringify(state));
    setPublisherNotice('发布接口配置已保存');
  }, [publisherApiBase, publisherApiKey, publisherAutoSubmit, publisherEnabled, publisherTaskId]);

  const handleSubmitPublisher = useCallback(async (targetJob: ExtractJobResponse) => {
    const payLink = targetJob.result?.url;
    const taskId = publisherTaskId.trim();
    const apiKey = publisherApiKey.trim();
    if (!payLink || !taskId || !apiKey) return;

    setPublisherByJob((current) => ({
      ...current,
      [targetJob.job_id]: { status: 'submitting', message: '' },
    }));
    try {
      setApiKey(apiKey);
      await submitPublisherCheckout({
        api_key: apiKey,
        api_base: publisherApiBase.trim() || PUBLISHER_UPSTREAM_DEFAULT,
        task_id: taskId,
        access_token: jobAccessTokens[targetJob.job_id] || accessToken.trim(),
        pay_link: payLink,
      });
      setPublisherByJob((current) => ({
        ...current,
        [targetJob.job_id]: { status: 'success', message: '支付长链已提交到发布任务' },
      }));
    } catch (e: unknown) {
      setPublisherByJob((current) => ({
        ...current,
        [targetJob.job_id]: {
          status: 'error',
          message: e instanceof Error ? e.message : '发布接口提交失败',
        },
      }));
    }
  }, [accessToken, jobAccessTokens, publisherApiBase, publisherApiKey, publisherTaskId]);

  useEffect(() => {
    if (!publisherEnabled || !publisherAutoSubmit) return;
    if (!publisherTaskId.trim() || !publisherApiKey.trim()) return;
    jobs.forEach((item) => {
      if (
        item.status !== 'completed' ||
        !item.result?.url ||
        autoPublisherJobRef.current.has(item.job_id)
      ) {
        return;
      }
      autoPublisherJobRef.current.add(item.job_id);
      void handleSubmitPublisher(item);
    });
  }, [
    handleSubmitPublisher,
    jobs,
    publisherApiKey,
    publisherAutoSubmit,
    publisherEnabled,
    publisherTaskId,
  ]);

  useEffect(() => {
    jobs.forEach((item) => {
      if (
        item.status !== 'completed' ||
        !item.result?.url ||
        resultSoundJobRef.current.has(item.job_id)
      ) {
        return;
      }
      resultSoundJobRef.current.add(item.job_id);
      playResultSound(resultAudioContextRef);
    });
  }, [jobs]);

  const customProxyCount = buildProxySeedChains(customProxyTexts, paymentMethod).length;
  const hasFinishedJobs = jobs.some(
    (item) => item.status === 'completed' || item.status === 'failed' || item.status === 'cancelled',
  );
  const handleClearFinishedJobs = useCallback(() => {
    const finishedIds = new Set(
      jobs
        .filter((item) => item.status === 'completed' || item.status === 'failed' || item.status === 'cancelled')
        .map((item) => item.job_id),
    );
    clearFinished();
    setPublisherByJob((current) =>
      Object.fromEntries(Object.entries(current).filter(([jobId]) => !finishedIds.has(jobId))),
    );
    setJobAccessTokens((current) =>
      Object.fromEntries(Object.entries(current).filter(([jobId]) => !finishedIds.has(jobId))),
    );
    finishedIds.forEach((jobId) => {
      autoPublisherJobRef.current.delete(jobId);
      resultSoundJobRef.current.delete(jobId);
    });
  }, [clearFinished, jobs]);
  const canSubmit =
    !!accessToken.trim() &&
    (proxySourceMode !== 'custom' || customProxyCount > 0);
  const canSubmitPublisher =
    !!publisherTaskId.trim() &&
    !!publisherApiKey.trim();

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">支付链接提取</h2>
          <p className="text-sm text-gray-500">后端并发执行 UPI / iDEAL / MoMo / Kakao 提炼任务，前端实时显示每个任务的 WebSocket 日志。</p>
        </div>
        {jobs.length > 0 && (
          <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700">
            运行中 {activeCount} / 总计 {jobs.length}
          </span>
        )}
      </div>

      <form onSubmit={handleSubmit} className="space-y-5 rounded-lg border border-gray-200 bg-white p-5">
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Access Token</label>
            <textarea
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              rows={3}
              required
              placeholder="粘贴 access_token，或包含 accessToken 的导出 JSON"
              className="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
            />
          </div>

          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-gray-700">提炼类型</label>
              <select
                value={paymentMethod}
                onChange={(event) => handlePaymentMethodChange(event.target.value as PaymentMethod)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
              >
                <option value="upi">UPI（JP / IN）</option>
                <option value="ideal">iDEAL（JP / NL）</option>
                <option value="momo">MoMo（VN / VND）</option>
                <option value="kakao">Kakao（KR / VN / KR）</option>
              </select>
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-gray-700">账单国家</label>
              <select
                value={billingCountry}
                onChange={(event) => setBillingCountry(event.target.value)}
                disabled={paymentMethod === 'ideal' || paymentMethod === 'momo' || paymentMethod === 'kakao'}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
              >
                {COUNTRY_OPTIONS.map((country) => (
                  <option key={country} value={country}>{country}</option>
                ))}
              </select>
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={captureDiagnostics}
                onChange={(event) => setCaptureDiagnostics(event.target.checked)}
                className="rounded border-gray-300 text-purple-600 focus:ring-purple-500"
              />
              保存 HTTP 诊断日志
            </label>
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-gray-700">Session Token Cookie（可选）</label>
          <input
            type="password"
            value={sessionToken}
            onChange={(event) => setSessionToken(event.target.value)}
            placeholder="可选：__Secure-next-auth.session-token"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <label className="block text-sm font-medium text-gray-700">代理来源</label>
            <select
              value={proxySourceMode}
              onChange={(event) => {
                setProxySourceMode(event.target.value as ProxySourceMode);
                setTestResult(null);
              }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
            >
              <option value="builtin">使用服务器 proxy_seeds.txt</option>
              <option value="custom">使用本次任务自定义代理</option>
            </select>

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
                      rows={3}
                      placeholder={'HOST:PORT:USER:PASS\nHOST:PORT@USER:PASS\nUSER:PASS:HOST:PORT\nUSER:PASS@HOST:PORT'}
                      className="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
                    />
                  </label>
                ))}
                <div className="text-xs text-gray-500">
                  {customProxyCount > 0 ? `已组合 ${customProxyCount} 组两段代理链` : customProxyEmptyText(paymentMethod)}
                </div>
              </div>
            )}

            <label className="block text-sm font-medium text-gray-700">国家链路</label>
            <select
              value={proxyChainMode}
              onChange={(event) => {
                setProxyChainMode(event.target.value);
                setTestResult(null);
              }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
            >
              <option value="default">使用后端默认国家链路</option>
              {paymentMethod === 'upi' && (
                <option value="india">JP checkout / IN UPI provider</option>
              )}
              {paymentMethod === 'ideal' && (
                <option value="ideal">JP checkout / NL iDEAL provider</option>
              )}
              {paymentMethod === 'momo' && (
                <option value="momo">VN checkout / VN Stripe init</option>
              )}
              {paymentMethod === 'kakao' && (
                <option value="kakao">KR checkout / VN update / KR Kakao</option>
              )}
              <option value="manual">手动选择国家</option>
            </select>
            <p className="text-xs text-gray-500">
              {paymentMethod === 'ideal'
                ? 'iDEAL 默认使用 JP 代理创建 checkout，NL 代理完成 provider 与确认。'
                : paymentMethod === 'momo'
                  ? 'MoMo 默认使用 VN/VND checkout，通过 Stripe init 的 payment_method_types 判断是否包含 momo。'
                  : paymentMethod === 'kakao'
                    ? 'Kakao 默认使用 KR 创建 checkout/bootstrap，VN 执行 checkout/update，再回到 KR 完成 Kakao/Nicepay 跳转。'
                  : 'UPI 默认使用 JP 代理创建 checkout，IN 代理完成 Stripe/UPI/approve。'}
            </p>

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
                      className="w-full rounded border border-gray-300 px-2 py-1.5 text-xs outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
                    >
                      {COUNTRY_OPTIONS.map((country) => (
                        <option key={country} value={country}>{country}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-3">
            <label className="block text-sm font-medium text-gray-700">前置代理</label>
            <input
              value={customExportProxy}
              onChange={(event) => setCustomExportProxy(event.target.value)}
              placeholder="可选本地前置代理，例如 socks5://127.0.0.1:7890"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
            />

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleTestProxy}
                disabled={testLoading || !proxyChain}
                className="rounded border border-purple-200 px-3 py-1.5 text-xs font-medium text-purple-700 hover:bg-purple-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300"
              >
                {testLoading ? '测试中...' : '测试链路'}
              </button>
              <button
                type="button"
                onClick={handleSaveProxy}
                className="rounded border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
              >
                保存代理
              </button>
              <button
                type="button"
                onClick={handleClearProxy}
                className="rounded border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-400 hover:bg-gray-50"
              >
                清除保存
              </button>
            </div>

            {testResult && (
              <div className={`rounded px-3 py-2 text-xs ${testResult.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                {testResult.success
                  ? `代理链路可用（${testResult.latency_ms ?? 0} ms）`
                  : testResult.error || '代理测试失败'}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
            <input
              type="checkbox"
              checked={publisherEnabled}
              onChange={(event) => setPublisherEnabled(event.target.checked)}
              className="rounded border-gray-300 text-purple-600 focus:ring-purple-500"
            />
            提交到发布接口
          </label>

          {publisherEnabled && (
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-500">发布接口 API Key</label>
                <input
                  type="password"
                  value={publisherApiKey}
                  onChange={(event) => setPublisherApiKey(event.target.value)}
                  placeholder="pk_live_... or PBK-..."
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-gray-500">发布任务 ID</label>
                <input
                  value={publisherTaskId}
                  onChange={(event) => setPublisherTaskId(event.target.value)}
                  placeholder="任务 ID"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
                />
              </div>

              <div className="lg:col-span-2">
                <label className="mb-1 block text-xs font-medium text-gray-500">发布接口上游地址</label>
                <input
                  value={publisherApiBase}
                  onChange={(event) => setPublisherApiBase(event.target.value)}
                  placeholder="https://foarge.com/api/publisher/v1"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
                />
              </div>

              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={publisherAutoSubmit}
                  onChange={(event) => setPublisherAutoSubmit(event.target.checked)}
                  className="rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                />
                提取成功后自动提交
              </label>

              <div className="flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  onClick={handleSavePublisher}
                  className="rounded border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
                >
                  保存发布配置
                </button>
              </div>

              {publisherNotice && (
                <div className="lg:col-span-2 rounded bg-blue-50 px-3 py-2 text-xs text-blue-700">
                  {publisherNotice}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={loading || !canSubmit}
            className="flex-1 rounded-lg bg-purple-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {loading ? '提交中...' : activeCount > 0 ? '再开一个提取任务' : '开始提取'}
          </button>
          {hasFinishedJobs && (
            <button
              type="button"
              onClick={handleClearFinishedJobs}
              className="rounded-lg border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-500 hover:bg-gray-50"
            >
              清理已结束
            </button>
          )}
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}
      </form>

      {jobs.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-800">任务队列</h3>
            <span className="text-xs text-gray-500">后端并发上限由 UPISCAN_WORKERS 控制</span>
          </div>
          {jobs.map((item) => (
            <ExtractJobCard
              key={item.job_id}
              job={item}
              publisherState={publisherByJob[item.job_id]}
              publisherEnabled={publisherEnabled}
              canSubmitPublisher={canSubmitPublisher}
              onCancel={(jobId) => void cancel(jobId)}
              onRemove={(jobId) => {
                remove(jobId);
                setPublisherByJob((current) => {
                  const next = { ...current };
                  delete next[jobId];
                  return next;
                });
                setJobAccessTokens((current) => {
                  const next = { ...current };
                  delete next[jobId];
                  return next;
                });
                autoPublisherJobRef.current.delete(jobId);
                resultSoundJobRef.current.delete(jobId);
              }}
              onSubmitPublisher={(targetJob) => void handleSubmitPublisher(targetJob)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
