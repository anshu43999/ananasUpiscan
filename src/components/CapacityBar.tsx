interface Props {
  queuedCount: number;
  availableSlots: number;
  acceptingNew: boolean;
}

export function CapacityBar({ queuedCount, availableSlots, acceptingNew }: Props) {
  const total = queuedCount + availableSlots;
  const percent = total > 0 ? Math.round((queuedCount / total) * 100) : 0;

  return (
    <div className="flex items-center gap-3 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-gray-500">队列容量</span>
        <div
          role="progressbar"
          aria-valuenow={queuedCount}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-label={`队列容量 ${queuedCount}/${total}`}
          className="flex h-2 w-24 rounded-full bg-gray-200 overflow-hidden"
        >
          <div
            className="h-full bg-purple-500 transition-all"
            style={{ width: `${percent}%` }}
          />
        </div>
        <span className="text-gray-600">
          {queuedCount}/{total}
        </span>
      </div>
      <span
        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
          acceptingNew
            ? 'bg-green-100 text-green-800'
            : 'bg-red-100 text-red-800'
        }`}
      >
        {acceptingNew ? '可接收' : '已满'}
      </span>
    </div>
  );
}
