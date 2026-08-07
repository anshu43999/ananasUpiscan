import { getAuthToken, getExtractApiBase, setAuthSession, type AuthSession } from './client';

async function authFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const response = await fetch(`${getExtractApiBase()}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers as Record<string, string> | undefined),
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === 'string' ? detail : (body as { error?: string }).error || `Request failed (${response.status})`);
  }

  return response.json() as Promise<T>;
}

export interface AuthStatusResponse {
  initialized: boolean;
  registration_open: boolean;
}

export interface AuthMeResponse {
  ok: boolean;
  user: AuthSession['user'];
}

export function getAuthStatus(): Promise<AuthStatusResponse> {
  return authFetch('/api/auth/status');
}

export async function loginSystem(username: string, password: string): Promise<AuthSession> {
  const session = await authFetch<AuthSession>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  setAuthSession(session);
  return session;
}

export async function registerFirstAdmin(username: string, password: string): Promise<AuthSession> {
  const session = await authFetch<AuthSession>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  setAuthSession(session);
  return session;
}

export function getCurrentUser(): Promise<AuthMeResponse> {
  return authFetch('/api/auth/me');
}
