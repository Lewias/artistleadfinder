import { useState } from 'react';
import { api, type BrowserAction } from '../services/api';
import type { ThreadsState } from '../services/types';
import { errorText } from '../lib/errors';
import { useResource } from './useResource';

export function useThreads() {
  const resource = useResource<ThreadsState>('threads.state', {}, 5000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const perform = async <T>(operation: () => Promise<T>) => {
    setBusy(true);
    setError('');
    try {
      const value = await operation();
      resource.refresh();
      return value;
    } catch (err) {
      setError(errorText(err));
      return undefined;
    } finally {
      setBusy(false);
    }
  };
  const call = <T>(method: string, params: object = {}) => perform(() => api.request<T>(method, params));
  const browser = <T>(action: BrowserAction, params: object = {}) =>
    perform(() => api.browser<T>(action, params));
  const openBrowser = (profileId: string) =>
    perform(async () => {
      await api.browser('threads_open', { id: profileId });
      return api.request('threads.browser_start', { profile_id: profileId });
    });
  const visit = async (url: string) => {
    try {
      await api.openProfile(url);
    } catch (err) {
      setError(errorText(err));
    }
  };
  return { ...resource, busy, call, browser, visit, openBrowser, mutationError: error };
}

export type ThreadsResource = ReturnType<typeof useThreads>;
