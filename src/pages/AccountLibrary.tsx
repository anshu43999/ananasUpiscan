import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  archiveAccounts,
  checkStoredAccountEligibility,
  checkStoredAccountHealth,
  deleteAccounts,
  exportAccountJson,
  exportAccountTokens,
  getAccount,
  getAccountStats,
  importAccounts,
  listAccounts,
  updateAccount,
  type AccountLibraryDetail,
  type AccountLibraryItem,
  type AccountLibraryStatsResponse,
} from '../api/extract';
import type { PaymentMethod } from './LinkExtract';

type AccountLibraryProps = {
  onUseTokens: (tokens: string, paymentMethod?: PaymentMethod) => void;
};

type StatusFilter = 'active' | 'archived' | 'all';
type EligibilityFilter = '' | 'eligible' | 'not_eligible' | 'failed' | 'unknown' | 'all';

interface AccountEditForm {
  email: string;
  password: string;
  access_token: string;
  session_json: string;
  status: string;
  note: string;
}

const eligibilityLabels: Record<string, string> = {
  eligible: '可提取',
  not_eligible: '不可用',
  failed: '检测失败',
  unknown: '未检测',
};

const eligibilityClasses: Record<string, string> = {
  eligible: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  not_eligible: 'border-amber-200 bg-amber-50 text-amber-700',
  failed: 'border-rose-200 bg-rose-50 text-rose-700',
  unknown: 'border-gray-200 bg-gray-50 text-gray-600',
};

const healthLabels: Record<string, string> = {
  active: '正常',
  active_free: 'Free 正常',
  active_plus: 'Plus 正常',
  token_expired: 'Token 过期',
  invalid_token: 'Token 无效',
  missing_material: '缺少 AT',
  unknown: '未知',
};

const healthClasses: Record<string, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  active_free: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  active_plus: 'border-sky-200 bg-sky-50 text-sky-700',
  token_expired: 'border-amber-200 bg-amber-50 text-amber-700',
  invalid_token: 'border-rose-200 bg-rose-50 text-rose-700',
  missing_material: 'border-gray-200 bg-gray-50 text-gray-600',
  unknown: 'border-gray-200 bg-gray-50 text-gray-600',
};

const QUICK_EXTRACT_METHODS: Array<{ value: PaymentMethod; label: string }> = [
  { value: 'upi', label: 'UPI' },
  { value: 'ideal', label: 'iDEAL' },
  { value: 'momo', label: 'MoMo' },
  { value: 'kakao', label: 'Kakao' },
  { value: 'card', label: '直卡' },
];

function formatTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function accountTitle(account: AccountLibraryItem): string {
  return account.email || account.account_id || account.account_key || `#${account.id}`;
}

function badgeLabel(value: string, labels: Record<string, string>): string {
  return labels[value] || value || '未知';
}

function badgeClass(value: string, classes: Record<string, string>): string {
  return classes[value] || classes.unknown;
}

function accountEditForm(account: AccountLibraryDetail): AccountEditForm {
  return {
    email: account.email || '',
    password: account.password || '',
    access_token: account.access_token || '',
    session_json: account.session_json || '',
    status: account.status || 'active',
    note: account.note || '',
  };
}

