import { useCallback, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { LinkExtract } from './pages/LinkExtract';
import { getExtractApiBase, setExtractApiBase } from './api/client';

export default function App() {
  const [extractApiBaseInput, setExtractApiBaseInput] = useState(getExtractApiBase());
  const [saved, setSaved] = useState(false);

  const handleSaveExtractApiBase = useCallback(() => {
    setExtractApiBase(extractApiBaseInput);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  }, [extractApiBaseInput]);

  return (
    <ErrorBoundary>
      <div className="min-h-screen bg-gray-50">
        <header className="border-b border-gray-200 bg-white px-6 py-4">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4">
            <div>
              <h1 className="text-lg font-bold text-gray-900">UPIScan</h1>
              <p className="text-xs text-gray-500">UPI 链接提取</p>
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <input
                type="text"
                value={extractApiBaseInput}
                onChange={(event) => setExtractApiBaseInput(event.target.value)}
                placeholder="链接提取 API 地址，留空使用当前域名"
                className="w-72 max-w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500"
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

        <main className="mx-auto max-w-6xl px-6 py-6">
          <LinkExtract />
        </main>
      </div>
    </ErrorBoundary>
  );
}
