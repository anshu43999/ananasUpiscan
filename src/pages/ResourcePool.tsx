import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  deleteResources,
  importEmailResources,
  importPhoneResources,
  importProxySeedResources,
  listResources,
  updateResourceStatus,
  type ResourcePoolItem,
} from '../api/extract';
import { BusyNotice } from '../components/BusyNotice';

type ResourceTarget = 'phone_register' | 'phone_bind' | 'proxy_seed' | 'email_icloud_api' | 'email_outlook_token' | 'email_icloud_privacy' | 'email_forwarded_domain' | 'email_cfworker';
type StatusFilter = 'all' | 'available' | 'leased' | 'used' | 'cooldown' | 'disabled';
type ProxyProtocol = 'socks5' | 'http' | 'https';
type ProxyStyle = '' | 'kookeey' | 'lajiao' | 'bestgo' | 'plain';

const targetOptions: Array<{ id: ResourceTarget; label: string; hint: string }> = [
  { id: 'email_forwarded_domain', label: 'Forwarded Domain', hint: 'Catch-all domain + IMAP mailbox for OTP' },
  { id: 'email_cfworker', label: 'CFWorker Mail', hint: 'Cloudflare Worker / Cloud Mail API mailbox' },
  { id: 'email_icloud_api', label: '邮箱接码池', hint: '邮箱注册和 OAuth 邮箱 OTP 从这里租用接码行' },
  { id: 'email_outlook_token', label: 'Outlook Token 池', hint: '保存 Outlook Graph token 邮箱资源' },
  { id: 'email_icloud_privacy', label: 'iCloud 隐私邮箱池', hint: '保存 iCloud 隐私邮箱地址资源' },
  { id: 'phone_register', label: '注册手机号池', hint: '手机注册链路从这里租用号码' },
  { id: 'phone_bind', label: '绑定手机号池', hint: 'OAuth 续跑遇到 add_phone 时从这里取号' },
  { id: 'proxy_seed', label: '代理 Seed 池', hint: '注册和 OAuth 链路生成 sticky 代理会话' },
];

const statusLabels: Record<string, string> = {
  available: '可用',
  leased: '租用中',
  used: '已使用',
  cooldown: '冷却',
  disabled: '禁用',
};

const statusClasses: Record<string, string> = {
  available: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  leased: 'border-sky-200 bg-sky-50 text-sky-700',
  used: 'border-gray-200 bg-gray-50 text-gray-600',
  cooldown: 'border-amber-200 bg-amber-50 text-amber-700',
  disabled: 'border-rose-200 bg-rose-50 text-rose-700',
};

const sampleRows = [
  '+15551234567|https://sms.example.com/latest?phone=15551234567',
  '+15557654321----https://sms.example.com/latest?phone=15557654321',
].join('\n');

const proxySampleRows = [
  'account:password@proxy.example.com:1080',
  'socks5://account:password@proxy.example.com:1080',
].join('\n');

const emailSampleRows = [
  'email@example.com----https://mail.example.com/show/token/email',
  'email@example.com----code:https://mail.example.com/api/code/token/email----mail:https://mail.example.com/api/mail/token/email',
].join('\n');

const outlookSampleRows = [
  'user@outlook.com----password----client_id----refresh_token',
].join('\n');

const forwardedDomainSampleRows = [
  'example.com----mailbox@163.com----imap_auth_code',
  'example.com----mailbox@163.com----imap_auth_code----imap.163.com----993',
].join('\n');

const cfworkerSampleRows = [
  'https://mail.example.com----admin_token----example.com',
  'https://mail.example.com----admin_token----example.com----fingerprint',
].join('\n');

function formatTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function phoneValue(item: ResourcePoolItem): string {
  return String(item.payload.phone || item.resource_key || '');
}

function smsUrlValue(item: ResourcePoolItem): string {
  return String(item.payload.sms_url || '');
}

function resourceTypeFor(target: ResourceTarget): 'phone' | 'proxy' | 'email' {
  if (target === 'proxy_seed') return 'proxy';
  if (target.startsWith('email_')) return 'email';
  return 'phone';
}

function providerFor(target: ResourceTarget): 'user_phone_url' | 'bind_user_phone_url' | 'proxy_seed' | 'icloud_api' | 'outlook_token' | 'icloud_privacy' | 'forwarded_domain' | 'cfworker_admin_api' {
  if (target === 'phone_bind') return 'bind_user_phone_url';
  if (target === 'proxy_seed') return 'proxy_seed';
  if (target === 'email_outlook_token') return 'outlook_token';
  if (target === 'email_icloud_privacy') return 'icloud_privacy';
  if (target === 'email_forwarded_domain') return 'forwarded_domain';
  if (target === 'email_cfworker') return 'cfworker_admin_api';
  if (target === 'email_icloud_api') return 'icloud_api';
  return 'user_phone_url';
}

function primaryValue(item: ResourcePoolItem): string {
  if (item.resource_type === 'proxy') return String(item.payload.url || item.resource_key || '');
  if (item.resource_type === 'email') return String(item.payload.email || item.resource_key || '');
  return phoneValue(item);
}

