import type { Task } from '../types';
import { StatusBadge } from './StatusBadge';
import { PAYMENT_METHOD_LABELS } from '../utils/constants';

interface Props {
  task: Task;
  isSelected?: boolean;
  onSelect: (id: string) => void;
}

export function TaskCard({ task, isSelected, onSelect }: Props) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(task.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(task.id);
        }
      }}
      className={`bg-white border rounded-lg p-4 cursor-pointer hover:shadow-md transition-all focus:outline-none focus:ring-2 focus:ring-purple-500 ${
        isSelected
          ? 'border-purple-500 ring-1 ring-purple-200'
          : 'border-gray-200 hover:border-purple-300'
      }`}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-sm font-mono text-gray-400">
            #{task.id.slice(0, 8)}
          </span>
          <span className="text-xs font-medium text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
            {PAYMENT_METHOD_LABELS[task.payment_method] || task.payment_method}
          </span>
        </div>
        <StatusBadge status={task.status} />
      </div>

      <div className="text-sm text-gray-600 mb-2">{task.account_email}</div>

      <div className="flex items-center justify-between text-xs text-gray-400">
        <span>{new Date(task.created_at).toLocaleString('zh-CN')}</span>
        <span className="font-medium text-gray-500">
          ¥{task.total_charge_cny?.toFixed(2) || '—'}
        </span>
      </div>
    </div>
  );
}
