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
    const retryAfter = res.headers.get('Retry-After');
    const detail = (body as { detail?: unknown }).detail;
    const detailMessage =
      typeof detail === 'string'
        ? detail
        : detail && typeof detail === 'object'
          ? ((detail as { error?: { message?: string; code?: string } }).error?.message ||
            (detail as { error?: { message?: string; code?: string } }).error?.code ||
            JSON.stringify(detail))
          : undefined;
    const message =
      detailMessage ||
      (body as { message?: string }).message ||
      (body as { error?: string }).error ||
      `Request failed (${res.status})`;
    throw new Error(retryAfter ? `${message}；建议 ${retryAfter} 秒后重试` : message);
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

export interface AccountEligibilityCheckOptions {
  token: string;
  promo_id?: string;
}

export interface AccountEligibilityCheckResponse {
  token_ok: boolean;
  eligible: boolean;
  reason?: string | null;
  coupon_state?: string | null;
  promo_id?: string | null;
  status?: number | null;
  email?: string | null;
  account_id?: string | null;
  plan_type?: string | null;
  phone_number?: string | null;
  phone_verified?: boolean | null;
  reg_type?: string | null;
  jwt_expired: boolean;
  jwt_exp_ms?: number | null;
  jwt_exp_in_sec?: number | null;
  upi_eligible?: boolean | null;
  upi_eligible_reason?: string | null;
  gcash_eligible?: boolean | null;
  gcash_eligible_reason?: string | null;
  ideal_eligible?: boolean | null;
  ideal_eligible_reason?: string | null;
  error?: string | null;
}

export function checkAccountEligibility(
  options: AccountEligibilityCheckOptions,
): Promise<AccountEligibilityCheckResponse> {
  return extractFetch('/api/account-eligibility-check', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export interface ReadyPlusTaskSubmitItem {
  client_ref: string;
  session_json: unknown;
}

export type ReadyPlusChannel = 'upi' | 'kakao';

export interface ReadyPlusTaskSubmitOptions {
  channel: ReadyPlusChannel;
  items: ReadyPlusTaskSubmitItem[];
  idempotency_key?: string;
  api_key?: string;
}

export interface ReadyPlusTaskItem {
  order_id: string;
  client_ref: string;
  channel: ReadyPlusChannel | string;
  status: 'queued' | 'running' | 'reconciling' | 'succeeded' | 'failed' | 'rejected' | string;
  charged: string;
  provider_status: string;
  error_code: string;
  result?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
}

export interface ReadyPlusTaskSubmitResponse {
  ok: boolean;
  task_id?: string | null;
  status?: string | null;
  accepted: ReadyPlusTaskItem[];
  rejected: Array<{ client_ref: string; reason: string }>;
  balance?: string | null;
  idempotent_replay: boolean;
}

export interface ReadyPlusTaskDetail {
  task_id: string;
  channel: ReadyPlusChannel | string;
  source: string;
  status: string;
  requested_count: number;
  accepted_count: number;
  rejected_count: number;
  succeeded_count: number;
  failed_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  items: ReadyPlusTaskItem[];
}

export interface ReadyPlusTaskSummary {
  task_id: string;
  channel: ReadyPlusChannel | string;
  source: string;
  status: string;
  requested_count: number;
  accepted_count: number;
  rejected_count: number;
  succeeded_count: number;
  failed_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
}

export interface ReadyPlusTaskListResponse {
  ok: boolean;
  tasks: ReadyPlusTaskSummary[];
}

export interface ReadyPlusTaskDetailResponse {
  ok: boolean;
  task: ReadyPlusTaskDetail;
}

export interface ReadyPlusDownloadTokenResponse {
  ok: boolean;
  url: string;
  expires_at: number;
}

function readyPlusAuthHeaders(apiKey?: string): Record<string, string> | undefined {
  const trimmed = apiKey?.trim();
  return trimmed ? { 'X-Ready-Plus-Key': trimmed } : undefined;
}

export function submitReadyPlusTask(
  options: ReadyPlusTaskSubmitOptions,
): Promise<ReadyPlusTaskSubmitResponse> {
  const { api_key, ...payload } = options;
  return extractFetch('/api/ready-plus/tasks', {
    method: 'POST',
    headers: readyPlusAuthHeaders(api_key),
    body: JSON.stringify(payload),
  });
}

export function getReadyPlusTask(taskId: string, apiKey?: string): Promise<ReadyPlusTaskDetailResponse> {
  return extractFetch(`/api/ready-plus/tasks/${taskId}`, {
    headers: readyPlusAuthHeaders(apiKey),
  });
}

export function listReadyPlusTasks(apiKey?: string, limit = 20): Promise<ReadyPlusTaskListResponse> {
  return extractFetch(`/api/ready-plus/tasks?limit=${encodeURIComponent(String(limit))}`, {
    headers: readyPlusAuthHeaders(apiKey),
  });
}

export function getReadyPlusDownloadToken(itemId: string, apiKey?: string): Promise<ReadyPlusDownloadTokenResponse> {
  return extractFetch(`/api/ready-plus/items/${itemId}/download-token`, {
    headers: readyPlusAuthHeaders(apiKey),
  });
}

export async function downloadReadyPlusArtifact(itemId: string, token: string, apiKey?: string): Promise<Blob> {
  const res = await fetch(
    `${getExtractApiBase()}/api/ready-plus/items/${encodeURIComponent(itemId)}/download?token=${encodeURIComponent(token)}`,
    {
      headers: readyPlusAuthHeaders(apiKey),
    },
  );

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const retryAfter = res.headers.get('Retry-After');
    const detail = (body as { detail?: { error?: { message?: string; code?: string } } }).detail;
    const message = detail?.error?.message || detail?.error?.code || `Download failed (${res.status})`;
    throw new Error(retryAfter ? `${message}；建议 ${retryAfter} 秒后重试` : message);
  }

  return res.blob();
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