export function AccountLibrary({ onUseTokens }: AccountLibraryProps) {
  const [accounts, setAccounts] = useState<AccountLibraryItem[]>([]);
  const [stats, setStats] = useState<AccountLibraryStatsResponse | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('active');
  const [eligibility, setEligibility] = useState<EligibilityFilter>('');
  const [importText, setImportText] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editLoading, setEditLoading] = useState(false);
  const [editingAccount, setEditingAccount] = useState<AccountLibraryDetail | null>(null);
  const [editForm, setEditForm] = useState<AccountEditForm>({
    email: '',
    password: '',
    access_token: '',
    session_json: '',
    status: 'active',
    note: '',
  });
  const [quickMethods, setQuickMethods] = useState<Record<number, PaymentMethod>>({});
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedAccounts = useMemo(
    () => accounts.filter((account) => selectedIds.has(account.id)),
    [accounts, selectedIds],
  );
  const selectedTokenCount = selectedAccounts.filter((account) => account.has_access_token).length;
  const allVisibleSelected = accounts.length > 0 && accounts.every((account) => selectedIds.has(account.id));

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, nextStats] = await Promise.all([
        listAccounts({ search, status, eligibility, limit: 500 }),
        getAccountStats(),
      ]);
      setAccounts(list.items);
      setStats(nextStats);
      setSelectedIds((current) => {
        const visible = new Set(list.items.map((item) => item.id));
        return new Set([...current].filter((id) => visible.has(id)));
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : '账号库加载失败');
    } finally {
      setLoading(false);
    }
  }, [eligibility, search, status]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const selectedIdList = useCallback(() => [...selectedIds], [selectedIds]);

  const runAction = useCallback(async (action: () => Promise<string>) => {
    setWorking(true);
    setError(null);
    setMessage(null);
    try {
      setMessage(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    } finally {
      setWorking(false);
    }
  }, []);

  const handleImport = useCallback(async () => {
    if (!importText.trim()) {
      setError('请先粘贴账号、Access Token 或 Session JSON。');
      return;
    }
    await runAction(async () => {
      const result = await importAccounts(importText);
      setImportText('');
      setImportOpen(false);
      await loadData();
      return `已导入/更新 ${result.imported} 个账号`;
    });
  }, [importText, loadData, runAction]);

  const handleExportTokens = useCallback(async (useEligibleOnly: boolean, fillExtract: boolean) => {
    await runAction(async () => {
      const result = await exportAccountTokens(selectedIdList(), useEligibleOnly);
      if (!result.text.trim()) return '没有可用 Access Token';
      if (fillExtract) {
        onUseTokens(result.text);
        return `已将 ${result.count} 个 AT 加入链接提取`;
      }
      await navigator.clipboard.writeText(result.text);
      return `已复制 ${result.count} 个 AT`;
    });
  }, [onUseTokens, runAction, selectedIdList]);

  const handleExportJson = useCallback(async () => {
    await runAction(async () => {
      const result = await exportAccountJson(selectedIdList(), false);
      await navigator.clipboard.writeText(result.text);
      return `已复制 ${result.count} 个账号 JSON 摘要`;
    });
  }, [runAction, selectedIdList]);

  const handleQuickExtract = useCallback(async (account: AccountLibraryItem, paymentMethod: PaymentMethod) => {
    if (!account.has_access_token) {
      setError('这个账号没有 Access Token，无法加入提取。');
      return;
    }
    await runAction(async () => {
      const result = await exportAccountTokens([account.id], false);
      if (!result.text.trim()) return '这个账号没有可用 Access Token';
      onUseTokens(result.text, paymentMethod);
      return `已将 ${accountTitle(account)} 加入 ${QUICK_EXTRACT_METHODS.find((item) => item.value === paymentMethod)?.label || paymentMethod} 提炼`;
    });
  }, [onUseTokens, runAction]);

  const quickMethodFor = useCallback((accountId: number): PaymentMethod => {
    return quickMethods[accountId] || 'upi';
  }, [quickMethods]);

  const handleEditOpen = useCallback(async (account: AccountLibraryItem) => {
    setEditLoading(true);
    setError(null);
    try {
      const detail = await getAccount(account.id);
      setEditingAccount(detail);
      setEditForm(accountEditForm(detail));
      setEditOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : '账号信息加载失败');
    } finally {
      setEditLoading(false);
    }
  }, []);

  const handleEditSave = useCallback(async () => {
    if (!editingAccount) return;
    await runAction(async () => {
      const saved = await updateAccount(editingAccount.id, {
        email: editForm.email,
        password: editForm.password,
        access_token: editForm.access_token,
        session_json: editForm.session_json,
        status: editForm.status,
        note: editForm.note,
      });
      setEditOpen(false);
      setEditingAccount(null);
      await loadData();
      return `已更新 ${accountTitle(saved)}`;
    });
  }, [editForm, editingAccount, loadData, runAction]);

  const handleEligibilityCheck = useCallback(async () => {
    const ids = selectedIdList();
    if (ids.length === 0) {
      setError('请先选择要检测的账号。');
      return;
    }
    await runAction(async () => {
      const result = await checkStoredAccountEligibility(ids);
      await loadData();
      return `已检测 ${result.checked} 个账号资格`;
    });
  }, [loadData, runAction, selectedIdList]);

  const handleHealthCheck = useCallback(async () => {
    const ids = selectedIdList();
    if (ids.length === 0) {
      setError('请先选择要健康检测的账号。');
      return;
    }
    await runAction(async () => {
      const result = await checkStoredAccountHealth(ids);
      await loadData();
      const summary = Object.entries(result.counts)
        .map(([name, count]) => `${badgeLabel(name, healthLabels)} ${count}`)
        .join('，');
      return `已健康检测 ${result.checked} 个账号${summary ? `：${summary}` : ''}`;
    });
  }, [loadData, runAction, selectedIdList]);

  const handleArchive = useCallback(async () => {
    const ids = selectedIdList();
    if (ids.length === 0) return;
    await runAction(async () => {
      const result = await archiveAccounts(ids);
      setSelectedIds(new Set());
      await loadData();
      return `已归档 ${result.updated} 个账号`;
    });
  }, [loadData, runAction, selectedIdList]);

  const handleDelete = useCallback(async () => {
    const ids = selectedIdList();
    if (ids.length === 0) return;
    if (!window.confirm(`确定删除 ${ids.length} 个账号？此操作不可恢复。`)) return;
    await runAction(async () => {
      const result = await deleteAccounts(ids);
      setSelectedIds(new Set());
      await loadData();
      return `已删除 ${result.deleted} 个账号`;
    });
  }, [loadData, runAction, selectedIdList]);

  const toggleSelected = useCallback((id: number) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAllVisible = useCallback(() => {
    setSelectedIds((current) => {
      if (accounts.length > 0 && accounts.every((account) => current.has(account.id))) return new Set();
      return new Set(accounts.map((account) => account.id));
    });
  }, [accounts]);

  return (
    <div className="space-y-5">
      <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">账号库</h2>
            <p className="mt-1 text-sm text-gray-500">管理 ChatGPT 账号、AT 健康状态和支付资格。</p>
          </div>
          <div className="flex flex-wrap items-start justify-end gap-3">
            <div className="grid grid-cols-2 gap-2 text-right sm:grid-cols-5">
              <Metric label="总数" value={stats?.total ?? 0} />
              <Metric label="可用" value={stats?.active ?? 0} />
              <Metric label="有 AT" value={stats?.with_access_token ?? 0} />
              <Metric label="支付可用" value={stats?.eligible ?? 0} tone="emerald" />
              <Metric label="AT 健康" value={stats?.healthy ?? 0} tone="sky" />
            </div>
            <button
              type="button"
              onClick={() => setImportOpen(true)}
              className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800"
            >
              导入账号
            </button>
          </div>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索邮箱、账号 ID、备注"
              className="min-w-64 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
            />
            <select value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)} className="rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500">
              <option value="active">可用账号</option>
              <option value="archived">已归档</option>
              <option value="all">全部状态</option>
            </select>
            <select value={eligibility} onChange={(event) => setEligibility(event.target.value as EligibilityFilter)} className="rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500">
              <option value="">全部支付资格</option>
              <option value="eligible">可提取</option>
              <option value="not_eligible">不可用</option>
              <option value="failed">检测失败</option>
              <option value="unknown">未检测</option>
            </select>
            <button type="button" onClick={() => void loadData()} disabled={loading} className="rounded-md border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:text-gray-300">
              {loading ? '刷新中...' : '刷新'}
            </button>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-gray-200 bg-gray-50 px-3 py-2">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={allVisibleSelected} onChange={toggleAllVisible} />
              已选 {selectedIds.size} 个，含 AT {selectedTokenCount} 个
            </label>
            <div className="flex flex-wrap gap-2">
              <ActionButton onClick={() => void handleEligibilityCheck()} disabled={working || selectedIds.size === 0} tone="emerald">支付资格</ActionButton>
              <ActionButton onClick={() => void handleHealthCheck()} disabled={working || selectedIds.size === 0} tone="sky">AT 健康</ActionButton>
              <ActionButton onClick={() => void handleExportTokens(false, true)} disabled={working || selectedIds.size === 0}>加入提取</ActionButton>
              <ActionButton onClick={() => void handleExportTokens(true, true)} disabled={working || selectedIds.size === 0}>仅通过加入</ActionButton>
              <ActionButton onClick={() => void handleExportTokens(false, false)} disabled={working || selectedIds.size === 0}>复制 AT</ActionButton>
              <ActionButton onClick={() => void handleExportJson()} disabled={working || selectedIds.size === 0}>导出 JSON</ActionButton>
              <ActionButton onClick={() => void handleArchive()} disabled={working || selectedIds.size === 0}>归档</ActionButton>
              <ActionButton onClick={() => void handleDelete()} disabled={working || selectedIds.size === 0} tone="rose">删除</ActionButton>
            </div>
          </div>

          {error && <div className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}
          {message && <div className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{message}</div>}

          <div className="overflow-x-auto rounded-md border border-gray-200">
            <div className="grid min-w-[980px] grid-cols-[36px_minmax(260px,1.7fr)_112px_120px_136px_230px] border-b border-gray-200 bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-500">
              <div />
              <div>账号</div>
              <div>AT 健康</div>
              <div>支付资格</div>
              <div>更新时间</div>
              <div>提炼链路</div>
            </div>
            <div className="max-h-[64vh] min-h-[320px] min-w-[980px] overflow-y-auto">
              {accounts.length === 0 ? (
                <div className="px-4 py-12 text-center text-sm text-gray-500">暂无账号。</div>
              ) : (
                accounts.map((account) => (
                  <div key={account.id} className="grid grid-cols-[36px_minmax(260px,1.7fr)_112px_120px_136px_230px] items-center border-b border-gray-100 px-3 py-2.5 text-sm hover:bg-gray-50">
                    <div>
                      <input type="checkbox" checked={selectedIds.has(account.id)} onChange={() => toggleSelected(account.id)} />
                    </div>
                    <div className="min-w-0">
                      <div className="truncate font-medium text-gray-900">{accountTitle(account)}</div>
                      <div className="mt-0.5 truncate font-mono text-xs text-gray-500">
                        {account.access_token_preview || '无 AT'} · {account.plan_type || 'plan unknown'}
                      </div>
                      {account.note && <div className="mt-0.5 truncate text-xs text-gray-400">{account.note}</div>}
                    </div>
                    <StatusBadge value={account.health_status} labels={healthLabels} classes={healthClasses} message={account.health_error} />
                    <StatusBadge value={account.eligibility_status} labels={eligibilityLabels} classes={eligibilityClasses} message={account.eligibility_reason} />
                    <div className="text-xs text-gray-500">{formatTime(account.updated_at)}</div>
                    <div className="flex items-center gap-1.5">
                      <select
                        value={quickMethodFor(account.id)}
                        onChange={(event) => {
                          const value = event.target.value as PaymentMethod;
                          setQuickMethods((current) => ({ ...current, [account.id]: value }));
                        }}
                        disabled={working || !account.has_access_token}
                        className="h-8 min-w-0 flex-1 rounded-md border border-gray-200 bg-white px-2 text-xs text-gray-700 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50 disabled:text-gray-300"
                      >
                        {QUICK_EXTRACT_METHODS.map((method) => (
                          <option key={`${account.id}-${method.value}`} value={method.value}>
                            {method.label}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => void handleQuickExtract(account, quickMethodFor(account.id))}
                        disabled={working || !account.has_access_token}
                        className="h-8 rounded-md border border-gray-200 px-2.5 text-xs font-medium text-gray-700 hover:bg-white disabled:cursor-not-allowed disabled:text-gray-300"
                      >
                        使用
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleEditOpen(account)}
                        disabled={working || editLoading}
                        className="h-8 rounded-md border border-gray-200 px-2.5 text-xs font-medium text-gray-700 hover:bg-white disabled:cursor-not-allowed disabled:text-gray-300"
                      >
                        编辑
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
      </section>

      {importOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/45 px-4 py-6">
          <div className="w-full max-w-3xl rounded-lg border border-gray-200 bg-white shadow-xl">
            <div className="flex items-start justify-between gap-4 border-b border-gray-200 px-5 py-4">
              <div>
                <h3 className="text-base font-semibold text-gray-900">导入账号</h3>
                <p className="mt-1 text-xs leading-5 text-gray-500">
                  支持一行一个 AT、Session JSON、数组 JSON、邮箱四段格式。导入时会从 JWT 中解析邮箱、账号 ID 和套餐。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setImportOpen(false)}
                disabled={working}
                className="rounded-md border border-gray-200 px-2.5 py-1 text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:text-gray-300"
              >
                关闭
              </button>
            </div>
            <div className="space-y-4 px-5 py-4">
              <textarea
                value={importText}
                onChange={(event) => setImportText(event.target.value)}
                rows={14}
                placeholder="粘贴 AT / Session JSON / email----password----...----access_token"
                className="max-h-[56vh] w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
              />
              <div className="flex flex-wrap items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setImportOpen(false)}
                  disabled={working}
                  className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:text-gray-300"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void handleImport()}
                  disabled={working || !importText.trim()}
                  className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:bg-gray-300"
                >
                  {working ? '处理中...' : '导入账号库'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {editOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/45 px-4 py-6">
          <div className="w-full max-w-4xl rounded-lg border border-gray-200 bg-white shadow-xl">
            <div className="flex items-start justify-between gap-4 border-b border-gray-200 px-5 py-4">
              <div className="min-w-0">
                <h3 className="text-base font-semibold text-gray-900">更新账号信息</h3>
                <p className="mt-1 truncate text-xs text-gray-500">
                  {editingAccount ? accountTitle(editingAccount) : '-'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setEditOpen(false)}
                disabled={working}
                className="rounded-md border border-gray-200 px-2.5 py-1 text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:text-gray-300"
              >
                关闭
              </button>
            </div>
            <div className="max-h-[76vh] overflow-y-auto px-5 py-4">
              <div className="grid gap-4 md:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">邮箱</span>
                  <input
                    value={editForm.email}
                    onChange={(event) => setEditForm((current) => ({ ...current, email: event.target.value }))}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">账号状态</span>
                  <select
                    value={editForm.status}
                    onChange={(event) => setEditForm((current) => ({ ...current, status: event.target.value }))}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                  >
                    <option value="active">可用</option>
                    <option value="archived">归档</option>
                    <option value="disabled">停用</option>
                  </select>
                </label>
              </div>

              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">密码</span>
                  <input
                    value={editForm.password}
                    onChange={(event) => setEditForm((current) => ({ ...current, password: event.target.value }))}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-gray-700">备注</span>
                  <input
                    value={editForm.note}
                    onChange={(event) => setEditForm((current) => ({ ...current, note: event.target.value }))}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                  />
                </label>
              </div>

              <label className="mt-4 block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">Access Token</span>
                <textarea
                  value={editForm.access_token}
                  onChange={(event) => setEditForm((current) => ({ ...current, access_token: event.target.value }))}
                  rows={7}
                  className="max-h-[34vh] w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                />
              </label>

              <label className="mt-4 block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">Session JSON</span>
                <textarea
                  value={editForm.session_json}
                  onChange={(event) => setEditForm((current) => ({ ...current, session_json: event.target.value }))}
                  rows={5}
                  placeholder="可选，粘贴包含 accessToken 的 Session JSON"
                  className="max-h-[28vh] w-full resize-y rounded-md border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                />
              </label>

              <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-gray-100 pt-4">
                <button
                  type="button"
                  onClick={() => setEditOpen(false)}
                  disabled={working}
                  className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:text-gray-300"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void handleEditSave()}
                  disabled={working || !editingAccount}
                  className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:bg-gray-300"
                >
                  {working ? '保存中...' : '保存更新'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, tone = 'gray' }: { label: string; value: number; tone?: 'gray' | 'emerald' | 'sky' }) {
  const colors = {
    gray: 'border-gray-200 text-gray-500 text-gray-900',
    emerald: 'border-emerald-200 text-emerald-600 text-emerald-700',
    sky: 'border-sky-200 text-sky-600 text-sky-700',
  }[tone];
  const [border, labelColor, valueColor] = colors.split(' ');
  return (
    <div className={`rounded-md border ${border} px-3 py-2`}>
      <div className={`text-xs ${labelColor}`}>{label}</div>
      <div className={`font-semibold ${valueColor}`}>{value}</div>
    </div>
  );
}

function StatusBadge({
  value,
  labels,
  classes,
  message,
}: {
  value: string;
  labels: Record<string, string>;
  classes: Record<string, string>;
  message?: string | null;
}) {
  return (
    <div>
      <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${badgeClass(value, classes)}`}>
        {badgeLabel(value, labels)}
      </span>
      {message && <div className="mt-1 truncate text-xs text-gray-400">{message}</div>}
    </div>
  );
}

function ActionButton({
  children,
  disabled,
  onClick,
  tone = 'gray',
}: {
  children: string;
  disabled?: boolean;
  onClick: () => void;
  tone?: 'gray' | 'emerald' | 'sky' | 'rose';
}) {
  const cls = {
    gray: 'border-gray-200 text-gray-700 hover:bg-white disabled:text-gray-300',
    emerald: 'border-emerald-200 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-gray-300',
    sky: 'border-sky-200 bg-sky-600 text-white hover:bg-sky-700 disabled:bg-gray-300',
    rose: 'border-rose-200 text-rose-700 hover:bg-rose-50 disabled:text-gray-300',
  }[tone];
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={`rounded-md border px-3 py-1.5 text-xs font-medium disabled:cursor-not-allowed ${cls}`}>
      {children}
    </button>
  );
}
