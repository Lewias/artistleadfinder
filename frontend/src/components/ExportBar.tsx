import { ErrorToast } from './Toaster';
import { errorText } from '../lib/errors';
import { useState } from 'react';
import { save } from '@tauri-apps/plugin-dialog';
import { api } from '../services/api';
import type { LeadQuery } from '../services/types';
import { Download } from 'lucide-react';
import { Button } from './ui/button';
import { ActionMenu } from '../design/ActionMenu';

export function ExportBar({
  query,
  selected,
  compact = false,
}: {
  query: LeadQuery;
  selected: number[];
  compact?: boolean;
}) {
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
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="export-bar">
      {compact ? (
        <ActionMenu label="Экспорт CSV">
          {close => (
            <>
              {(['filtered', 'selected', 'all'] as const).map((scope, index) => (
                <button
                  role="menuitem"
                  key={scope}
                  disabled={busy || (scope === 'selected' && !selected.length)}
                  onClick={() => {
                    close();
                    void exportLeads(scope);
                  }}
                >
                  {['По текущим фильтрам', `Выбранные (${selected.length})`, 'Вся база'][index]}
                </button>
              ))}
            </>
          )}
        </ActionMenu>
      ) : (
        <div className="actions">
          <Button
            variant="green"
            disabled={busy}
            title="Экспорт в CSV по текущим фильтрам"
            onClick={() => void exportLeads('filtered')}
          >
            <Download size={15} /> Экспорт
          </Button>
          {selected.length > 0 && (
            <Button variant="outline" disabled={busy} onClick={() => void exportLeads('selected')}>
              Выбранные ({selected.length})
            </Button>
          )}
          <Button variant="outline" disabled={busy} onClick={() => void exportLeads('all')}>
            Вся база
          </Button>
        </div>
      )}
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
      <ErrorToast message={error} />
    </div>
  );
}
