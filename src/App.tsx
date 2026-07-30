import { useCallback, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { LinkExtract, type LinkExtractLaunchRequest, type PaymentMethod } from './pages/LinkExtract';
import { AccountLibrary } from './pages/AccountLibrary';
import { getExtractApiBase, setExtractApiBase } from './api/client';

type AppTab = 'extract' | 'accounts';

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
              <p className="text-xs text-gray-500">支付链接提取与账号库管理</p>
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
            <button
              type="button"
              onClick={() => setActiveTab('extract')}
              className={`rounded-lg px-4 py-2 text-sm font-semibold ${
                activeTab === 'extract'
                  ? 'bg-gray-900 text-white'
                  : 'border border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              链接提取
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('accounts')}
              className={`rounded-lg px-4 py-2 text-sm font-semibold ${
                activeTab === 'accounts'
                  ? 'bg-gray-900 text-white'
                  : 'border border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              账号库
            </button>
          </div>

          {activeTab === 'extract' ? (
            <LinkExtract injectedAccessTokens={accountLibraryTokens} launchRequest={accountLaunchRequest} />
          ) : (
            <AccountLibrary onUseTokens={handleUseAccountTokens} />
          )}
        </main>
      </div>
    </ErrorBoundary>
  );
}
