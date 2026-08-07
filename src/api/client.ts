const EXTRACT_API_BASE_STORAGE_KEY = 'upiscan_extract_api_base';
const AUTH_SESSION_STORAGE_KEY = 'upiscan_auth_session';
export const AUTH_EXPIRED_EVENT = 'upiscan-auth-expired';

export interface AuthUser {
  id: number;
  username: string;
  role: string;
  status: string;
  created_at?: string;
  last_login_at?: string;
}

export interface AuthSession {
  token: string;
  user: AuthUser;
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

export function getAuthSession(): AuthSession | null {
  try {
    const raw = localStorage.getItem(AUTH_SESSION_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthSession) : null;
  } catch {
    return null;
  }
}

export function getAuthToken(): string {
  return getAuthSession()?.token || '';
}

export function setAuthSession(session: AuthSession): void {
  localStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(session));
}

export function clearAuthSession(): void {
  localStorage.removeItem(AUTH_SESSION_STORAGE_KEY);
}

export function notifyAuthExpired(): void {
  clearAuthSession();
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
}
