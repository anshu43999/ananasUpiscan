import { getAuthToken, getExtractApiBase, notifyAuthExpired } from './client';

async function extractFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${getExtractApiBase()}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {}),
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
    if (res.status === 401) {
      notifyAuthExpired();
    }
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

export interface AccountLibraryItem {
  id: number;
  account_key: string;
  account_id?: string | null;
  email?: string | null;
  plan_type?: string | null;
  status: string;
  source?: string | null;
  channels: string[];
  eligibility_status: string;
  eligibility_reason?: string | null;
  eligibility?: Record<string, unknown>;
  last_checked_at?: string | null;
  health_status: string;
  health_checked_at?: string | null;
  health_source?: string | null;
  health_error?: string | null;
  health?: Record<string, unknown>;
  plus_status: string;
  plus_verified_at?: string | null;
  plus_check_source?: string | null;
  plus_check_error?: string | null;
  plus?: Record<string, unknown>;
  note?: string | null;
  has_access_token: boolean;
  access_token_preview?: string | null;
  has_password: boolean;
  has_session_json: boolean;
  created_at: string;
  updated_at: string;
}

export interface AccountLibraryDetail extends AccountLibraryItem {
  access_token?: string | null;
  password?: string | null;
  session_json?: string | null;
}

export interface AccountLibraryListResponse {
  ok: boolean;
  total: number;
  items: AccountLibraryItem[];
}

export interface AccountLibraryImportResponse {
  ok: boolean;
  imported: number;
  items: AccountLibraryItem[];
}

export interface AccountLibraryStatsResponse {
  ok: boolean;
  total: number;
  active: number;
  eligible: number;
  with_access_token: number;
  healthy: number;
  plus: number;
}

export type EmailResourceProvider = 'icloud_api' | 'outlook_token' | 'icloud_privacy' | 'forwarded_domain' | 'cfworker_admin_api';

export interface EmailRegistrationCreateOptions {
  mailbox_text?: string;
  mailbox_proxy?: string;
  use_email_resource_pool?: boolean;
  email_resource_provider?: EmailResourceProvider;
  email_resource_count?: number;
  registration_proxy?: string;
  registration_proxies?: string | string[];
  use_proxy_resource_pool?: boolean;
  proxy_resource_provider?: string;
  proxy_resource_count?: number;
  proxy_seed_region?: string;
  proxy_seed_ttl?: number;
  proxy_seed_protocol?: 'socks5' | 'http' | 'https';
  proxy_precheck_enabled?: boolean;
  proxy_precheck_timeout?: number;
  proxy_precheck_max_candidates?: number;
  proxy_precheck_max_fraud_score?: number;
  registration_retry_attempts?: number;
  concurrency?: number;
  headed?: boolean;
  chatgpt_password?: string;
  email_register_flow?: string;
  email_protocol_backend?: 'python' | 'go';
  go_email_protocol_url?: string;
  go_email_protocol_timeout_seconds?: number;
  go_email_protocol_poll_interval_ms?: number;
  browser_engine?: string;
  email_otp_timeout?: number;
  email_otp_poll_interval?: number;
  config?: Record<string, unknown>;
}

