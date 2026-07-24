export type PaymentMethod = 'upi' | 'pix';

export type TaskStatus =
  | 'queued'
  | 'awaiting_checkout'
  | 'awaiting_publisher'
  | 'pending'
  | 'assigned'
  | 'completed'
  | 'expired'
  | 'cancelled';

export interface Task {
  id: string;
  payment_method: PaymentMethod;
  account_email: string;
  status: TaskStatus;
  markup_cny: number;
  total_charge_cny: number;
  created_at: string;
  updated_at: string;
  checkout_data?: CheckoutData;
}

export interface CheckoutData {
  access_token: string;
  pay_link?: string;
  brcode?: string;
  upi_qr_png?: string;
}

export interface CapacityInfo {
  payment_method: string;
  queued_count: number;
  available_slots: number;
  accepting_new: boolean;
}

export interface CreateTaskRequest {
  payment_method: PaymentMethod;
  account_email: string;
  markup_cny?: number;
}

export interface SubmitCheckoutRequest {
  checkout_data: {
    access_token: string;
    pay_link?: string;
    brcode?: string;
    upi_qr_png?: string;
  };
}

export interface RefreshCheckoutRequest {
  checkout_data: {
    pay_link?: string;
    brcode?: string;
    access_token?: string;
  };
}

export interface ApiError {
  status: number;
  message: string;
}

// ── Link Extractor types ──

export type ExtractPaymentMethod =
  | 'hosted_checkout'
  | 'paypal'
  | 'ideal'
  | 'upi'
  | 'pix'
  | 'twint'
  | 'kakao_pay'
  | 'gopay';

export type PaymentPageMode = 'hosted' | 'custom';

export type ExtractLanguage = 'auto' | 'en' | 'zh' | 'ja' | 'ko' | 'de' | 'fr' | 'es' | 'pt' | 'nl';

export interface ProxyRegion {
  checkout: string;
  promotion: string;
  provider: string;
  approve: string;
}

export interface ProxyPreset {
  name: string;
  regions: ProxyRegion;
}

export interface ExtractJobOptions {
  access_token: string;
  payment_method: ExtractPaymentMethod;
  payment_page_mode: PaymentPageMode;
  language: ExtractLanguage;
  billing_country: string;
  proxy_chain: ProxyRegion;
  custom_export_proxy?: string;
  client_fingerprint?: 'chrome' | 'safari';
  capture_diagnostics?: boolean;
}

export type ExtractJobStatus = 'pending' | 'running' | 'completed' | 'failed';

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

export interface ExtractJob {
  job_id: string;
  status: ExtractJobStatus;
  logs: ExtractJobLog[];
  result: ExtractJobResult | null;
  error?: string;
  diagnostic_url?: string;
}

export interface ExtractConfig {
  cdk_enabled: boolean;
  cost_per_task: number;
  log_visible: boolean;
}

export interface CdkInfo {
  code: string;
  points: number;
}

export interface CdkTaskRecord {
  task_id: string;
  status: string;
  payment_method: string;
  created_at: string;
  points_used: number;
}
