import { useState, useCallback } from 'react';
import { fetchCapacity as apiFetchCapacity } from '../api/tasks';
import type { CapacityInfo } from '../types';

export function useCapacity() {
  const [capacity, setCapacity] = useState<CapacityInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async (paymentMethod: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetchCapacity(paymentMethod);
      setCapacity(data);
      return data;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '容量检查失败';
      setError(msg);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { capacity, loading, error, check };
}
