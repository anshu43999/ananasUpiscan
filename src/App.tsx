import { useCallback, useState } from 'react';
import { getExtractApiBase, setExtractApiBase } from './api/client';
import { ErrorBoundary } from './components/ErrorBoundary';
import { AccountLibrary } from './pages/AccountLibrary';
import { EmailRegister } from './pages/EmailRegister';
import { LinkExtract, type LinkExtractLaunchRequest, type PaymentMethod } from './pages/LinkExtract';
import { OAuthResume } from './pages/OAuthResume';
import { PhoneRegister } from './pages/PhoneRegister';
import { ResourcePool } from './pages/ResourcePool';

type AppTab = 'extract' | 'accounts' | 'resources' | 'email-register' | 'phone-register' | 'oauth-resume';

const tabs: Array<{ id: AppTab; label: string }> = [
  { id: 'extract', label: '链接提取' },
  { id: 'accounts', label: '账号库' },
  { id: 'resources', label: '资源池' },
  { id: 'email-register', label: '邮箱注册' },
  { id: 'phone-register', label: '手机注册' },
  { id: 'oauth-resume', label: 'OAuth续跑' },
];

export default function App() {
  const [extractApiBaseInput, setExtractApiBaseInput] = useState(getExtractApiBase());
  const [activeTab, setActiveTab] = useState<AppTab>('extract');
  const [accountLibraryTokens, setAccountLibraryTokens] = useState('');
  const [accountLaunchRequest, setAccountLaunchRequest] = useState<LinkExtractLaunchRequest | null>(null);
  const [saved, setSaved] = useState(false);

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
      <div className="min-h-screen bg-gray-50">
        <header className="border-b border-gray-200 bg-white px-6 py-4">
          <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4 px-4 sm:px-6">
            <div>
              <h1 className="text-lg font-bold text-gray-900">UPIScan</h1>
              <p className="text-xs text-gray-500">支付链接提取、注册链路与账号库管理</p>
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <input
                type="text"
                value={extractApiBaseInput}
                onChange={(event) => setExtractApiBaseInput(event.target.value)}
                placeholder="链接提取 API 地址，留空使用当前域名"
                className="w-72 max-w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
              />
              <button
                type="button"
                onClick={handleSaveExtractApiBase}
                className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-600 hover:bg-gray-50"
              >
                保存地址
              </button>
              {saved && <span className="text-xs text-green-600">已保存</span>}
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
          <div className="mb-5 flex flex-wrap gap-2">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`rounded-lg px-4 py-2 text-sm font-semibold ${
                  activeTab === tab.id
                    ? 'bg-gray-900 text-white'
                    : 'border border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {activeTab === 'extract' ? (
            <LinkExtract injectedAccessTokens={accountLibraryTokens} launchRequest={accountLaunchRequest} />
          ) : activeTab === 'accounts' ? (
            <AccountLibrary onUseTokens={handleUseAccountTokens} />
          ) : activeTab === 'resources' ? (
            <ResourcePool />
          ) : activeTab === 'email-register' ? (
            <EmailRegister />
          ) : activeTab === 'phone-register' ? (
            <PhoneRegister />
          ) : (
            <OAuthResume />
          )}
        </main>
      </div>
    </ErrorBoundary>
  );
}
