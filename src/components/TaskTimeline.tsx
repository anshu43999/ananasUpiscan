import type { TaskStatus } from '../types';
import { STATUS_LABELS } from '../utils/constants';

const ORDER: TaskStatus[] = [
  'queued',
  'awaiting_checkout',
  'awaiting_publisher',
  'pending',
  'assigned',
  'completed',
];

interface Props {
  currentStatus: TaskStatus;
}

export function TaskTimeline({ currentStatus }: Props) {
  const currentIdx = ORDER.indexOf(currentStatus);

  return (
    <ol className="flex items-center gap-0 py-4 list-none">
      {ORDER.map((status, idx) => {
        const isActive = idx <= currentIdx || status === currentStatus;
        const isCurrent = status === currentStatus;

        return (
          <li key={status} className="flex items-center flex-1 last:flex-none">
            <div className="flex flex-col items-center">
              <div
                aria-current={isCurrent ? 'step' : undefined}
                className={`w-3 h-3 rounded-full ${
                  isActive
                    ? isCurrent
                      ? 'bg-purple-500 ring-4 ring-purple-100'
                      : 'bg-purple-500'
                    : 'bg-gray-300'
                }`}
              />
              <span
                className={`mt-1.5 text-xs whitespace-nowrap ${
                  isCurrent ? 'text-purple-700 font-medium' : 'text-gray-400'
                }`}
              >
                {STATUS_LABELS[status]}
              </span>
            </div>
            {idx < ORDER.length - 1 && (
              <div
                aria-hidden="true"
                className={`flex-1 h-0.5 mx-1 mt-[-16px] ${
                  idx < currentIdx ? 'bg-purple-500' : 'bg-gray-200'
                }`}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
