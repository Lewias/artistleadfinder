import { useState } from 'react';
import { useResource } from '../hooks/useResource';
import type { InstagramWarmupHistoryRun, InstagramWarmupHistoryDetail } from '../services/types';
import { DataState, StatusBadge } from './DataState';
import { Button } from './ui/button';
import { number } from '../lib/format';

const STATUS = {
  running: ['running', 'Выполняется'],
  done: ['completed', 'Завершён'],
  stopped: ['cancelled', 'Остановлен'],
  interrupted: ['cancelled', 'Прерван'],
  failed: ['failed', 'Ошибка'],
} as const;

const timestamp = (value: number) => new Date(value * 1000).toLocaleString('ru-RU');
function duration(run: InstagramWarmupHistoryRun) {
  const seconds = Math.max(0, Math.floor((run.completed_at ?? Date.now() / 1000) - run.started_at));
  return `${Math.floor(seconds / 60)} мин. ${seconds % 60} с.`;
}

export function InstagramWarmupHistory({ runs }: { runs: InstagramWarmupHistoryRun[] }) {
  const [selected, setSelected] = useState<string>();
  const visible = selected && runs.some(run => run.id === selected);
  return (
    <section>
      <div className="section-heading">
        <h2>Прогрев Instagram</h2>
      </div>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Аккаунт</th>
              <th>Начало</th>
              <th>Статус</th>
              <th>Сообщения</th>
              <th>Лайки</th>
              <th>Подписки</th>
              <th>Длительность</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(run => (
              <tr key={run.id}>
                <td>
                  <button className="text-action" onClick={() => setSelected(run.id)}>
                    {run.profile_name}
                  </button>
                </td>
                <td>{timestamp(run.started_at)}</td>
                <td>
                  <StatusBadge value={STATUS[run.status][0]} label={STATUS[run.status][1]} />
                </td>
                <td>
                  {number(run.sent)} / {number(run.targets.messages)}
                </td>
                <td>
                  {number(run.liked)} / {number(run.targets.likes)}
                </td>
                <td>
                  {number(run.followed)} / {number(run.targets.follows)}
                </td>
                <td>{duration(run)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.length === 0 && <p className="empty-copy">Запусков прогрева пока нет.</p>}
      </div>
      {visible && <WarmupDetail key={selected} id={selected} onClose={() => setSelected(undefined)} />}
    </section>
  );
}

function WarmupDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const resource = useResource<InstagramWarmupHistoryDetail>('instagram_warmup.detail', { id }, 2000);
  const run = resource.data;
  return (
    <div className="history-detail">
      <div className="section-heading">
        <h2>Прогрев · {run?.profile_name ?? 'Загрузка…'}</h2>
        <Button variant="outline" onClick={onClose}>
          Скрыть
        </Button>
      </div>
      <DataState {...resource} retry={resource.refresh} />
      {run && (
        <>
          <section className="panel configuration">
            <h2>Результаты запуска</h2>
            <dl>
              {[
                ['Начало', timestamp(run.started_at)],
                ['Завершение', run.completed_at ? timestamp(run.completed_at) : 'Выполняется'],
                ['Статус', STATUS[run.status][1]],
                ['Сообщения', `${run.sent} / ${run.targets.messages}`],
                ['Лайки', `${run.liked} / ${run.targets.likes}`],
                ['Подписки', `${run.followed} / ${run.targets.follows}`],
                ['Профилей проверено', number(run.checked)],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            {run.settings && (
              <>
                <h3>Настройки этого запуска</h3>
                <p>{run.settings.queries.join(' · ')}</p>
                <p className="helper">Пауза между шагами: {run.settings.delay_seconds} сек.</p>
                <p style={{ whiteSpace: 'pre-wrap' }}>{run.settings.message || 'Сообщения отключены'}</p>
              </>
            )}
          </section>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Профиль</th>
                  <th>Результат</th>
                </tr>
              </thead>
              <tbody>
                {[...run.events].reverse().map((event, index) => (
                  <tr key={`${event.at}-${index}`}>
                    <td>{timestamp(event.at)}</td>
                    <td>{event.username ? `@${event.username}` : '—'}</td>
                    <td>{event.text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
