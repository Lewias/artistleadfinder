import { useState } from 'react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { OutreachSender } from '../../services/types';
import { Button } from '../ui/button';
import { StatusBadge } from '../DataState';
import { dateTime, senderStatusLabels } from './outreachText';

/** Sender accounts (existing browser profiles) and their outreach health. */
export function SendersPanel() {
  const senders = useResource<OutreachSender[]>('outreach.senders', {}, 5000);
  const [error, setError] = useState('');
  const set = async (id: string, status: string) => {
    setError('');
    try {
      await api.request('outreach.sender_status', { id, status });
      senders.refresh();
    } catch (err) {
      setError(String(err));
    }
  };
  return (
    <section className="panel">
      <h2>Аккаунты-отправители</h2>
      <p className="helper">
        Используются подключённые аккаунты из раздела «Аккаунты». Если Instagram просит войти, показывает
        checkpoint или ограничивает действия, аккаунт останавливается: сообщения ждут, пока вы не решите
        проблему в окне браузера и не нажмёте «Возобновить». Ограничение Instagram снимается само после
        перерыва из настроек. Другие аккаунты автоматически не подставляются.
      </p>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Аккаунт</th>
              <th>Состояние</th>
              <th>Окно</th>
              <th>За 24 ч</th>
              <th>Последнее</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(senders.data || []).map(sender => (
              <tr key={sender.id}>
                <td>
                  <strong>{sender.name}</strong>
                  {!sender.has_session && <small className="cell-sub">Нет сохранённой сессии</small>}
                </td>
                <td>
                  <StatusBadge
                    value={
                      sender.status === 'active'
                        ? 'completed'
                        : sender.status === 'paused'
                          ? 'paused'
                          : 'failed'
                    }
                    label={senderStatusLabels[sender.status]}
                  />
                  {(sender.reason || sender.until) && (
                    <small className="cell-sub">
                      {sender.reason}
                      {sender.until ? ` · до ${dateTime(sender.until)}` : ''}
                    </small>
                  )}
                </td>
                <td>{sender.open ? 'Открыто' : 'Закрыто'}</td>
                <td>
                  {sender.sent_24h} / {sender.daily_limit}
                </td>
                <td>{dateTime(sender.last_sent_at)}</td>
                <td className="row-actions">
                  {sender.status === 'active' ? (
                    <>
                      <Button variant="outline" onClick={() => void set(sender.id, 'paused')}>
                        Пауза
                      </Button>
                      <Button variant="outline" onClick={() => void set(sender.id, 'disabled')}>
                        Отключить
                      </Button>
                    </>
                  ) : (
                    <Button variant="outline" onClick={() => void set(sender.id, 'active')}>
                      Возобновить
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!senders.data?.length && (
          <p className="empty-copy">Нет аккаунтов. Добавьте их в разделе «Аккаунты».</p>
        )}
      </div>
    </section>
  );
}
