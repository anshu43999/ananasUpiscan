const API_KEY_STORAGE_KEY = 'upiscan_api_key';
const EXTRACT_API_BASE_STORAGE_KEY = 'upiscan_extract_api_base';

export function getApiKey(): string | null {
  return sessionStorage.getItem(API_KEY_STORAGE_KEY);
}

export function setApiKey(key: string): void {
  sessionStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function getExtractApiBase(): string {
  const stored = sessionStorage.getItem(EXTRACT_API_BASE_STORAGE_KEY);
  return stored && stored !== '/paycccy' ? stored : '';
}

export function setExtractApiBase(url: string): void {
  if (url.trim()) {
    sessionStorage.setItem(EXTRACT_API_BASE_STORAGE_KEY, url.trim().replace(/\/+$/, ''));
  } else {
    sessionStorage.removeItem(EXTRACT_API_BASE_STORAGE_KEY);
  }
}