export interface EmailRegistrationSnapshot {
  job_id: string;
  status: ExtractJobStatus;
  total: number;
  completed: number;
  success: number;
  failed: number;
  logs: ExtractJobLog[];
  items: Array<{
    ok: boolean;
    email: string;
    account_id?: string | null;
    account?: AccountLibraryItem | null;
    proxy_label?: string | null;
    attempts?: number | null;
    tried_proxy_labels?: string[];
    error?: string | null;
  }>;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GoEmailBatchCreateOptions {
  count: number;
  max_concurrent?: number;
  batch_id?: string;
  go_email_protocol_url?: string;
  mailbox_provider?: EmailResourceProvider;
  proxy_seed_region?: string;
  proxy_seed_styles?: string;
  proxy_seed_ttl?: number;
  email_otp_timeout?: number;
  go_batch_timeout_seconds?: number;
  email_tries?: number;
  skip_phone?: boolean;
  config?: Record<string, unknown>;
}

export interface GoEmailBatchResponse {
  ok: boolean;
  batch_id: string;
  snapshot: Record<string, unknown>;
}

export interface PhoneRegistrationCreateOptions {
  phone_text?: string;
  sms_provider?: string;
  use_resource_pool?: boolean;
  resource_provider?: string;
  provider_count?: number;
  sms_proxy?: string;
  sms_api_key?: string;
  sms_service?: string;
  sms_country?: string;
  sms_activate_api_key?: string;
  sms_activate_country?: string;
  herosms_api_key?: string;
  herosms_service?: string;
  herosms_country?: string;
  herosms_max_price?: number | null;
  register_reuse_phone_to_max?: boolean;
  register_phone_success_max?: number;
  smsbower_api_key?: string;
  smsbower_service?: string;
  smsbower_country?: string;
  smsbower_max_price?: number | null;
  smsbower_min_price?: number | null;
  smsbower_provider_ids?: string;
  registration_proxy?: string;
  registration_proxies?: string | string[];
  use_proxy_resource_pool?: boolean;
  proxy_resource_provider?: string;
  proxy_resource_count?: number;
  proxy_seed_region?: string;
  proxy_seed_ttl?: number;
  proxy_seed_protocol?: 'socks5' | 'http' | 'https';
  proxy_precheck_enabled?: boolean;
  proxy_precheck_timeout?: number;
  proxy_precheck_max_candidates?: number;
  proxy_precheck_max_fraud_score?: number;
  registration_retry_attempts?: number;
  concurrency?: number;
  headed?: boolean;
  chatgpt_password?: string;
  browser_engine?: string;
  country_code?: string;
  country_name?: string;
  sms_timeout?: number;
  sms_poll_interval?: number;
  config?: Record<string, unknown>;
}

export interface PhoneRegistrationSnapshot {
  job_id: string;
  status: ExtractJobStatus;
  total: number;
  completed: number;
  success: number;
  failed: number;
  logs: ExtractJobLog[];
  items: Array<{
    ok: boolean;
    phone: string;
    email?: string | null;
    account_id?: string | null;
    account?: AccountLibraryItem | null;
    proxy_label?: string | null;
    attempts?: number | null;
    tried_proxy_labels?: string[];
    error?: string | null;
  }>;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface OAuthResumeCreateOptions {
  oauth_mode?: 'resume' | 'email_otp' | 'phone_bind';
  account_id?: number | null;
  account_ids?: number[];
  resume_json?: string;
  bind_email?: string;
  bind_email_text?: string;
  mailbox_proxy?: string;
  bind_email_use_resource_pool?: boolean;
  bind_email_resource_provider?: EmailResourceProvider;
  bind_sms_provider?: string;
  bind_use_resource_pool?: boolean;
  bind_resource_provider?: string;
  bind_sms_phone_url?: string;
  bind_sms_phone_urls?: string;
  bind_sms_phone_url_file?: string;
  bind_sms_proxy?: string;
  bind_sms_api_key?: string;
  bind_sms_service?: string;
  bind_sms_country?: string;
  bind_country_code?: string;
  bind_country_name?: string;
  bind_herosms_api_key?: string;
  bind_herosms_service?: string;
  bind_herosms_country?: string;
  bind_herosms_max_price?: number | null;
  bind_smsbower_api_key?: string;
  bind_smsbower_service?: string;
  bind_smsbower_country?: string;
  bind_smsbower_max_price?: number | null;
  bind_smsbower_min_price?: number | null;
  bind_smsbower_provider_ids?: string;
  bind_sms_activate_api_key?: string;
  bind_sms_activate_country?: string;
  registration_proxy?: string;
  registration_proxies?: string | string[];
  use_proxy_resource_pool?: boolean;
  proxy_resource_provider?: string;
  proxy_resource_count?: number;
  proxy_seed_region?: string;
  proxy_seed_ttl?: number;
  proxy_seed_protocol?: 'socks5' | 'http' | 'https';
  proxy_precheck_enabled?: boolean;
  proxy_precheck_timeout?: number;
  proxy_precheck_max_candidates?: number;
  proxy_precheck_max_fraud_score?: number;
  registration_retry_attempts?: number;
  concurrency?: number;
  headed?: boolean;
  chatgpt_password?: string;
  browser_engine?: string;
  email_otp_timeout?: number;
  email_otp_poll_interval?: number;
  allow_page_fallback?: boolean;
  login_identity?: string;
  redirect_uri?: string;
  client_id?: string;
  authorize_url?: string;
  config?: Record<string, unknown>;
}

export interface OAuthResumeSnapshot {
  job_id: string;
  status: ExtractJobStatus;
  total: number;
  completed: number;
  success: number;
  failed: number;
  logs: ExtractJobLog[];
  items: Array<{
    ok: boolean;
    email?: string | null;
    account_id?: string | null;
    account?: AccountLibraryItem | null;
    proxy_label?: string | null;
    attempts?: number | null;
    tried_proxy_labels?: string[];
    result?: Record<string, unknown>;
    error?: string | null;
  }>;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AccountLibraryMutateResponse {
  ok: boolean;
  updated: number;
  deleted: number;
}

export interface ResourcePoolItem {
  id: number;
  resource_type: string;
  provider: string;
  resource_key: string;
  payload: Record<string, unknown>;
  status: string;
  lease_id?: string | null;
  success_count: number;
  fail_count: number;
  cooldown_until?: string | null;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResourcePoolListResponse {
  ok: boolean;
  items: ResourcePoolItem[];
  counts: Record<string, number>;
}

export interface ResourcePoolImportResponse {
  ok: boolean;
  imported: number;
  total_rows: number;
}

export interface ResourcePoolMutateResponse {
  ok: boolean;
  updated: number;
  deleted: number;
}

export interface AccountLibraryExportTokenResponse {
  ok: boolean;
  count: number;
  text: string;
  items: AccountLibraryItem[];
}

export interface AccountLibraryCheckResponse {
  ok: boolean;
  checked: number;
  items: AccountLibraryDetail[];
}

export interface AccountLibraryHealthResponse {
  ok: boolean;
  checked: number;
  counts: Record<string, number>;
  items: AccountLibraryDetail[];
}

export interface AccountLibraryPlusVerifyResponse {
  ok: boolean;
  checked: number;
  paid: number;
  counts: Record<string, number>;
  items: AccountLibraryDetail[];
  proxy_pool_used: boolean;
  proxy_region: string;
}

export interface AccountLibraryExportJsonResponse {
  ok: boolean;
  count: number;
  items: Array<Record<string, unknown>>;
  text: string;
}

export interface AccountLibraryUpdateOptions {
  email?: string;
  password?: string;
  access_token?: string;
  session_json?: string;
  status?: string;
  note?: string;
}

export function listAccounts(
  options: { search?: string; status?: string; eligibility?: string; limit?: number } = {},
): Promise<AccountLibraryListResponse> {
  const params = new URLSearchParams();
  if (options.search) params.set('search', options.search);
  if (options.status) params.set('status', options.status);
  if (options.eligibility) params.set('eligibility', options.eligibility);
  if (options.limit) params.set('limit', String(options.limit));
  const query = params.toString();
  return extractFetch(`/api/accounts${query ? `?${query}` : ''}`);
}

export function getAccountStats(): Promise<AccountLibraryStatsResponse> {
  return extractFetch('/api/accounts/stats');
}

export function startEmailRegistrationJob(options: EmailRegistrationCreateOptions): Promise<{ job_id: string }> {
  return extractFetch('/api/email-registration/jobs', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export function getEmailRegistrationJob(jobId: string): Promise<EmailRegistrationSnapshot> {
  return extractFetch(`/api/email-registration/jobs/${jobId}`);
}

export function startGoEmailBatch(options: GoEmailBatchCreateOptions): Promise<GoEmailBatchResponse> {
  return extractFetch('/api/go-email-batches', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export function getGoEmailBatch(batchId: string, goEmailProtocolUrl = ''): Promise<GoEmailBatchResponse> {
  const params = new URLSearchParams();
  if (goEmailProtocolUrl) params.set('go_email_protocol_url', goEmailProtocolUrl);
  const query = params.toString();
  return extractFetch(`/api/go-email-batches/${encodeURIComponent(batchId)}${query ? `?${query}` : ''}`);
}

export function cancelGoEmailBatch(batchId: string, goEmailProtocolUrl = ''): Promise<GoEmailBatchResponse> {
  const params = new URLSearchParams();
  if (goEmailProtocolUrl) params.set('go_email_protocol_url', goEmailProtocolUrl);
  const query = params.toString();
  return extractFetch(`/api/go-email-batches/${encodeURIComponent(batchId)}${query ? `?${query}` : ''}`, {
    method: 'DELETE',
  });
}

export function startPhoneRegistrationJob(options: PhoneRegistrationCreateOptions): Promise<{ job_id: string }> {
  return extractFetch('/api/phone-registration/jobs', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export function getPhoneRegistrationJob(jobId: string): Promise<PhoneRegistrationSnapshot> {
  return extractFetch(`/api/phone-registration/jobs/${jobId}`);
}

export function startOAuthResumeJob(options: OAuthResumeCreateOptions): Promise<{ job_id: string }> {
  return extractFetch('/api/oauth-resume/jobs', {
    method: 'POST',
    body: JSON.stringify(options),
  });
}

export function getOAuthResumeJob(jobId: string): Promise<OAuthResumeSnapshot> {
  return extractFetch(`/api/oauth-resume/jobs/${jobId}`);
}

export function importAccounts(text: string, defaultChannel = ''): Promise<AccountLibraryImportResponse> {
  return extractFetch('/api/accounts/import', {
    method: 'POST',
    body: JSON.stringify({ text, default_channel: defaultChannel }),
  });
}

export function getAccount(accountId: number): Promise<AccountLibraryDetail> {
  return extractFetch(`/api/accounts/${accountId}`);
}

export function updateAccount(accountId: number, options: AccountLibraryUpdateOptions): Promise<AccountLibraryDetail> {
  return extractFetch(`/api/accounts/${accountId}`, {
    method: 'PUT',
    body: JSON.stringify(options),
  });
}

export function listResources(
  options: { resource_type?: string; provider?: string; status?: string; limit?: number } = {},
): Promise<ResourcePoolListResponse> {
  const params = new URLSearchParams();
  params.set('resource_type', options.resource_type || 'phone');
  if (options.provider) params.set('provider', options.provider);
  if (options.status) params.set('status', options.status);
  if (options.limit) params.set('limit', String(options.limit));
  const query = params.toString();
  return extractFetch(`/api/resources${query ? `?${query}` : ''}`);
}

export function importPhoneResources(text: string, provider: 'user_phone_url' | 'bind_user_phone_url'): Promise<ResourcePoolImportResponse> {
  return extractFetch('/api/resources/import-phone', {
    method: 'POST',
    body: JSON.stringify({ text, provider }),
  });
}

export function importProxySeedResources(
  text: string,
  options: { provider?: 'proxy_seed'; protocol?: 'socks5' | 'http' | 'https'; style?: '' | 'kookeey' | 'lajiao' | 'bestgo' | 'plain' } = {},
): Promise<ResourcePoolImportResponse> {
  return extractFetch('/api/resources/import-proxy-seeds', {
    method: 'POST',
    body: JSON.stringify({
      text,
      provider: options.provider || 'proxy_seed',
      protocol: options.protocol || 'socks5',
      style: options.style || '',
    }),
  });
}

export function importEmailResources(
  text: string,
  provider: EmailResourceProvider = 'icloud_api',
): Promise<ResourcePoolImportResponse> {
  return extractFetch('/api/resources/import-email', {
    method: 'POST',
    body: JSON.stringify({ text, provider }),
  });
}

export function updateResourceStatus(ids: number[], status: string, error = ''): Promise<ResourcePoolMutateResponse> {
  return extractFetch('/api/resources/status', {
    method: 'POST',
    body: JSON.stringify({ ids, status, error }),
  });
}

export function deleteResources(ids: number[]): Promise<ResourcePoolMutateResponse> {
  return extractFetch('/api/resources/delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

export function exportAccountTokens(ids: number[], onlyEligible = false): Promise<AccountLibraryExportTokenResponse> {
  return extractFetch('/api/accounts-bulk/export-tokens', {
    method: 'POST',
    body: JSON.stringify({ ids, only_eligible: onlyEligible }),
  });
}

export function exportAccountImportText(ids: number[]): Promise<AccountLibraryExportTokenResponse> {
  return extractFetch('/api/accounts-bulk/export-import-text', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

export function checkStoredAccountEligibility(ids: number[]): Promise<AccountLibraryCheckResponse> {
  return extractFetch('/api/accounts-bulk/check-eligibility', {
    method: 'POST',
    body: JSON.stringify({ ids, promo_id: 'plus-1-month-free', concurrency: 3 }),
  });
}

export function checkStoredAccountHealth(ids: number[]): Promise<AccountLibraryHealthResponse> {
  return extractFetch('/api/accounts-bulk/check-health', {
    method: 'POST',
    body: JSON.stringify({ ids, concurrency: 8 }),
  });
}

export function verifyStoredAccountPlus(ids: number[], proxyRegion = 'JP', useProxyPool = true, goEmailProtocolUrl = ''): Promise<AccountLibraryPlusVerifyResponse> {
  return extractFetch('/api/accounts-bulk/verify-plus', {
    method: 'POST',
    body: JSON.stringify({ ids, concurrency: 8, proxy_region: proxyRegion, use_proxy_pool: useProxyPool, go_email_protocol_url: goEmailProtocolUrl }),
  });
}

export function markStoredAccountPlus(ids: number[]): Promise<AccountLibraryMutateResponse> {
  return extractFetch('/api/accounts-bulk/mark-plus', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

export function exportAccountJson(ids: number[], includeSecrets = false): Promise<AccountLibraryExportJsonResponse> {
  return extractFetch('/api/accounts-bulk/export-json', {
    method: 'POST',
    body: JSON.stringify({ ids, include_secrets: includeSecrets }),
  });
}

export function archiveAccounts(ids: number[]): Promise<AccountLibraryMutateResponse> {
  return extractFetch('/api/accounts-bulk/archive', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

export function deleteAccounts(ids: number[]): Promise<AccountLibraryMutateResponse> {
  return extractFetch('/api/accounts-bulk/delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
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
  const authToken = getAuthToken();
  const applyToken = (rawUrl: string) => {
    if (!authToken) return rawUrl;
    const url = new URL(rawUrl);
    url.searchParams.set('token', authToken);
    return url.toString();
  };

  if (!base) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return applyToken(`${protocol}//${window.location.host}${path}`);
  }

  if (base.startsWith('http://') || base.startsWith('https://')) {
    const url = new URL(path, base);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return applyToken(url.toString());
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return applyToken(`${protocol}//${window.location.host}${base}${path}`);
}
