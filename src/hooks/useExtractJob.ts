import { useCallback, useEffect, useRef, useState } from 'react';
import {
  cancelExtractJob,
  getExtractJob,
  startExtractJob,
  type ExtractJobLog,
  type ExtractJobResponse,
  type ExtractJobStatus,
  type StartExtractOptions,
} from '../api/extract';
import { useExtractWebSocket } from './useExtractWebSocket';

interface UseExtractJobReturn {
  job: ExtractJobResponse | null;
  loading: boolean;
  error: string | null;
  submit: (options: StartExtractOptions) => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
}

const TERMINAL: ExtractJobStatus[] = ['completed', 'failed', 'cancelled'];

export function useExtractJob(): UseExtractJobReturn {
  const [job, setJob] = useState<ExtractJobResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const mergeLog = useCallback((log: ExtractJobLog) => {
    setJob((current) => {
      if (!current) return current;
      const exists = current.logs.some(
        (item) => item.timestamp === log.timestamp && item.message === log.message,
      );
      if (exists) return current;
      return { ...current, logs: [...current.logs, log] };
    });
  }, []);

  const applySnapshot = useCallback((snapshot: ExtractJobResponse) => {
    setJob(snapshot);
    if (TERMINAL.includes(snapshot.status) && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  useExtractWebSocket({
    jobId,
    onLog: mergeLog,
    onSnapshot: applySnapshot,
    onError: setError,
  });

  const startPolling = useCallback((id: string) => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = setInterval(async () => {
      try {
        const snapshot = await getExtractJob(id);
        if (!mountedRef.current) return;
        applySnapshot(snapshot);
      } catch (e: unknown) {
        if (!mountedRef.current) return;
        setError(e instanceof Error ? e.message : 'Failed to fetch job status');
      }
    }, 3000);
  }, [applySnapshot]);

  const submit = useCallback(
    async (options: StartExtractOptions) => {
      setLoading(true);
      setError(null);
      setJob(null);
      setJobId(null);

      try {
        const created = await startExtractJob(options);
        if (!mountedRef.current) return;
        const initial: ExtractJobResponse = {
          job_id: created.job_id,
          status: 'pending',
          logs: [],
          result: null,
        };
        setJob(initial);
        setJobId(created.job_id);
        startPolling(created.job_id);
      } catch (e: unknown) {
        if (!mountedRef.current) return;
        setError(e instanceof Error ? e.message : 'Failed to submit job');
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    },
    [startPolling],
  );

  const cancel = useCallback(async () => {
    if (!jobId) return;
    const snapshot = await cancelExtractJob(jobId);
    if (mountedRef.current) applySnapshot(snapshot);
  }, [applySnapshot, jobId]);

  const reset = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    setJob(null);
    setJobId(null);
    setError(null);
    setLoading(false);
  }, []);

  return { job, loading, error, submit, cancel, reset };
}
