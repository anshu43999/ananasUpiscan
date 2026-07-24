import { getExtractApiBase } from './client';

async function extractFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${getExtractApiBase()}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> | undefined),
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const message =
      (body as { detail?: string }).detail ||
      (body as { message?: string }).message ||
      (body as { error?: string }).error ||
      `Request failed (${res.status})`;
    throw new Error(message);
  }

  return res.json() as Promise<T>;
}

export interface ExtractConfig {
  cdk_enabled: boolean;
  cost_per_task: number;
  log_visible: boolean;
}

export function getExtractConfig(): Promise<ExtractConfig> {
  return extractFetch('/api/config');
}

export interface ExtractSettings {
  payment_methods?: string[];
  languages?: string[];
  billing_countries?: string[];
  proxy_regions?: string[];
}

export function getExtractSettings(): Promise<ExtractSettings> {
  return extractFetch('/api/settings');
}

export interface ExtractJobLog {
  timestamp: string;
  message: string;
  level: 'info' | 'warn' | 'error';
}

export interface ExtractJobResult {
  url: string;
  cs_id?: string;
  billing_country?: string;
  currency?: string;
  amount?: number;
  qr_code?: string;
  expires_at?: string;
  status: 'ok' | string;
  luck?: number;
}

export type ExtractJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface ExtractJobResponse {
  job_id: string;
  status: ExtractJobStatus;
  logs: ExtractJobLog[];
  result: ExtractJobResult | null;
  error?: string | null;
  diagnostic_url?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface StartExtractOptions {
  access_token: string;
  session_token?: string;
  payment_method: string;
  payment_page_mode: string;
  language: string;
  billing_country: string;
  proxy_chain?: Record<string, string>;
  proxy_seeds?: string[];
  proxy_seed_chains?: Array<Record<string, string>>;
  custom_export_proxy?: string;
  client_fingerprint?: string;
  capture_diagnostics?: boolean;
  config?: Record<string, unknown>;
}

export function startExtractJob(
  options: StartExtractOptions,
): Promise<{ job_id: string }> {
  return extractFetch('/api/extract/jobs', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export function getExtractJob(jobId: string): Promise<ExtractJobResponse> {
  return extractFetch(`/api/extract/jobs/${jobId}`);
}

export function cancelExtractJob(jobId: string): Promise<ExtractJobResponse> {
  return extractFetch(`/api/extract/jobs/${jobId}/cancel`, {
    method: 'POST',
  });
}

export interface ProxyChainTestResult {
  success: boolean;
  latency_ms?: number;
  error?: string;
}

export function testProxyChain(
  config: Record<string, string>,
): Promise<ProxyChainTestResult> {
  return extractFetch('/api/proxy-chain-test', {
    method: 'POST',
    body: JSON.stringify(config),
  });
}

export interface PublisherSubmitCheckoutOptions {
  api_key: string;
  api_base: string;
  task_id: string;
  access_token: string;
  pay_link: string;
}

export interface PublisherSubmitCheckoutResult {
  success: boolean;
  status_code: number;
  message?: string;
  data?: Record<string, unknown>;
}

export function submitPublisherCheckout(
  options: PublisherSubmitCheckoutOptions,
): Promise<PublisherSubmitCheckoutResult> {
  return extractFetch('/api/publisher/submit-checkout', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export function extractJobWsUrl(jobId: string): string {
  const base = getExtractApiBase();
  const path = `/api/extract/jobs/${jobId}/ws`;

  if (!base) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}${path}`;
  }

  if (base.startsWith('http://') || base.startsWith('https://')) {
    const url = new URL(path, base);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${base}${path}`;
}
