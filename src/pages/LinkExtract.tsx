import { useCallback, useEffect, useMemo, useState } from 'react';
import QRCode from 'qrcode';
import { useExtractJob } from '../hooks/useExtractJob';
import { testProxyChain, type ProxyChainTestResult, type StartExtractOptions } from '../api/extract';

const STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800',
  running: 'bg-blue-100 text-blue-800',
  completed: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
  cancelled: 'bg-gray-100 text-gray-700',
};

const COUNTRY_OPTIONS = ['IN', 'VN', 'US', 'NL', 'JP', 'BR', 'DE', 'FR', 'GB'];
const PROXY_STAGES = ['checkout', 'promotion', 'provider', 'approve'] as const;
const STORAGE_KEY_PROXY = 'upiscan_extract_proxy';

type ProxyStage = (typeof PROXY_STAGES)[number];

interface SavedProxyState {
  proxyChainMode: string;
  manualRegions: Record<ProxyStage, string>;
  customExportProxy: string;
}

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
): Record<string, string> | undefined {
  if (mode === 'default') return undefined;
  if (mode === 'india') {
    return { checkout: 'IN', promotion: 'VN', provider: 'IN', approve: 'IN' };
  }
  if (mode === 'manual') return { ...manualRegions };
  return undefined;
}

function configFromProxyChain(
  chain: Record<string, string> | undefined,
  customExportProxy: string,
): Record<string, unknown> | undefined {
  const config: Record<string, unknown> = {};
  if (chain?.checkout) config.bootstrap_country = chain.checkout;
  if (chain?.promotion) config.promotion_countries = [chain.promotion];
  if (chain?.provider) {
    config.provider_country = chain.provider;
    config.billing_country = chain.provider;
  }
  if (customExportProxy.trim()) config.pre_proxy = customExportProxy.trim();
  return Object.keys(config).length ? config : undefined;
}

