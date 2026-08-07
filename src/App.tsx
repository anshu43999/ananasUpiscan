import { useCallback, useMemo, useState } from 'react';
import { getExtractApiBase, setExtractApiBase } from './api/client';
import { ErrorBoundary } from './components/ErrorBoundary';
import { PageHelp } from './components/PageHelp';
import { AccountLibrary } from './pages/AccountLibrary';
import { EmailRegister } from './pages/EmailRegister';
import { LinkExtract, type LinkExtractLaunchRequest, type PaymentMethod } from './pages/LinkExtract';
import { OAuthResume } from './pages/OAuthResume';
import { PhoneRegister } from './pages/PhoneRegister';
import { ResourcePool } from './pages/ResourcePool';

type AppTab = 'extract' | 'accounts' | 'resources' | 'email-register' | 'phone-register' | 'oauth-resume';

const tabGroups: Array<{
  title: string;
  items: Array<{ id: AppTab; label: string; summary: string }>;
}> = [
  {
    title: '日常操作',
    items: [
      { id: 'extract', label: '链接提取', summary: 'UPI、iDEAL、MoMo、Kakao、直卡与第三方开通' },
      { id: 'accounts', label: '账号库', summary: '导入账号、检测状态、快捷发起提取' },
    ],
  },
  {
    title: '资源与注册',
    items: [
      { id: 'resources', label: '资源池', summary: '邮箱、手机号、代理 Seed 的统一库存' },
      { id: 'email-register', label: '邮箱注册', summary: '邮箱接码注册并写入账号库' },
      { id: 'phone-register', label: '手机注册', summary: '手机号接码注册并支持租号服务商' },
      { id: 'oauth-resume', label: 'OAuth 续跑', summary: '恢复会话、绑定 OAuth、更新账号 token' },
    ],
  },
];

