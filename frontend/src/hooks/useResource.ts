import { useCallback, useEffect, useState } from 'react';
import { api } from '../services/api';
import { errorText } from '../lib/errors';

export function useResource<T>(method: string, params: object = {}, poll = 0) {
  const key = JSON.stringify(params);
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    const load = async () => {
      try {
        const value = await api.request<T>(method, JSON.parse(key));
        if (!cancelled) {
          setData(value);
          setError('');
        }
      } catch (err) {
        if (!cancelled) setError(errorText(err));
      } finally {
        if (!cancelled) {
          setLoading(false);
          if (poll) timer = setTimeout(load, poll);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [method, key, poll, revision]);
  return { data, error, loading, refresh };
}
