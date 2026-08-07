import { useCallback, useMemo, useState } from 'react';
import { loginSystem, registerFirstAdmin } from '../api/auth';
import type { AuthSession } from '../api/client';

interface AuthPageProps {
  registrationOpen: boolean;
  onAuthenticated: (session: AuthSession) => void;
}

export function AuthPage({ registrationOpen, onAuthenticated }: AuthPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const modeLabel = registrationOpen ? '初始化管理员' : '系统登录';
  const disabled = busy || username.trim().length < 3 || password.length < 8 || (registrationOpen && password !== confirmPassword);
  const helper = useMemo(() => {
    if (registrationOpen) return '当前系统还没有管理员。第一个注册成功的账号会成为 admin，注册完成后不再开放新用户注册。';
    return '系统已完成初始化。请使用管理员账号登录，登录信息会保存在当前浏览器中。';
  }, [registrationOpen]);

  const handleSubmit = useCallback(async () => {
    if (disabled) return;
    setBusy(true);
    setError('');
    try {
      const session = registrationOpen
        ? await registerFirstAdmin(username.trim(), password)
        : await loginSystem(username.trim(), password);
      onAuthenticated(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : '认证失败');
    } finally {
      setBusy(false);
    }
  }, [disabled, onAuthenticated, password, registrationOpen, username]);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto grid min-h-screen max-w-6xl px-5 py-8 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-center lg:gap-12">
        <section className="hidden lg:block">
          <div className="max-w-2xl">
            <div className="mb-5 inline-flex rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-1 text-xs font-semibold text-emerald-200">
              UPIScan Access Console
            </div>
            <h1 className="text-4xl font-bold tracking-tight text-white">受控入口</h1>
            <p className="mt-4 max-w-xl text-sm leading-7 text-slate-300">
              登录后才能访问链接提取、账号库、资源池、注册和 OAuth 续跑。认证状态保存在浏览器本地，刷新页面会自动恢复。
            </p>
            <div className="mt-8 grid max-w-xl gap-3 sm:grid-cols-3">
              {['API 受保护', '首个账号为 admin', '关闭公开注册'].map((item) => (
                <div key={item} className="rounded-lg border border-white/10 bg-white/[0.04] px-4 py-3">
                  <div className="text-sm font-semibold text-white">{item}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="flex min-h-[calc(100vh-4rem)] items-center lg:min-h-0">
          <div className="w-full rounded-xl border border-white/10 bg-white px-5 py-5 text-slate-950 shadow-2xl">
            <div className="mb-5">
              <h2 className="text-xl font-bold">{modeLabel}</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">{helper}</p>
            </div>

            <div className="space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold text-slate-700">用户名</span>
                <input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                  placeholder="admin"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-semibold text-slate-700">密码</span>
                <input
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  autoComplete={registrationOpen ? 'new-password' : 'current-password'}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                  placeholder="至少 8 个字符"
                />
              </label>
              {registrationOpen && (
                <label className="block">
                  <span className="mb-1.5 block text-sm font-semibold text-slate-700">确认密码</span>
                  <input
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    type="password"
                    autoComplete="new-password"
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                    placeholder="再次输入密码"
                  />
                </label>
              )}
            </div>

            {error && (
              <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {error}
              </div>
            )}

            <button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={disabled}
              className="mt-5 w-full rounded-lg bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {busy ? '处理中...' : registrationOpen ? '创建 admin 并进入系统' : '登录'}
            </button>

            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-500">
              登录态保存在此浏览器的 localStorage。公共电脑使用后请点击退出登录。
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
