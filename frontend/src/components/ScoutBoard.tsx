import { ErrorToast } from './Toaster';
import { useState, type KeyboardEvent } from 'react';
import {
  AtSign,
  BarChart3,
  Copy,
  CornerDownLeft,
  LayoutGrid,
  Radar,
  SlidersHorizontal,
  Square,
  SquarePen,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import type { ScoutAccountRow, ScoutSourceRow } from '../services/types';
import { number, relativeTime } from '../lib/format';
import { Button } from './ui/button';
import { Menu } from './Menu';
import { Modal } from './Modal';
import { parseScoutSources, sourceEntries, sourceHandle } from './scoutSources';
import { SourceTable } from './ScoutSourceTable';
import { sourceStatusLabels } from './scoutStatus';
import { WorkSettingsModal } from './WorkSettings';
import { controlScout, errorText, scoutActive, scoutRunsLabel, startScout } from './accountRuns';

type Dialog = 'settings' | 'stats' | 'clear' | null;

const tone = (row: ScoutSourceRow) =>
  !row.enabled
    ? 'muted'
    : row.status === 'scanning'
      ? 'queued'
      : row.status === 'rate_limited' || row.status === 'error'
        ? 'bad'
        : '';

const chipTitle = (row: ScoutSourceRow) =>
  [
    sourceStatusLabels[row.status] || row.status,
    row.enabled ? null : 'выключен',
    `кандидатов ${number(row.candidates_found ?? 0)}`,
    `лидов ${number(row.leads_found)}`,
    row.last_scanned_at ? `сканирован ${relativeTime(row.last_scanned_at)}` : null,
  ]
    .filter(Boolean)
    .join(' · ') + `\nНажмите, чтобы ${row.enabled ? 'выключить' : 'включить'}`;

/** The parser checks at most 20 entries at a time; report the entry number in the whole list. */
function sourcesError(entries: string[]): string {
  for (let start = 0; start < entries.length; start += 20) {
    const checked = parseScoutSources(entries.slice(start, start + 20).join('\n'));
    if (checked.error) return checked.error.replace(/№(\d+)/, (_, n: string) => `№${Number(n) + start}`);
  }
  return '';
}

/** SMM sources as chips (or one per line in bulk mode) and the start button for the chosen accounts. */
export function ScoutBoard({
  rows,
  accounts,
  selected,
  refresh,
}: {
  rows: ScoutSourceRow[] | undefined;
  accounts: ScoutAccountRow[];
  selected: string[];
  refresh: () => void;
}) {
  const [draft, setDraft] = useState('');
  const [bulk, setBulk] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const list = rows ?? [];
  const enabled = list.filter(row => row.enabled).length;

  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await operation();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const add = async (text: string) => {
    const entries = sourceEntries(text);
    if (!entries.length) return false;
    const problem = sourcesError(entries);
    if (problem) {
      setError(problem);
      return false;
    }
    return act(() => api.request('scout.source_add', { values: entries }));
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    void add(draft).then(ok => ok && setDraft(''));
  };

  // Bulk mode edits the whole list: new lines are added, removed lines are deleted.
  const bulkText = () => list.map(row => row.username).join('\n');
  const applyBulk = async (text: string) => {
    const entries = sourceEntries(text);
    if (!entries.length && list.length) {
      setDialog('clear');
      return false;
    }
    const problem = sourcesError(entries);
    if (problem) {
      setError(problem);
      return false;
    }
    const wanted = new Set(entries.map(entry => sourceHandle(entry).toLowerCase()));
    const known = new Set(list.map(row => row.username.toLowerCase()));
    const added = entries.filter(entry => !known.has(sourceHandle(entry).toLowerCase()));
    const removed = list.filter(row => !wanted.has(row.username.toLowerCase()));
    return act(async () => {
      if (added.length) await api.request('scout.source_add', { values: added });
      for (const row of removed) await api.request('scout.source_remove', { url: row.url });
    });
  };
  const toggleBulk = () => {
    if (bulk === null) setBulk(bulkText());
    else if (bulk.trim() === bulkText().trim()) setBulk(null);
    else void applyBulk(bulk).then(ok => ok && setBulk(null));
  };
  const clearAll = () =>
    void act(async () => {
      for (const row of list) await api.request('scout.source_remove', { url: row.url });
    }).then(ok => {
      setDialog(null);
      if (ok) setBulk(null);
    });
  const copyList = async () => {
    try {
      await navigator.clipboard.writeText(bulkText());
      setNotice(`Скопировано: ${list.length}`);
    } catch {
      setError('Не удалось скопировать в буфер обмена.');
    }
  };

  const active = accounts.filter(scoutActive);
  const chosen = accounts.filter(row => selected.includes(row.profile.id));
  const launch = chosen.length ? chosen : accounts.length === 1 ? accounts : [];
  const parse = () =>
    act(async () => {
      if (!launch.length) throw new Error('Отметьте аккаунты в «Настройках» → «Аккаунты и рассылка».');
      for (const row of launch) await startScout(row);
    });
  const stop = () =>
    act(async () => {
      for (const row of active) await controlScout(row, 'cancel');
    });
  const attention = active.some(row => row.run?.status === 'paused' && row.run.error);

  return (
    <section className="panel board scout-board">
      <div className="board-heading">
        <h2>Где искать</h2>
        <span className="helper">SMM-источники: паблики и медиа, где выкладывают артистов</span>
      </div>
      {bulk === null ? (
        <div className="chip-cloud board-chips" aria-label="SMM-источники">
          {list.map(row => (
            <span key={row.url} className={`chip board-chip ${tone(row)}`} title={chipTitle(row)}>
              <button
                type="button"
                className="chip-label"
                aria-pressed={row.enabled}
                disabled={busy}
                onClick={() =>
                  void act(() => api.request('scout.source_update', { url: row.url, enabled: !row.enabled }))
                }
              >
                {row.username}
              </button>
              <button
                type="button"
                aria-label={`Убрать ${row.username}`}
                disabled={busy}
                onClick={() => void act(() => api.request('scout.source_remove', { url: row.url }))}
              >
                <X size={13} />
              </button>
            </span>
          ))}
          {rows && !list.length && (
            <p className="empty-copy">
              Источников пока нет. Добавьте аккаунты музыкальных медиа и пабликов ниже или вставьте список
              через «Массовый».
            </p>
          )}
        </div>
      ) : (
        <textarea
          className="board-bulk"
          autoFocus
          aria-label="Список источников, по одному в строке"
          placeholder={'rapgoat.tv\n@topdailyrap\nhttps://www.instagram.com/raphitsusa/'}
          value={bulk}
          disabled={busy}
          onChange={event => setBulk(event.target.value)}
        />
      )}

      {bulk === null && (
        <label className="chip-input">
          <AtSign size={17} aria-hidden="true" />
          <input
            aria-label="Новый источник"
            placeholder="Добавьте SMM источники и нажмите Enter…"
            value={draft}
            disabled={busy}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={onKey}
          />
          <kbd aria-hidden="true">
            <CornerDownLeft size={13} />
          </kbd>
        </label>
      )}

      <div className="board-footer scout-footer">
        <div className="actions">
          <Button variant="outline" disabled={busy} onClick={toggleBulk}>
            {bulk === null ? <SquarePen size={15} /> : <LayoutGrid size={15} />}
            {bulk === null ? 'Массовый' : 'Карточки'}
          </Button>
          <Button variant="outline" onClick={() => setDialog('settings')}>
            <SlidersHorizontal size={15} /> Настройки
          </Button>
          <Menu label="Ещё">
            {close => (
              <>
                <button
                  type="button"
                  role="menuitem"
                  className="menu-item"
                  onClick={() => {
                    close();
                    setDialog('stats');
                  }}
                >
                  <BarChart3 size={15} /> Статистика источников
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="menu-item"
                  disabled={!list.length}
                  onClick={() => {
                    close();
                    void copyList();
                  }}
                >
                  <Copy size={15} /> Скопировать список
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="menu-item danger"
                  disabled={!list.length}
                  onClick={() => {
                    close();
                    setDialog('clear');
                  }}
                >
                  <Trash2 size={15} /> Удалить все источники
                </button>
              </>
            )}
          </Menu>
        </div>
        {active.length ? (
          <Button disabled={busy} onClick={() => void stop()}>
            <Square size={14} /> Остановить
          </Button>
        ) : (
          <Button
            disabled={busy || !enabled}
            title={enabled ? 'Запустить на отмеченных аккаунтах' : 'Включите хотя бы один источник'}
            onClick={() => void parse()}
          >
            <Radar size={15} /> Только парсинг
          </Button>
        )}
      </div>
      <ErrorToast message={error} />
      <p className="board-status" role="status">
        {active.length ? (
          <span className={attention ? 'board-warning' : undefined}>
            Идёт парсинг · {scoutRunsLabel(active)}
          </span>
        ) : (
          notice || 'Остановлен'
        )}
      </p>

      {dialog === 'settings' && <WorkSettingsModal onClose={() => setDialog(null)} />}
      {dialog === 'stats' && (
        <Modal
          title={`Источники · включено ${enabled} из ${list.length}`}
          wide
          onClose={() => setDialog(null)}
        >
          <SourceTable rows={list} busy={busy} act={act} />
        </Modal>
      )}
      {dialog === 'clear' && (
        <Modal title="Удалить все источники?" onClose={() => setDialog(null)}>
          <p className="helper">
            Список SMM-источников будет очищен. Найденные лиды и память парсера о разобранных публикациях
            останутся.
          </p>
          <div className="actions">
            <Button variant="danger" disabled={busy} onClick={clearAll}>
              <Trash2 size={15} /> Удалить все
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </section>
  );
}