function secondaryValue(item: ResourcePoolItem): string {
  if (item.resource_type === 'proxy') {
    const style = String(item.payload.style || '');
    const protocol = String(item.payload.protocol || '');
    const host = String(item.payload.host || '');
    return [protocol, style, host].filter(Boolean).join(' / ');
  }
  if (item.resource_type === 'email') {
    return String(item.payload.inbox_url || item.payload.code_url || item.payload.mail_url || item.provider || '');
  }
  return smsUrlValue(item);
}

export function ResourcePool() {
  const [target, setTarget] = useState<ResourceTarget>('phone_register');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [proxyProtocol, setProxyProtocol] = useState<ProxyProtocol>('socks5');
  const [proxyStyle, setProxyStyle] = useState<ProxyStyle>('');
  const [items, setItems] = useState<ResourcePoolItem[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [importText, setImportText] = useState('');
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const busyLabel = loading ? '正在加载资源池' : working ? '正在处理资源池操作' : '';
  const selectedList = useMemo(() => [...selectedIds], [selectedIds]);
  const allVisibleSelected = items.length > 0 && items.every((item) => selectedIds.has(item.id));

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listResources({
        resource_type: resourceTypeFor(target),
        provider: providerFor(target),
        status,
        limit: 2000,
      });
      setItems(result.items);
      setCounts(result.counts);
      setSelectedIds((current) => {
        const visible = new Set(result.items.map((item) => item.id));
        return new Set([...current].filter((id) => visible.has(id)));
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : '资源池加载失败');
    } finally {
      setLoading(false);
    }
  }, [status, target]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const runAction = useCallback(async (action: () => Promise<string>) => {
    setWorking(true);
    setMessage(null);
    setError(null);
    try {
      setMessage(await action());
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : '资源池操作失败');
    } finally {
      setWorking(false);
    }
  }, [loadData]);

  const handleImport = useCallback(async () => {
    if (!importText.trim()) {
      setError('请先粘贴手机号接码数据。');
      return;
    }
    await runAction(async () => {
      const result = target === 'proxy_seed'
        ? await importProxySeedResources(importText, { protocol: proxyProtocol, style: proxyStyle })
        : resourceTypeFor(target) === 'email'
          ? await importEmailResources(importText, providerFor(target) as 'icloud_api' | 'outlook_token' | 'icloud_privacy' | 'forwarded_domain' | 'cfworker_admin_api')
          : await importPhoneResources(importText, providerFor(target) as 'user_phone_url' | 'bind_user_phone_url');
      setImportText('');
      return `已导入 ${result.imported} 条新资源，解析 ${result.total_rows} 行`;
    });
  }, [importText, proxyProtocol, proxyStyle, runAction, target]);

  const handleStatus = useCallback(async (nextStatus: string) => {
    if (selectedList.length === 0) {
      setError('请先选择资源。');
      return;
    }
    await runAction(async () => {
      const result = await updateResourceStatus(selectedList, nextStatus);
      setSelectedIds(new Set());
      return `已更新 ${result.updated} 条资源`;
    });
  }, [runAction, selectedList]);

  const handleDelete = useCallback(async () => {
    if (selectedList.length === 0) {
      setError('请先选择资源。');
      return;
    }
    await runAction(async () => {
      const result = await deleteResources(selectedList);
      setSelectedIds(new Set());
      return `已删除 ${result.deleted} 条资源`;
    });
  }, [runAction, selectedList]);

  return (
    <div className="space-y-5">
      <BusyNotice
        active={Boolean(busyLabel)}
        label={busyLabel}
        detail="资源池用于跨任务保存手机号状态，任务会按可用、租用、成功、冷却、禁用的生命周期自动回写。"
      />

      <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">资源池</h2>
            <p className="mt-1 text-sm text-gray-500">
              先导入手机号和接码 URL，再让手机注册或 OAuth 绑定链路自动租用号码。
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            {Object.entries(counts).map(([key, value]) => (
              <span key={key} className={`rounded-md border px-2.5 py-1 ${statusClasses[key] || 'border-gray-200 bg-gray-50 text-gray-600'}`}>
                {statusLabels[key] || key}: {value}
              </span>
            ))}
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.4fr)]">
        <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div>
            <span className="mb-2 block text-sm font-medium text-gray-700">导入目标</span>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              {targetOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setTarget(option.id)}
                  disabled={working}
                  className={`rounded-lg border px-3 py-2 text-left ${
                    target === option.id
                      ? 'border-gray-900 bg-gray-900 text-white'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <span className="block text-sm font-semibold">{option.label}</span>
                  <span className={`mt-1 block text-xs ${target === option.id ? 'text-gray-300' : 'text-gray-500'}`}>{option.hint}</span>
                </button>
              ))}
            </div>
          </div>

          {target === 'proxy_seed' && (
            <div className="grid gap-3 rounded-lg border border-gray-100 bg-gray-50 p-3 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">代理协议</span>
                <select
                  value={proxyProtocol}
                  onChange={(event) => setProxyProtocol(event.target.value as ProxyProtocol)}
                  disabled={working}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                >
                  <option value="socks5">SOCKS5</option>
                  <option value="http">HTTP</option>
                  <option value="https">HTTPS</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-gray-700">厂商风格</span>
                <select
                  value={proxyStyle}
                  onChange={(event) => setProxyStyle(event.target.value as ProxyStyle)}
                  disabled={working}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-100"
                >
                  <option value="">自动识别</option>
                  <option value="kookeey">Kookeey / proxy001</option>
                  <option value="lajiao">Lajiao</option>
                  <option value="bestgo">Bestgo / RRP</option>
                  <option value="plain">不改用户名</option>
                </select>
              </label>
            </div>
          )}

          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-gray-700">{target === 'proxy_seed' ? '代理 Seed 数据' : '手机号接码数据'}</span>
            <textarea
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              rows={12}
              placeholder={target === 'proxy_seed' ? proxySampleRows : target === 'email_outlook_token' ? outlookSampleRows : target === 'email_forwarded_domain' ? forwardedDomainSampleRows : target === 'email_cfworker' ? cfworkerSampleRows : target.startsWith('email_') ? emailSampleRows : sampleRows}
              disabled={working}
              className="max-h-[48vh] w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
            />
          </label>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void handleImport()}
              disabled={working || !importText.trim()}
              className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:bg-gray-300"
            >
              {working ? '处理中...' : '导入资源池'}
            </button>
            <button
              type="button"
              onClick={() => void loadData()}
              disabled={loading || working}
              className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:text-gray-300"
            >
              {loading ? '刷新中...' : '刷新列表'}
            </button>
          </div>
        </div>

        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={status}
                onChange={(event) => setStatus(event.target.value as StatusFilter)}
                disabled={loading || working}
                className="rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 disabled:bg-gray-50"
              >
                <option value="all">全部状态</option>
                <option value="available">可用</option>
                <option value="leased">租用中</option>
                <option value="used">已使用</option>
                <option value="cooldown">冷却</option>
                <option value="disabled">禁用</option>
              </select>
              <button
                type="button"
                onClick={() => setSelectedIds(allVisibleSelected ? new Set() : new Set(items.map((item) => item.id)))}
                className="rounded-md border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                {allVisibleSelected ? '取消全选' : '全选当前页'}
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => void handleStatus('available')} disabled={working || selectedList.length === 0} className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700 disabled:opacity-50">
                恢复可用
              </button>
              <button type="button" onClick={() => void handleStatus('disabled')} disabled={working || selectedList.length === 0} className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700 disabled:opacity-50">
                禁用
              </button>
              <button type="button" onClick={() => void handleDelete()} disabled={working || selectedList.length === 0} className="rounded-md border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                删除
              </button>
            </div>
          </div>

          {message && <div className="mb-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{message}</div>}
          {error && <div className="mb-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

          <div className="overflow-hidden rounded-lg border border-gray-200">
            <div className="grid grid-cols-[44px_minmax(160px,0.8fr)_minmax(220px,1.3fr)_110px_96px_96px_160px] bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-500">
              <div />
              <div>{target === 'proxy_seed' ? '代理 Seed' : '手机号'}</div>
              <div>{target === 'proxy_seed' ? '协议 / 风格 / 主机' : '接码 URL'}</div>
              <div>状态</div>
              <div>成功</div>
              <div>失败</div>
              <div>更新时间</div>
            </div>
            <div className="max-h-[56vh] overflow-auto">
              {items.length === 0 ? (
                <div className="px-3 py-10 text-center text-sm text-gray-500">暂无资源</div>
              ) : (
                items.map((item) => (
                  <label
                    key={item.id}
                    className="grid min-w-[980px] cursor-pointer grid-cols-[44px_minmax(160px,0.8fr)_minmax(220px,1.3fr)_110px_96px_96px_160px] items-center border-t border-gray-100 px-3 py-2 text-xs hover:bg-gray-50"
                  >
                    <input
                      type="checkbox"
                      checked={selectedIds.has(item.id)}
                      onChange={(event) => {
                        setSelectedIds((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(item.id);
                          else next.delete(item.id);
                          return next;
                        });
                      }}
                    />
                    <div className="min-w-0 truncate font-mono text-gray-900" title={primaryValue(item)}>{primaryValue(item)}</div>
                    <div className="min-w-0 truncate font-mono text-gray-500" title={secondaryValue(item)}>{secondaryValue(item)}</div>
                    <div>
                      <span className={`rounded-md border px-2 py-1 ${statusClasses[item.status] || 'border-gray-200 bg-gray-50 text-gray-600'}`}>
                        {statusLabels[item.status] || item.status}
                      </span>
                    </div>
                    <div className="text-gray-600">{item.success_count}</div>
                    <div className="text-gray-600">{item.fail_count}</div>
                    <div className="text-gray-500" title={item.last_error || item.cooldown_until || ''}>{formatTime(item.updated_at)}</div>
                  </label>
                ))
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
