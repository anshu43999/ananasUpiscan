import { useState } from 'react';
import { useTaskDetail } from '../hooks/useTaskDetail';
import { refreshCheckout, cancelTask, fetchQrStatus } from '../api/tasks';
import { StatusBadge } from '../components/StatusBadge';
import { TaskTimeline } from '../components/TaskTimeline';
import { PAYMENT_METHOD_LABELS } from '../utils/constants';

interface Props {
  taskId: string;
  onBack: () => void;
}

export function TaskDetail({ taskId, onBack }: Props) {
  const { task, loading, error, reload } = useTaskDetail(taskId);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [qrInfo, setQrInfo] = useState<string | null>(null);

  const handleRefresh = async () => {
    setActionLoading('refresh');
    setActionError(null);
    try {
      await refreshCheckout(taskId, { checkout_data: {} });
      await reload();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : '刷新失败');
    } finally {
      setActionLoading(null);
    }
  };

  const handleCancel = async () => {
    if (!window.confirm('确定要取消这个任务吗？')) return;
    setActionLoading('cancel');
    setActionError(null);
    try {
      await cancelTask(taskId);
      await reload();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : '取消失败');
    } finally {
      setActionLoading(null);
    }
  };

  const handleQrStatus = async () => {
    setActionLoading('qr');
    try {
      const result = await fetchQrStatus(taskId);
      setQrInfo(JSON.stringify(result, null, 2));
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : '查询失败');
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="text-center py-20 text-gray-400">
        <div className="animate-spin w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full mx-auto mb-3" />
        加载中...
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="text-center py-20">
        <p className="text-red-500 mb-4">{error || '任务不存在'}</p>
        <button
          onClick={onBack}
          className="px-4 py-2 text-sm text-purple-600 border border-purple-200 rounded-lg hover:bg-purple-50 transition-colors"
        >
          返回任务列表
        </button>
      </div>
    );
  }

  const canCancel = ['queued', 'awaiting_checkout', 'pending'].includes(task.status);
  const canRefresh = task.status === 'pending';

  return (
    <div className="max-w-2xl">
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={onBack}
          aria-label="返回任务列表"
          className="text-gray-400 hover:text-gray-600 transition-colors"
        >
          <svg className="w-5 h-5" aria-hidden="true" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h2 className="text-xl font-semibold text-gray-900">任务详情</h2>
        <StatusBadge status={task.status} />
      </div>

      {actionError && (
        <div role="alert" className="bg-red-50 text-red-700 text-sm rounded-lg px-4 py-3 mb-4">
          {actionError}
        </div>
      )}

      {/* Timeline */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 mb-4">
        <TaskTimeline currentStatus={task.status} />
      </div>

      {/* Task info */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 mb-4 space-y-3">
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">任务 ID</span>
          <span className="font-mono text-gray-700">{task.id}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">支付方式</span>
          <span className="text-gray-700">
            {PAYMENT_METHOD_LABELS[task.payment_method] || task.payment_method}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">收款邮箱</span>
          <span className="text-gray-700">{task.account_email}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">总费用</span>
          <span className="text-gray-700 font-medium">
            ¥{task.total_charge_cny?.toFixed(2) || '—'}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">创建时间</span>
          <span className="text-gray-700">
            {new Date(task.created_at).toLocaleString('zh-CN')}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">更新时间</span>
          <span className="text-gray-700">
            {new Date(task.updated_at).toLocaleString('zh-CN')}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-3 mb-4">
        {canRefresh && (
          <button
            onClick={handleRefresh}
            disabled={actionLoading === 'refresh'}
            className="px-4 py-2 text-sm font-medium text-purple-700 bg-purple-50 border border-purple-200 rounded-lg hover:bg-purple-100 disabled:opacity-50 transition-colors"
          >
            {actionLoading === 'refresh' ? '刷新中...' : '刷新 Checkout'}
          </button>
        )}

        <button
          onClick={handleQrStatus}
          disabled={actionLoading === 'qr'}
          className="px-4 py-2 text-sm font-medium text-gray-600 bg-gray-50 border border-gray-200 rounded-lg hover:bg-gray-100 disabled:opacity-50 transition-colors"
        >
          {actionLoading === 'qr' ? '查询中...' : '查看 QR 状态'}
        </button>

        {canCancel && (
          <button
            onClick={handleCancel}
            disabled={actionLoading === 'cancel'}
            className="px-4 py-2 text-sm font-medium text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 disabled:opacity-50 transition-colors"
          >
            {actionLoading === 'cancel' ? '取消中...' : '取消任务'}
          </button>
        )}
      </div>

      {qrInfo && (
        <div className="bg-gray-900 text-green-400 text-xs font-mono rounded-lg p-4 overflow-auto">
          <pre>{qrInfo}</pre>
        </div>
      )}

      {/* Checkout data display */}
      {task.checkout_data && (
        <div className="bg-white border border-gray-200 rounded-lg p-6">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Checkout 数据</h3>
          <div className="space-y-2 text-sm">
            {task.checkout_data.pay_link && (
              <div className="truncate">
                <span className="text-gray-500">Pay Link: </span>
                <a
                  href={task.checkout_data.pay_link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-purple-600 hover:underline"
                >
                  {task.checkout_data.pay_link}
                </a>
              </div>
            )}
            {task.checkout_data.brcode && (
              <div>
                <span className="text-gray-500">BR Code: </span>
                <span className="font-mono text-gray-700 break-all">{task.checkout_data.brcode}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
