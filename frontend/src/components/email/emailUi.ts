import { useState } from 'react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errors';

export const time = (value: string | null) =>
  value
    ? new Date(value).toLocaleString('ru-RU', {
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';

/** A core call that refreshes the page after it and keeps the error and notice to show. */
export function useCall(refresh: () => void) {
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const call = async <T>(method: string, params: object = {}) => {
    setError('');
    setNotice('');
    try {
      const result = await api.request<T>(method, params);
      refresh();
      return result;
    } catch (err) {
      setError(errorText(err));
      return undefined;
    }
  };
  return { call, error, notice, setNotice };
}
