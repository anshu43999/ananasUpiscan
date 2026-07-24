import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchTask as apiFetchTask } from '../api/tasks';
import type { Task } from '../types';

export function useTaskDetail(id: string | undefined) {
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const data = await apiFetchTask(id);
      if (!mountedRef.current) return;
      setError(null);
      setTask(data);
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      const msg = e instanceof Error ? e.message : '加载失败';
      setError(msg);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!task) return;
    const active = ['pending', 'assigned', 'queued', 'awaiting_checkout'];
    if (!active.includes(task.status)) return;

    const interval = setInterval(load, 10000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [task?.status, load]);

  return { task, loading, error, reload: load, setTask };
}
