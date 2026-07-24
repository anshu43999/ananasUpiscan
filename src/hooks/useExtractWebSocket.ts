import { useEffect } from 'react';
import {
  extractJobWsUrl,
  type ExtractJobLog,
  type ExtractJobResponse,
} from '../api/extract';

type Message =
  | { type: 'log'; log: ExtractJobLog }
  | { type: 'snapshot'; job: ExtractJobResponse };

interface UseExtractWebSocketOptions {
  jobId: string | null;
  onLog: (log: ExtractJobLog) => void;
  onSnapshot: (job: ExtractJobResponse) => void;
  onError?: (message: string) => void;
}

export function useExtractWebSocket({
  jobId,
  onLog,
  onSnapshot,
  onError,
}: UseExtractWebSocketOptions): void {
  useEffect(() => {
    if (!jobId) return undefined;

    let closed = false;
    const ws = new WebSocket(extractJobWsUrl(jobId));

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string) as Message;
        if (data.type === 'log') onLog(data.log);
        if (data.type === 'snapshot') onSnapshot(data.job);
      } catch {
        onError?.('Failed to parse WebSocket message');
      }
    };

    ws.onerror = () => {
      if (!closed) onError?.('WebSocket connection failed');
    };

    return () => {
      closed = true;
      ws.close();
    };
  }, [jobId, onError, onLog, onSnapshot]);
}
