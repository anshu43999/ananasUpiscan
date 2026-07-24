import { useState, useMemo } from 'react';
import { TaskCard } from '../components/TaskCard';
import { useTaskList } from '../hooks/useTaskList';
import type { TaskStatus } from '../types';
import { STATUS_LABELS } from '../utils/constants';

interface Props {
  onSelectTask: (id: string) => void;
  onNewTask: () => void;
  selectedTaskId: string | null;
}

const FILTERS: Array<{ value: TaskStatus | 'all'; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'queued', label: '排队中' },
  { value: 'awaiting_checkout', label: '待提交' },
  { value: 'pending', label: '处理中' },
  { value: 'assigned', label: '已分配' },
  { value: 'completed', label: '已完成' },
];

export function Dashboard({ onSelectTask, onNewTask, selectedTaskId }: Props) {
  const { tasks, loading, error, reload } = useTaskList();
  const [filter, setFilter] = useState<TaskStatus | 'all'>('all');

  const filteredTasks = useMemo(() => {
    if (filter === 'all') return tasks;
    return tasks.filter((t) => t.status === filter);
  }, [tasks, filter]);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-900">任务列表</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={onNewTask}
            className="px-3 py-1.5 text-sm font-medium text-white bg-purple-600 rounded-lg hover:bg-purple-700 transition-colors"
          >
            + 新建
          </button>
          <button
            onClick={reload}
            className="px-3 py-1.5 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-100 transition-colors"
          >
            刷新
          </button>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1.5 mb-4 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            aria-pressed={filter === f.value}
            className={`px-2.5 py-1 text-xs rounded-lg transition-colors ${
              filter === f.value
                ? 'bg-purple-100 text-purple-700 font-medium'
                : 'text-gray-500 hover:bg-gray-100'
            }`}
          >
            {f.label}
            {f.value !== 'all' && (
              <span className="ml-1 text-gray-400">
                {tasks.filter((t) => t.status === f.value).length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading && tasks.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          <div className="animate-spin w-6 h-6 border-2 border-purple-500 border-t-transparent rounded-full mx-auto mb-2" />
          加载中...
        </div>
      )}

      {error && (
        <div role="alert" className="bg-red-50 text-red-700 text-xs rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      )}

      {!loading && filteredTasks.length === 0 && (
        <div className="text-center py-12 text-gray-400 text-sm">
          {filter === 'all' ? '暂无任务，去提交一个账号吧' : `没有${STATUS_LABELS[filter]}的任务`}
        </div>
      )}

      <div className="grid gap-2">
        {filteredTasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            isSelected={task.id === selectedTaskId}
            onSelect={onSelectTask}
          />
        ))}
      </div>
    </div>
  );
}
