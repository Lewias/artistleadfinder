import { useState, type KeyboardEvent } from 'react';
import { AtSign, CornerDownLeft, Layers, Trash2 } from 'lucide-react';
import { api } from '../services/api';
import type { ScoutSourceRow } from '../services/types';
import { Button } from './ui/button';
import { parseScoutSources, sourceEntries } from './scoutSources';
import { number, relativeTime } from '../lib/format';

const statusLabels: Record<string, string> = {
  new: 'Новый',
  queued: 'В очереди',
  scanning: 'Сканируется',
  done: 'Просканирован',
  stopped: 'Остановлен',
  rate_limited: 'Ограничение',
};

export function ScoutSources({ rows, refresh }: { rows: ScoutSourceRow[] | undefined; refresh: () => void }) {
  const [draft, setDraft] = useState('');
  const [bulk, setBulk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
      refresh();
      return true;
    } catch (err) {
      setError(String(err));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const add = async (text: string) => {
    const entries = sourceEntries(text);
    if (!entries.length) return false;
    // The parser validates up to 20 entries at a time; report the entry number in the whole list.
    for (let start = 0; start < entries.length; start += 20) {
      const checked = parseScoutSources(entries.slice(start, start + 20).join('\n'));
      if (checked.error) {
        setError(checked.error.replace(/№(\d+)/, (_, n: string) => `№${Number(n) + start}`));
        return false;
      }
    }
    return act(() => api.request('scout.source_add', { values: entries }));
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    void add(draft).then(ok => ok && setDraft(''));
  };
  const enabled = rows?.filter(row => row.enabled).length ?? 0;
  return (
    <div className="panel source-board">
      <div className="section-heading">
        <h2>SMM-источники</h2>
        <span className="helper">
          Включено {enabled} из {rows?.length ?? 0}
        </span>
      </div>
      {bulk === null ? (
        <label className="chip-input">
          <AtSign size={17} aria-hidden="true" />
          <input
            aria-label="Новый источник"
            placeholder="Добавьте SMM-источник (@rapdaily) и нажмите Enter…"
            value={draft}
            disabled={busy}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={onKey}
          />
          <kbd aria-hidden="true">
            <CornerDownLeft size={13} />
          </kbd>
        </label>
      ) : (
        <div className="bulk-input">
          <textarea
            rows={4}
            aria-label="Список источников"
            placeholder={'@rapdaily\n@undergroundhiphop\nhttps://www.instagram.com/musicblog/'}
            value={bulk}
            onChange={event => setBulk(event.target.value)}
          />
          <div className="actions">
            <Button
              disabled={busy || !bulk.trim()}
              onClick={() => void add(bulk).then(ok => ok && setBulk(null))}
            >
              Добавить
            </Button>
            <Button variant="outline" onClick={() => setBulk(null)}>
              Отмена
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {rows && rows.length > 0 ? (
        <div className="source-table" role="table" aria-label="Источники">
          <div className="source-row source-head" role="row">
            <span role="columnheader">Источник</span>
            <span role="columnheader">Последнее сканирование</span>
            <span role="columnheader">Статус</span>
            <span role="columnheader">Лидов</span>
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
                  {statusLabels[row.status] || row.status}
                </span>
              </span>
              <span role="cell">{number(row.leads_found)}</span>
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
      ) : (
        rows && <p className="helper">Источников пока нет. Добавьте аккаунты музыкальных медиа и пабликов.</p>
      )}
      <div className="board-footer">
        <Button variant="outline" disabled={bulk !== null} onClick={() => setBulk('')}>
          <Layers size={16} /> Массовый
        </Button>
        <span className="board-status">
          Источники сканируются по очереди пачками; недавно просканированные пропускаются до конца кулдауна.
        </span>
      </div>
    </div>
  );
}
