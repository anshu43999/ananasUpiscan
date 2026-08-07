type HelpStep = {
  title: string;
  detail: string;
};

type PageHelpProps = {
  title: string;
  description: string;
  steps: HelpStep[];
  tips?: string[];
};

export function PageHelp({ title, description, steps, tips = [] }: PageHelpProps) {
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 bg-slate-900 px-5 py-4 text-white">
        <div className="text-xs font-semibold uppercase tracking-wider text-emerald-300">当前模块</div>
        <h2 className="mt-1 text-xl font-semibold">{title}</h2>
        <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-200">{description}</p>
      </div>
      <div className="grid gap-4 px-5 py-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="grid gap-3 md:grid-cols-3">
          {steps.map((step, index) => (
            <div key={step.title} className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="grid size-6 place-items-center rounded-full bg-emerald-600 text-xs font-semibold text-white">
                  {index + 1}
                </span>
                <h3 className="text-sm font-semibold text-slate-900">{step.title}</h3>
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-600">{step.detail}</p>
            </div>
          ))}
        </div>
        <aside className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
          <h3 className="text-sm font-semibold text-amber-950">使用提示</h3>
          <div className="mt-2 space-y-2 text-xs leading-5 text-amber-900">
            {tips.length === 0 ? (
              <p>按页面顺序填写必要信息即可，复杂参数保持默认值也可以运行。</p>
            ) : (
              tips.map((tip) => <p key={tip}>{tip}</p>)
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}