const pageGuides: Record<AppTab, {
  title: string;
  description: string;
  steps: Array<{ title: string; detail: string }>;
  tips: string[];
}> = {
  extract: {
    title: '链接提取',
    description: '用于把已准备好的 ChatGPT AT 或 Session 转成支付链接。提取逻辑在后端运行，前端只负责提交参数、展示日志和收集结果。',
    steps: [
      { title: '选择渠道', detail: '根据要跑的链路选择 UPI、iDEAL、MoMo、Kakao、直卡或第三方 Ready Plus。' },
      { title: '填入账号', detail: '从账号库快捷加入，或手动粘贴 AT / Session。批量任务建议先在账号库检测。' },
      { title: '查看结果', detail: '任务完成后页面会显示支付链接，并在有结果时播放提示音。' },
    ],
    tips: [
      '本地提链不需要第三方 API Key；只有“第三方开通”需要填写 Key。',
      '代理可以用系统内置资源池，也可以在当前任务里粘贴自定义代理。',
    ],
  },
  accounts: {
    title: '账号库',
    description: '集中管理账号 AT、Session、健康状态、Plus 状态和支付资格。这里是批量提链前的准备区。',
    steps: [
      { title: '导入账号', detail: '支持 AT、Session JSON、账号四段格式和导出的 JSON 格式。' },
      { title: '检测状态', detail: '先跑 AT 健康，再按需要跑支付资格或 Plus 校验。' },
      { title: '发起提链', detail: '在账号行尾选择渠道，或批量选择后加入链接提取页面。' },
    ],
    tips: [
      '健康检测主要判断 AT 是否可解析、是否过期；资格检测会调用资格接口。',
      '导出账号的格式与导入格式保持一致，方便迁移和备份。',
    ],
  },
  resources: {
    title: '资源池',
    description: '把邮箱接码、手机号接码和代理 Seed 统一放进后端资源池，注册、OAuth 和提链任务会按可用状态自动租用。',
    steps: [
      { title: '选择类型', detail: '先选择要导入的是邮箱、手机号还是代理 Seed。' },
      { title: '粘贴资源', detail: '按示例一行一条粘贴，导入后会进入可用状态。' },
      { title: '维护库存', detail: '可以筛选、恢复、禁用或删除资源，任务执行后会回写状态。' },
    ],
    tips: [
      '代理 Seed 用于生成 sticky 代理会话，不等同于提链页面临时粘贴的代理。',
      '手机号池分注册手机号和绑定手机号，两者用途不同。',
    ],
  },
  'email-register': {
    title: '邮箱注册',
    description: '通过邮箱接码完成 ChatGPT 注册，成功账号会自动写入账号库，适合批量准备可用 AT。',
    steps: [
      { title: '准备邮箱', detail: '可手动粘贴接码数据，也可从资源池租用邮箱资源。' },
      { title: '配置代理', detail: '可粘贴注册代理，或使用资源池代理 Seed 自动轮换 IP。' },
      { title: '启动任务', detail: '后端执行浏览器注册流程，并把成功结果写入账号库。' },
    ],
    tips: [
      'Go 批量注册适合大量任务；普通注册适合少量调试。',
      '如果 OTP 不稳定，先检查邮箱资源和接码代理。',
    ],
  },
  'phone-register': {
    title: '手机注册',
    description: '通过自备接码 URL 或短信服务商租号注册账号，并支持代理池轮换。',
    steps: [
      { title: '选择短信来源', detail: '可选自备手机号、资源池、HeroSMS、SMSBower 或 SMS-Activate。' },
      { title: '配置租号', detail: '服务商模式需要 API Key、服务编号、国家和价格参数。' },
      { title: '运行注册', detail: '后端完成注册、短信轮询和账号库写入。' },
    ],
    tips: [
      '服务商 API Key 只随本次任务提交，不写入账号库。',
      '需要轮换 IP 时优先启用代理 Seed 池。',
    ],
  },
  'oauth-resume': {
    title: 'OAuth 绑定 / 续跑',
    description: '用于恢复注册阶段保存的浏览器会话，继续完成 OAuth 授权、邮箱/手机绑定，并回写新的 token。',
    steps: [
      { title: '选择账号', detail: '从账号库选择带 session_json 的账号，或直接粘贴 resume JSON。' },
      { title: '补充资源', detail: '按需要提供邮箱 OTP、绑定手机号和 OAuth 代理池。' },
      { title: '回写结果', detail: '成功后 access_token、refresh_token 和 id_token 会更新到账号库。' },
    ],
    tips: [
      '只有需要继续 OAuth 或补绑定时才用这个模块。',
      '如果流程触发 add_phone，请确认绑定手机号池有可用资源。',
    ],
  },
};

function renderTab(activeTab: AppTab, onUseTokens: (tokens: string, paymentMethod?: PaymentMethod) => void, tokens: string, launchRequest: LinkExtractLaunchRequest | null) {
  if (activeTab === 'extract') {
    return <LinkExtract injectedAccessTokens={tokens} launchRequest={launchRequest} />;
  }
  if (activeTab === 'accounts') {
    return <AccountLibrary onUseTokens={onUseTokens} />;
  }
  if (activeTab === 'resources') {
    return <ResourcePool />;
  }
  if (activeTab === 'email-register') {
    return <EmailRegister />;
  }
  if (activeTab === 'phone-register') {
    return <PhoneRegister />;
  }
  return <OAuthResume />;
}

