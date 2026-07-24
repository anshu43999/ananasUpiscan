import { API_BASE } from '../utils/constants';

const API_KEY_STORAGE_KEY = 'upiscan_api_key';
const API_BASE_STORAGE_KEY = 'upiscan_api_base';
const EXTRACT_API_BASE_STORAGE_KEY = 'upiscan_extract_api_base';
const REQUEST_TIMEOUT_MS = 15000;

function getApiKey(): string | null {
  return sessionStorage.getItem(API_KEY_STORAGE_KEY);
}

export function setApiKey(key: string): void {
  sessionStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function clearApiKey(): void {
  sessionStorage.removeItem(API_KEY_STORAGE_KEY);
}

export function hasApiKey(): boolean {
  return !!getApiKey();
}

// ── Task management API base ──

export function getApiBase(): string {
  return sessionStorage.getItem(API_BASE_STORAGE_KEY) || API_BASE;
}

export function setApiBase(url: string): void {
  sessionStorage.setItem(API_BASE_STORAGE_KEY, url);
}

export function hasApiBase(): boolean {
  return !!sessionStorage.getItem(API_BASE_STORAGE_KEY);
}

// ── Link extraction API base ──

export function getExtractApiBase(): string {
  const stored = sessionStorage.getItem(EXTRACT_API_BASE_STORAGE_KEY);
  return stored && stored !== '/paycccy' ? stored : '';
}

export function setExtractApiBase(url: string): void {
  if (url.trim()) {
    sessionStorage.setItem(EXTRACT_API_BASE_STORAGE_KEY, url.trim());
  } else {
    sessionStorage.removeItem(EXTRACT_API_BASE_STORAGE_KEY);
  }
}

export function hasExtractApiBase(): boolean {
  return !!sessionStorage.getItem(EXTRACT_API_BASE_STORAGE_KEY);
}

class ApiClientError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiClientError';
  }
}

export async function apiClient<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const apiKey = getApiKey();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(`${getApiBase()}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const message = body.message || body.error || `请求失败 (${res.status})`;

      if (res.status === 401) {
        clearApiKey();
        window.dispatchEvent(new CustomEvent('upiscan:401'));
      }

      throw new ApiClientError(res.status, message);
    }

    return res.json();
  } finally {
    clearTimeout(timeoutId);
  }
}
