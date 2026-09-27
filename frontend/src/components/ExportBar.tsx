import { useState } from 'react';
import { save } from '@tauri-apps/plugin-dialog';
import { api } from '../services/api';
import type { LeadQuery } from '../services/types';
import { Button } from './ui/button';

export function ExportBar({ query, selected }: { query: LeadQuery; selected: number[] }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const exportLeads = async (scope: 'selected' | 'filtered' | 'all') => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const path = await save({
        title: 'Экспорт профилей',
        defaultPath: 'artist-leads.csv',
        filters: [{ name: 'CSV', extensions: ['csv'] }],
      });
      if (!path) return;
      const result = await api.request<{ count: number }>('leads.export', {
        path,
        query: scope === 'filtered' ? query : {},
        ...(scope === 'selected' ? { ids: selected } : {}),
      });
      setMessage(`Экспортировано профилей: ${result.count}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="export-bar">
      <div className="actions">
        <Button
          variant="outline"
          disabled={busy || !selected.length}
          onClick={() => void exportLeads('selected')}
        >
          Выбранные ({selected.length}) → CSV
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => void exportLeads('filtered')}>
          По фильтрам → CSV
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => void exportLeads('all')}>
          Вся база → CSV
        </Button>
      </div>
      {busy && (
        <p role="status" className="helper">
          Подготовка экспорта…
        </p>
      )}
      {message && (
        <p role="status" className="helper">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
    </div>
  );
}