export function LinkExtract() {
  const { job, loading, error, submit, cancel, reset } = useExtractJob();

  const [accessToken, setAccessToken] = useState('');
  const [sessionToken, setSessionToken] = useState('');
  const [billingCountry, setBillingCountry] = useState('IN');
  const [captureDiagnostics, setCaptureDiagnostics] = useState(false);
  const [proxyChainMode, setProxyChainMode] = useState('default');
  const [manualRegions, setManualRegions] = useState<Record<ProxyStage, string>>({
    checkout: 'IN',
    promotion: 'VN',
    provider: 'IN',
    approve: 'IN',
  });
  const [customExportProxy, setCustomExportProxy] = useState('');
  const [testResult, setTestResult] = useState<ProxyChainTestResult | null>(null);
  const [testLoading, setTestLoading] = useState(false);
  const [logsExpanded, setLogsExpanded] = useState(true);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const proxyChain = useMemo(
    () => buildProxyChain(proxyChainMode, manualRegions),
    [manualRegions, proxyChainMode],
  );

  useEffect(() => {
    const saved = loadProxyState();
    if (!saved) return;
    setProxyChainMode(saved.proxyChainMode);
    setManualRegions((current) => ({ ...current, ...saved.manualRegions }));
    setCustomExportProxy(saved.customExportProxy || '');
  }, []);

  useEffect(() => {
    if (!job?.result?.url) {
      setQrDataUrl(null);
      return;
    }
    QRCode.toDataURL(job.result.url, { width: 220, margin: 1 })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(null));
  }, [job?.result?.url]);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      const token = accessToken.trim();
      if (!token) return;

      const options: StartExtractOptions = {
        access_token: token,
        session_token: sessionToken.trim() || undefined,
        payment_method: 'upi',
        payment_page_mode: 'custom',
        language: 'auto',
        billing_country: billingCountry,
        proxy_chain: proxyChain,
        custom_export_proxy: customExportProxy.trim() || undefined,
        capture_diagnostics: captureDiagnostics,
        config: configFromProxyChain(proxyChain, customExportProxy),
      };

      await submit(options);
    },
    [
      accessToken,
      billingCountry,
      captureDiagnostics,
      customExportProxy,
      proxyChain,
      sessionToken,
      submit,
    ],
  );

  const handleSaveProxy = useCallback(() => {
    const state: SavedProxyState = {
      proxyChainMode,
      manualRegions,
      customExportProxy,
    };
    localStorage.setItem(STORAGE_KEY_PROXY, JSON.stringify(state));
  }, [customExportProxy, manualRegions, proxyChainMode]);

  const handleClearProxy = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY_PROXY);
  }, []);

  const handleTestProxy = useCallback(async () => {
    if (!proxyChain) {
      setTestResult({ success: false, error: 'Select a proxy chain first.' });
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
        error: e instanceof Error ? e.message : 'Proxy test failed',
      });
    } finally {
      setTestLoading(false);
    }
  }, [proxyChain]);

  const handleCopyUrl = useCallback(async () => {
    if (!job?.result?.url) return;
    await navigator.clipboard.writeText(job.result.url);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }, [job?.result?.url]);

  const canCancel = job?.status === 'pending' || job?.status === 'running';

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">UPI link extraction</h2>
          <p className="text-sm text-gray-500">FastAPI backend with live WebSocket logs.</p>
        </div>
        {job && (
          <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_COLORS[job.status] || STATUS_COLORS.pending}`}>
            {STATUS_LABELS[job.status] || job.status}
          </span>
        )}
      </div>

      <form onSubmit={handleSubmit} className="space-y-5 rounded-lg border border-gray-200 bg-white p-5">
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Access token</label>
            <textarea
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              rows={3}
              required
              placeholder="Paste access_token or exported JSON containing accessToken"
              className="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
            />
          </div>

          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-gray-700">Billing country</label>
              <select
                value={billingCountry}
                onChange={(event) => setBillingCountry(event.target.value)}
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
              Save HTTP diagnostics
            </label>
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-gray-700">Session token cookie</label>
          <input
            type="password"
            value={sessionToken}
            onChange={(event) => setSessionToken(event.target.value)}
            placeholder="Optional __Secure-next-auth.session-token"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <label className="block text-sm font-medium text-gray-700">Proxy chain</label>
            <select
              value={proxyChainMode}
              onChange={(event) => {
                setProxyChainMode(event.target.value);
                setTestResult(null);
              }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
            >
              <option value="default">Use backend proxy_seeds.txt</option>
              <option value="india">IN checkout / VN promo / IN provider</option>
              <option value="manual">Manual countries</option>
            </select>

            {proxyChainMode === 'manual' && (
              <div className="grid grid-cols-2 gap-2">
                {PROXY_STAGES.map((stage) => (
                  <label key={stage} className="text-xs text-gray-500">
                    <span className="mb-1 block capitalize">{stage}</span>
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
            <label className="block text-sm font-medium text-gray-700">Pre-proxy</label>
            <input
              value={customExportProxy}
              onChange={(event) => setCustomExportProxy(event.target.value)}
              placeholder="Optional local proxy, e.g. socks5://127.0.0.1:7890"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
            />

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleTestProxy}
                disabled={testLoading || !proxyChain}
                className="rounded border border-purple-200 px-3 py-1.5 text-xs font-medium text-purple-700 hover:bg-purple-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300"
              >
                {testLoading ? 'Testing...' : 'Test chain'}
              </button>
              <button
                type="button"
                onClick={handleSaveProxy}
                className="rounded border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
              >
                Save proxy
              </button>
              <button
                type="button"
                onClick={handleClearProxy}
                className="rounded border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-400 hover:bg-gray-50"
              >
                Clear saved
              </button>
            </div>

            {testResult && (
              <div className={`rounded px-3 py-2 text-xs ${testResult.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                {testResult.success
                  ? `Proxy chain accepted (${testResult.latency_ms ?? 0} ms)`
                  : testResult.error || 'Proxy test failed'}
              </div>
            )}
          </div>
        </div>

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={loading || !accessToken.trim() || canCancel}
            className="flex-1 rounded-lg bg-purple-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {loading ? 'Submitting...' : canCancel ? 'Job running' : 'Start extraction'}
          </button>
          {canCancel && (
            <button
              type="button"
              onClick={cancel}
              className="rounded-lg border border-red-200 px-4 py-2.5 text-sm font-medium text-red-600 hover:bg-red-50"
            >
              Cancel
            </button>
          )}
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}
      </form>

      {job && (
        <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-gray-800">Job result</h3>
              <p className="font-mono text-xs text-gray-400">{job.job_id}</p>
            </div>
            {(job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') && (
              <button
                type="button"
                onClick={reset}
                className="rounded border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-500 hover:bg-gray-50"
              >
                New job
              </button>
            )}
          </div>

          {(job.status === 'pending' || job.status === 'running') && (
            <div className="flex items-center gap-3 text-sm text-gray-500">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-purple-500 border-t-transparent" />
              Extracting UPI payment URL...
            </div>
          )}

          {job.status === 'completed' && job.result?.url && (
            <div className="grid gap-4 lg:grid-cols-[1fr_240px]">
              <div className="rounded-lg bg-gray-50 p-4">
                <label className="mb-2 block text-xs font-medium text-gray-500">Payment URL</label>
                <div className="flex gap-2">
                  <code className="min-w-0 flex-1 break-all rounded border border-gray-200 bg-white px-3 py-2 text-xs text-gray-700">
                    {job.result.url}
                  </code>
                  <button
                    type="button"
                    onClick={handleCopyUrl}
                    className="shrink-0 rounded border border-purple-200 px-3 py-2 text-xs font-medium text-purple-700 hover:bg-purple-50"
                  >
                    {copied ? 'Copied' : 'Copy'}
                  </button>
                </div>
              </div>
              {qrDataUrl && (
                <div className="flex justify-center rounded-lg border border-gray-200 bg-white p-3">
                  <img src={qrDataUrl} alt="Payment QR code" className="h-52 w-52" />
                </div>
              )}
            </div>
          )}

          {job.status === 'failed' && (
            <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {job.error || 'Extraction failed'}
            </div>
          )}

          {job.logs.length > 0 && (
            <div>
              <button
                type="button"
                onClick={() => setLogsExpanded((value) => !value)}
                className="text-xs text-gray-500 hover:text-gray-700"
              >
                {logsExpanded ? 'Hide logs' : 'Show logs'} ({job.logs.length})
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
      )}
    </div>
  );
}
