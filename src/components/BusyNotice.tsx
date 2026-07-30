type BusyNoticeProps = {
  active: boolean;
  label: string;
  detail?: string;
};

export function BusyNotice({ active, label, detail }: BusyNoticeProps) {
  if (!active) return null;

  return (
    <div className="sticky top-3 z-30 overflow-hidden rounded-lg border border-emerald-200 bg-white shadow-lg shadow-emerald-950/5">
      <div className="h-1 w-full overflow-hidden bg-emerald-50">
        <div className="h-full w-1/2 animate-pulse rounded-r-full bg-emerald-500" />
      </div>
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-emerald-200 border-t-emerald-600" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-gray-900">{label}</div>
          {detail && <div className="mt-0.5 truncate text-xs text-gray-500">{detail}</div>}
        </div>
      </div>
    </div>
  );
}
