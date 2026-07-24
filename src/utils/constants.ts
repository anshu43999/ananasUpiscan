export const API_BASE = 'https://foarge.com/api/publisher/v1';

export const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  awaiting_checkout: '待提交',
  awaiting_publisher: '待发布',
  pending: '处理中',
  assigned: '已分配',
  completed: '已完成',
  expired: '已过期',
  cancelled: '已取消',
};

export const STATUS_COLORS: Record<string, string> = {
  queued: 'bg-yellow-100 text-yellow-800',
  awaiting_checkout: 'bg-blue-100 text-blue-800',
  awaiting_publisher: 'bg-cyan-100 text-cyan-800',
  pending: 'bg-purple-100 text-purple-800',
  assigned: 'bg-indigo-100 text-indigo-800',
  completed: 'bg-green-100 text-green-800',
  expired: 'bg-red-100 text-red-800',
  cancelled: 'bg-gray-100 text-gray-800',
};

export const PAYMENT_METHOD_LABELS: Record<string, string> = {
  upi: 'UPI',
  pix: 'PIX',
};

// ── Link Extractor constants ──

export const EXTRACT_PAYMENT_METHODS = [
  { value: 'hosted_checkout' as const, label: 'Hosted Checkout' },
  { value: 'paypal' as const, label: 'PayPal' },
  { value: 'ideal' as const, label: 'iDEAL' },
  { value: 'upi' as const, label: 'UPI' },
  { value: 'pix' as const, label: 'PIX' },
  { value: 'twint' as const, label: 'TWINT' },
  { value: 'kakao_pay' as const, label: 'Kakao Pay' },
  { value: 'gopay' as const, label: 'GoPay' },
];

export const PAGE_MODES = [
  { value: 'hosted' as const, label: 'Hosted: pay.openai.com' },
  { value: 'custom' as const, label: 'Custom: Payment Element' },
];

export const EXTRACT_LANGUAGES = [
  { value: 'auto' as const, label: '自动检测' },
  { value: 'en' as const, label: 'English' },
  { value: 'zh' as const, label: '中文' },
  { value: 'ja' as const, label: '日本語' },
  { value: 'ko' as const, label: '한국어' },
  { value: 'de' as const, label: 'Deutsch' },
  { value: 'fr' as const, label: 'Français' },
  { value: 'es' as const, label: 'Español' },
  { value: 'pt' as const, label: 'Português' },
  { value: 'nl' as const, label: 'Nederlands' },
];

export const BILLING_COUNTRIES = [
  { code: 'AU', currency: 'AUD', name: '澳大利亚' },
  { code: 'BR', currency: 'BRL', name: '巴西' },
  { code: 'CA', currency: 'CAD', name: '加拿大' },
  { code: 'CH', currency: 'CHF', name: '瑞士' },
  { code: 'DE', currency: 'EUR', name: '德国' },
  { code: 'DK', currency: 'DKK', name: '丹麦' },
  { code: 'ES', currency: 'EUR', name: '西班牙' },
  { code: 'FR', currency: 'EUR', name: '法国' },
  { code: 'GB', currency: 'GBP', name: '英国' },
  { code: 'HK', currency: 'HKD', name: '中国香港' },
  { code: 'ID', currency: 'IDR', name: '印度尼西亚' },
  { code: 'IN', currency: 'INR', name: '印度' },
  { code: 'JP', currency: 'JPY', name: '日本' },
  { code: 'KR', currency: 'KRW', name: '韩国' },
  { code: 'MX', currency: 'MXN', name: '墨西哥' },
  { code: 'MY', currency: 'MYR', name: '马来西亚' },
  { code: 'NL', currency: 'EUR', name: '荷兰' },
  { code: 'NO', currency: 'NOK', name: '挪威' },
  { code: 'PH', currency: 'PHP', name: '菲律宾' },
  { code: 'PL', currency: 'PLN', name: '波兰' },
  { code: 'SE', currency: 'SEK', name: '瑞典' },
  { code: 'SG', currency: 'SGD', name: '新加坡' },
  { code: 'TH', currency: 'THB', name: '泰国' },
  { code: 'US', currency: 'USD', name: '美国' },
  { code: 'VN', currency: 'VND', name: '越南' },
];

export const PROXY_REGIONS = [
  'JP', 'NL', 'US', 'DE', 'FR', 'GB', 'SG', 'HK', 'KR', 'BR', 'IN', 'AU', 'CA', 'MX',
  'IT', 'ES', 'SE', 'CH', 'PL', 'NO', 'DK', 'ID', 'MY', 'TH', 'VN', 'PH', 'AT', 'BE',
  'FI', 'IE', 'PT', 'CZ', 'RO', 'BG', 'HR', 'SK', 'LT', 'LV', 'EE', 'LU', 'MT', 'CY',
];

export const PROXY_PRESETS = [
  // Strategy-based
  { name: '双链路 JP→NL→NL + NL→NL→NL', strategy: 'dual' as const },

  // Region-based
  {
    name: '日本 JP → 荷兰 NL → 荷兰 NL',
    regions: { checkout: 'JP', promotion: 'NL', provider: 'NL', approve: 'NL' },
  },
  {
    name: '荷兰 NL → 荷兰 NL → 荷兰 NL',
    regions: { checkout: 'NL', promotion: 'NL', provider: 'NL', approve: 'NL' },
  },
  {
    name: '美国 US → 美国 US → 美国 US',
    regions: { checkout: 'US', promotion: 'US', provider: 'US', approve: 'US' },
  },
  {
    name: '日本 JP → 美国 US → 美国 US',
    regions: { checkout: 'JP', promotion: 'US', provider: 'US', approve: 'US' },
  },
  {
    name: 'JP -> US -> approve US',
    regions: { checkout: 'JP', promotion: 'US', provider: 'US', approve: 'US' },
  },
  {
    name: '日本 JP → 美国 US → approve 日本 JP',
    regions: { checkout: 'JP', promotion: 'US', provider: 'US', approve: 'JP' },
  },
  {
    name: '日本 JP → 日本 JP → 日本 JP',
    regions: { checkout: 'JP', promotion: 'JP', provider: 'JP', approve: 'JP' },
  },
  {
    name: '美国 US → 日本 JP → 日本 JP',
    regions: { checkout: 'US', promotion: 'JP', provider: 'JP', approve: 'JP' },
  },

  // Strategy-based
  { name: 'Matrix 8 combos', strategy: 'matrix_8' as const },
  { name: 'Sequential 8 combos', strategy: 'sequential_8' as const },
  { name: '随机 JP/TR/BR/VN → 随机全部地区', strategy: 'random' as const },
];
