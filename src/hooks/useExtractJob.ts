import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  cancelExtractJob,
  extractJobWsUrl,
  getExtractJob,
  startExtractJob,
  type ExtractJobLog,
  type ExtractJobResponse,
  type ExtractJobStatus,
  type StartExtractOptions,
} from '../api/extract';

interface UseExtractJobsReturn {
  jobs: ExtractJobResponse[];
  loading: boolean;
  error: string | null;
  activeCount: number;
  submit: (options: StartExtractOptions) => Promise<string | null>;
  cancel: (jobId: string) => Promise<void>;
  remove: (jobId: string) => void;
  clearFinished: () => void;
}

type WsMessage =
  | { type: 'log'; log: ExtractJobLog }
  | { type: 'snapshot'; job: ExtractJobResponse };

const TERMINAL: ExtractJobStatus[] = ['completed', 'failed', 'cancelled'];

function isTerminal(status: ExtractJobStatus): boolean {
  return TERMINAL.includes(status);
}

function mergeJobLog(job: ExtractJobResponse, log: ExtractJobLog): ExtractJobResponse {
  const exists = job.logs.some(
    (item) => item.timestamp === log.timestamp && item.message === log.message,
  );
  if (exists) return job;
  return { ...job, logs: [...job.logs, log] };
}

export function useExtractJobs(): UseExtractJobsReturn {
  const [jobs, setJobs] = useState<ExtractJobResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const pollingRefs = useRef(new Map<string, ReturnType<typeof setInterval>>());
  const socketRefs = useRef(new Map<string, WebSocket>());

  const stopPolling = useCallback((jobId: string) => {
    const timer = pollingRefs.current.get(jobId);
    if (!timer) return;
    clearInterval(timer);
    pollingRefs.current.delete(jobId);
  }, []);

  const closeSocket = useCallback((jobId: string) => {
    const socket = socketRefs.current.get(jobId);
    if (!socket) return;
    socket.close();
    socketRefs.current.delete(jobId);
  }, []);

  const applySnapshot = useCallback(
    (snapshot: ExtractJobResponse) => {
      setJobs((current) => {
        const index = current.findIndex((item) => item.job_id === snapshot.job_id);
        if (index === -1) return current;
        const next = [...current];
        next[index] = snapshot;
        return next;
      });
      if (isTerminal(snapshot.status)) {
        stopPolling(snapshot.job_id);
        closeSocket(snapshot.job_id);
      }
    },
    [closeSocket, stopPolling],
  );

  const mergeLog = useCallback((jobId: string, log: ExtractJobLog) => {
    setJobs((current) =>
      current.map((job) => (job.job_id === jobId ? mergeJobLog(job, log) : job)),
    );
  }, []);

  const startPolling = useCallback(
    (jobId: string) => {
      stopPolling(jobId);
      const timer = setInterval(async () => {
        try {
          const snapshot = await getExtractJob(jobId);
          if (!mountedRef.current) return;
          applySnapshot(snapshot);
        } catch (e: unknown) {
          if (!mountedRef.current) return;
          setError(e instanceof Error ? e.message : '获取任务状态失败');
        }
      }, 3000);
      pollingRefs.current.set(jobId, timer);
    },
    [applySnapshot, stopPolling],
  );

  useEffect(() => {
    const pollingMap = pollingRefs.current;
    const socketMap = socketRefs.current;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pollingMap.forEach((timer) => clearInterval(timer));
      pollingMap.clear();
      socketMap.forEach((socket) => socket.close());
      socketMap.clear();
    };
  }, []);

  useEffect(() => {
    const activeIds = new Set(
      jobs.filter((job) => !isTerminal(job.status)).map((job) => job.job_id),
    );

    socketRefs.current.forEach((_socket, jobId) => {
      if (!activeIds.has(jobId)) closeSocket(jobId);
    });

    activeIds.forEach((jobId) => {
      if (socketRefs.current.has(jobId)) return;

      const socket = new WebSocket(extractJobWsUrl(jobId));
      socketRefs.current.set(jobId, socket);

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string) as WsMessage;
          if (data.type === 'log') mergeLog(jobId, data.log);
          if (data.type === 'snapshot') applySnapshot(data.job);
        } catch {
          setError('解析 WebSocket 消息失败');
        }
      };

      socket.onerror = () => {
        if (socketRefs.current.get(jobId) === socket) {
          setError(`任务 ${jobId.slice(0, 8)} WebSocket 连接失败`);
        }
      };

      socket.onclose = () => {
        if (socketRefs.current.get(jobId) === socket) {
          socketRefs.current.delete(jobId);
        }
      };
    });
  }, [applySnapshot, closeSocket, jobs, mergeLog]);

  const submit = useCallback(
    async (options: StartExtractOptions): Promise<string | null> => {
      setLoading(true);
      setError(null);

      try {
        const created = await startExtractJob(options);
        if (!mountedRef.current) return null;
        const initial: ExtractJobResponse = {
          job_id: created.job_id,
          status: 'pending',
          logs: [],
          result: null,
        };
        setJobs((current) => [initial, ...current]);
        startPolling(created.job_id);
        return created.job_id;
      } catch (e: unknown) {
        if (mountedRef.current) {
          setError(e instanceof Error ? e.message : '提交任务失败');
        }
        return null;
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    },
    [startPolling],
  );

  const cancel = useCallback(
    async (jobId: string) => {
      const snapshot = await cancelExtractJob(jobId);
      if (mountedRef.current) applySnapshot(snapshot);
    },
    [applySnapshot],
  );

  const remove = useCallback(
    (jobId: string) => {
      stopPolling(jobId);
      closeSocket(jobId);
      setJobs((current) => current.filter((job) => job.job_id !== jobId));
    },
    [closeSocket, stopPolling],
  );

  const clearFinished = useCallback(() => {
    setJobs((current) => current.filter((job) => !isTerminal(job.status)));
  }, []);

  const activeCount = useMemo(
    () => jobs.filter((job) => !isTerminal(job.status)).length,
    [jobs],
  );

  return { jobs, loading, error, activeCount, submit, cancel, remove, clearFinished };
}
