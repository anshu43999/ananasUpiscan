import { useState, useCallback, useEffect } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Dashboard } from './pages/Dashboard';
import { NewTask } from './pages/NewTask';
import { TaskDetail } from './pages/TaskDetail';
import { LinkExtract } from './pages/LinkExtract';
import { setApiKey, hasApiKey, clearApiKey, setApiBase, setExtractApiBase, getApiBase, getExtractApiBase } from './api/client';

type RightPanel =
  | { type: 'empty' }
  | { type: 'task'; taskId: string }
  | { type: 'new' };

type ActiveTab = 'tasks' | 'extract';

export default function App() {
  const [apiKeyReady, setApiKeyReady] = useState(hasApiKey());
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [apiBaseInput, setApiBaseInput] = useState(getApiBase());
  const [extractApiBaseInput, setExtractApiBaseInput] = useState(getExtractApiBase());
  const [rightPanel, setRightPanel] = useState<RightPanel>({ type: 'empty' });
  const [activeTab, setActiveTab] = useState<ActiveTab>('tasks');

  const handleSaveKey = useCallback(() => {
    const trimmed = apiKeyInput.trim();
    if (!trimmed) return;
    setApiKey(trimmed);
    if (apiBaseInput.trim()) setApiBase(apiBaseInput.trim());
    if (extractApiBaseInput.trim()) setExtractApiBase(extractApiBaseInput.trim());
    setApiKeyReady(true);
    setApiKeyInput('');
  }, [apiKeyInput, apiBaseInput, extractApiBaseInput]);

  const handleClearKey = useCallback(() => {
    clearApiKey();
    setApiKeyReady(false);
    setRightPanel({ type: 'empty' });
  }, []);

  const handleOpenExtractor = useCallback(() => {
    if (extractApiBaseInput.trim()) setExtractApiBase(extractApiBaseInput.trim());
    setActiveTab('extract');
    setApiKeyReady(true);
  }, [extractApiBaseInput]);

  const handleSelectTask = useCallback((taskId: string) => {
    setRightPanel({ type: 'task', taskId });
  }, []);

  const handleNewTask = useCallback(() => {
    setRightPanel({ type: 'new' });
  }, []);

  const handleTaskCreated = useCallback((taskId: string) => {
    setRightPanel({ type: 'task', taskId });
  }, []);

  const handleBack = useCallback(() => {
    setRightPanel({ type: 'empty' });
  }, []);

  useEffect(() => {
    const handler = () => {
      setApiKeyReady(false);
      setRightPanel({ type: 'empty' });
    };
    window.addEventListener('upiscan:401', handler);
    return () => window.removeEventListener('upiscan:401', handler);
  }, []);

  if (!apiKeyReady) {
    return (
      <ErrorBoundary>
        <div className="min-h-screen bg-gray-50 flex items-center justify-center">
          <div className="max-w-md w-full mx-4">
            <div className="text-center mb-8">
              <h1 className="text-2xl font-bold text-gray-900">UPIScan</h1>
              <p className="text-sm text-gray-500 mt-1">Foarge Publisher</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
              <label className="block text-sm font-medium text-gray-700">
                Foarge Publisher API Key
              </label>
              <input
                type="password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSaveKey()}
                placeholder="pk_live_..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none"
                autoFocus
              />
              <div className="border-t border-gray-100 pt-4 space-y-3">
                <p className="text-xs text-gray-500 font-medium">API 地址 (默认即可)</p>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">任务管理 API</label>
                  <input
                    type="text"
                    value={apiBaseInput}
                    onChange={(e) => setApiBaseInput(e.target.value)}
                    placeholder="/api/publisher/v1"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">链接提取 API</label>
                  <input
                    type="text"
                    value={extractApiBaseInput}
                    onChange={(e) => setExtractApiBaseInput(e.target.value)}
                    placeholder="http://127.0.0.1:8000 or blank for Vite proxy"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none font-mono"
                  />
                </div>
              </div>
              <p className="text-xs text-gray-400">
                请输入你的 Wallet API Key（以 pk_live_ 开头）。Key 仅在当前浏览器会话中保存。
              </p>
              <button
                onClick={handleSaveKey}
                disabled={!apiKeyInput.trim()}
                className="w-full py-2.5 text-sm font-medium text-white bg-purple-600 rounded-lg hover:bg-purple-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
              >
                保存
              </button>
              <button
                type="button"
                onClick={handleOpenExtractor}
                className="w-full py-2.5 text-sm font-medium text-purple-700 border border-purple-200 rounded-lg hover:bg-purple-50 transition-colors"
              >
                Open link extractor only
              </button>
            </div>
          </div>
        </div>
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <div className="h-screen flex flex-col bg-gray-50">
        <header className="flex items-center justify-between h-14 px-6 bg-white border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold text-gray-900">UPIScan</h1>
              <span className="text-xs text-gray-400">Foarge Publisher</span>
            </div>
            <nav className="flex items-center gap-1" role="tablist" aria-label="功能导航">
              {(['tasks', 'extract'] as const).map((tab) => (
                <button
                  key={tab}
                  role="tab"
                  aria-selected={activeTab === tab}
                  onClick={() => {
                    setActiveTab(tab);
                    if (tab === 'tasks') setRightPanel({ type: 'empty' });
                  }}
                  className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                    activeTab === tab
                      ? 'bg-purple-50 text-purple-700 font-medium'
                      : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  {tab === 'tasks' ? '任务管理' : '链接提取'}
                </button>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-green-600 bg-green-50 px-2 py-1 rounded">API Key 已配置</span>
            <button
              onClick={handleClearKey}
              className="text-xs text-gray-400 hover:text-red-500 transition-colors"
            >
              清除
            </button>
          </div>
        </header>

        <div className="flex flex-1 overflow-hidden">
          {activeTab === 'tasks' && (
            <>
              <div className="w-96 flex-shrink-0 border-r border-gray-200 overflow-y-auto p-4">
                <Dashboard
                  onSelectTask={handleSelectTask}
                  onNewTask={handleNewTask}
                  selectedTaskId={rightPanel.type === 'task' ? rightPanel.taskId : null}
                />
              </div>

              <div className="flex-1 overflow-y-auto p-6">
                {rightPanel.type === 'empty' && (
                  <div className="flex items-center justify-center h-full text-gray-400 text-sm">
                    选择一个任务查看详情，或点击"新建"提交账号
                  </div>
                )}
                {rightPanel.type === 'task' && (
                  <TaskDetail taskId={rightPanel.taskId} onBack={handleBack} />
                )}
                {rightPanel.type === 'new' && (
                  <NewTask onCreated={handleTaskCreated} onBack={handleBack} />
                )}
              </div>
            </>
          )}

          {activeTab === 'extract' && (
            <div className="flex-1 overflow-y-auto p-6">
              <LinkExtract />
            </div>
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
}
