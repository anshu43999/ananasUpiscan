import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchTasks as apiFetchTasks } from '../api/tasks';
import type { Task } from '../types';

export function useTaskList() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await apiFetchTasks();
      if (mountedRef.current) {
        setTasks(Array.isArray(data) ? data : []);
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      const msg = e instanceof Error ? e.message : '加载失败';
      setError(msg);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const interval = setInterval(load, 30000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [load]);

  return { tasks, loading, error, reload: load };
}
