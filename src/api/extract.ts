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
  billing_country: string;
  proxy_seeds?: string[];
  proxy_seed_chains?: Array<Record<string, string>>;
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

export interface MomoPermissionCheckOptions {
  access_token: string;
  session_token?: string;
  proxy_seeds?: string[];
  proxy_seed_chains?: Array<Record<string, string>>;
  capture_diagnostics?: boolean;
  config?: Record<string, unknown>;
}

export interface MomoPermissionCheckResponse {
  available: boolean;
  status: string;
  payment_method_types: string[];
  local_methods: string[];
  amount?: number | null;
  currency?: string | null;
  checkout_id?: string | null;
  checkout_url?: string | null;
  error?: string | null;
}

export function checkMomoPermission(
  options: MomoPermissionCheckOptions,
): Promise<MomoPermissionCheckResponse> {
  return extractFetch('/api/momo-permission-check', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export interface ProxyCheckItem {
  id: number;
  raw: string;
  proxy: string;
  ok: boolean;
  ip?: string | null;
  status: string;
  latency_ms?: number | null;
  error?: string | null;
}

export interface ProxyCheckResponse {
  items: ProxyCheckItem[];
  total: number;
  ok: number;
  failed: number;
}

export interface ProxyCheckOptions {
  proxies: string;
  protocol: 'http' | 'socks5' | 'socks5h';
  concurrency: number;
  timeout_ms: number;
}

export function checkProxies(
  options: ProxyCheckOptions,
): Promise<ProxyCheckResponse> {
  return extractFetch('/api/proxy-check', {
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
