import { Trash2 } from 'lucide-react';
import { api } from '../services/api';
import type { ScoutSourceRow } from '../services/types';
import { Button } from './ui/button';
import { number, relativeTime } from '../lib/format';
import { sourceStatusLabels } from './scoutStatus';

/** Per-source statistics with the on/off switch; shown in the board's «…» menu. */
export function SourceTable({
  rows,
  busy,
  act,
}: {
  rows: ScoutSourceRow[];
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<boolean>;
}) {
  if (!rows.length) return <p className="helper">Источников пока нет.</p>;
  return (
    <div className="source-table" role="table" aria-label="Источники">
      <div className="source-row source-head" role="row">
        <span role="columnheader">Источник</span>
        <span role="columnheader">Последнее сканирование</span>
        <span role="columnheader">Статус</span>
        <span role="columnheader" title="Кандидатов найдено за всё время">
          Кандид.
        </span>
        <span role="columnheader" title="Профилей прочитано">
          Прочит.
        </span>
        <span role="columnheader">Лидов</span>
        <span role="columnheader" title="Профилей пропущено фильтрами">
          Пропущ.
        </span>
        <span role="columnheader">Ошибок</span>
        <span role="columnheader" aria-label="Действия" />
      </div>
      {rows.map(row => (
        <div className={`source-row${row.enabled ? '' : ' disabled'}`} role="row" key={row.url}>
          <span role="cell" className="source-name">
            <label className="switch" title={row.enabled ? 'Выключить' : 'Включить'}>
              <input
                type="checkbox"
                aria-label={`Источник ${row.username} включён`}
                checked={row.enabled}
                disabled={busy}
                onChange={event =>
                  void act(() =>
                    api.request('scout.source_update', { url: row.url, enabled: event.target.checked }),
                  )
                }
              />
              <i />
            </label>
            @{row.username}
          </span>
          <span role="cell" className="helper">
            {relativeTime(row.last_scanned_at)}
          </span>
          <span role="cell">
            <span className={`status-chip source-${row.status}`}>
              {sourceStatusLabels[row.status] || row.status}
            </span>
          </span>
          <span role="cell">{number(row.candidates_found ?? 0)}</span>
          <span role="cell">{number(row.profiles_resolved ?? 0)}</span>
          <span role="cell">{number(row.leads_found)}</span>
          <span role="cell">{number(row.profiles_skipped ?? 0)}</span>
          <span role="cell" className={row.errors_count ? 'bad' : ''}>
            {number(row.errors_count ?? 0)}
          </span>
          <span role="cell">
            <Button
              icon
              variant="outline"
              aria-label={`Удалить ${row.username}`}
              title="Удалить источник"
              disabled={busy}
              onClick={() => void act(() => api.request('scout.source_remove', { url: row.url }))}
            >
              <Trash2 size={15} />
            </Button>
          </span>
        </div>
      ))}
    </div>
  );
}