export default function App() {
  const [extractApiBaseInput, setExtractApiBaseInput] = useState(getExtractApiBase());
  const [activeTab, setActiveTab] = useState<AppTab>('extract');
  const [accountLibraryTokens, setAccountLibraryTokens] = useState('');
  const [accountLaunchRequest, setAccountLaunchRequest] = useState<LinkExtractLaunchRequest | null>(null);
  const [saved, setSaved] = useState(false);

  const activeGuide = pageGuides[activeTab];
  const flatTabs = useMemo(() => tabGroups.flatMap((group) => group.items), []);

  const handleSaveExtractApiBase = useCallback(() => {
    setExtractApiBase(extractApiBaseInput);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  }, [extractApiBaseInput]);

  const handleUseAccountTokens = useCallback((tokens: string, paymentMethod?: PaymentMethod) => {
    setAccountLibraryTokens(tokens);
    if (paymentMethod) {
      setAccountLaunchRequest({
        accessTokens: tokens,
        paymentMethod,
        nonce: Date.now(),
      });
    }
    setActiveTab('extract');
  }, []);

  return (
    <ErrorBoundary>
      <div className="min-h-screen bg-slate-100 text-slate-900">
        <div className="mx-auto grid min-h-screen max-w-[1760px] gap-0 xl:grid-cols-[292px_minmax(0,1fr)]">
          <aside className="border-b border-slate-200 bg-white px-4 py-4 xl:sticky xl:top-0 xl:h-screen xl:border-b-0 xl:border-r xl:px-5">
            <div className="flex items-center justify-between gap-3 xl:block">
              <div>
                <h1 className="text-xl font-bold tracking-tight text-slate-950">UPIScan</h1>
                <p className="mt-1 text-xs leading-5 text-slate-500">支付链接提取、账号库、资源池和注册链路控制台</p>
              </div>
              <div className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700 xl:mt-4 xl:inline-flex">
                后端执行
              </div>
            </div>

            <nav className="mt-5 hidden space-y-5 xl:block">
              {tabGroups.map((group) => (
                <div key={group.title}>
                  <div className="px-2 text-xs font-semibold text-slate-400">{group.title}</div>
                  <div className="mt-2 space-y-1">
                    {group.items.map((tab) => {
                      const active = activeTab === tab.id;
                      return (
                        <button
                          key={tab.id}
                          type="button"
                          onClick={() => setActiveTab(tab.id)}
                          className={`w-full rounded-lg px-3 py-2.5 text-left transition-colors ${
                            active
                              ? 'bg-slate-900 text-white shadow-sm'
                              : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950'
                          }`}
                        >
                          <span className="block text-sm font-semibold">{tab.label}</span>
                          <span className={`mt-1 block text-xs leading-5 ${active ? 'text-slate-300' : 'text-slate-500'}`}>
                            {tab.summary}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>

            <div className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="text-sm font-semibold text-slate-900">推荐流程</div>
              <ol className="mt-2 space-y-2 text-xs leading-5 text-slate-600">
                <li>1. 先导入资源或账号</li>
                <li>2. 检测 AT 健康和账号资格</li>
                <li>3. 选择渠道发起提链</li>
                <li>4. 查看日志、复制结果、导出账号</li>
              </ol>
            </div>
          </aside>

          <main className="min-w-0 px-4 py-4 sm:px-6 lg:px-8">
            <div className="mb-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-end">
                <div>
                  <label className="text-xs font-semibold text-slate-500">后端 API 地址</label>
                  <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
                    <input
                      type="text"
                      value={extractApiBaseInput}
                      onChange={(event) => setExtractApiBaseInput(event.target.value)}
                      placeholder="留空使用当前域名，例如 http://49.51.182.250:8000"
                      className="min-w-64 flex-1 rounded-md border border-slate-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
                    />
                    <button
                      type="button"
                      onClick={handleSaveExtractApiBase}
                      className="rounded-md border border-slate-200 bg-slate-900 px-4 py-2 text-xs font-semibold text-white hover:bg-slate-800"
                    >
                      保存地址
                    </button>
                    {saved && <span className="text-xs font-medium text-emerald-700">已保存</span>}
                  </div>
                </div>
                <select
                  value={activeTab}
                  onChange={(event) => setActiveTab(event.target.value as AppTab)}
                  className="rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500 xl:hidden"
                >
                  {flatTabs.map((tab) => (
                    <option key={tab.id} value={tab.id}>{tab.label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="space-y-5">
              <PageHelp {...activeGuide} />
              {renderTab(activeTab, handleUseAccountTokens, accountLibraryTokens, accountLaunchRequest)}
            </div>
          </main>
        </div>
      </div>
    </ErrorBoundary>
  );
}
