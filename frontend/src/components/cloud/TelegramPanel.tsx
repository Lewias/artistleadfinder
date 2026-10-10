import { useState } from 'react';
import { Send, Unlink } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { TelegramLink, TelegramStatus } from '../../services/types';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { errorText } from '../../lib/errors';

/** The Telegram bot: start the cloud parser from the phone and hear when it is done. */
export function TelegramPanel() {
  const [link, setLink] = useState<TelegramLink>();
  const [error, setError] = useState('');
  // While a code is shown the state is checked often, so the panel sees the link at once.
  const status = useResource<TelegramStatus>('cloud.telegram', {}, link ? 4000 : 60000);
  const linked = status.data?.linked === true;

  const connect = async () => {
    setError('');
    try {
      const fresh = await api.request<TelegramLink>('cloud.telegram_link', {});
      setLink(fresh);
      await api.openProfile(fresh.url);
    } catch (err) {
      setError(errorText(err));
    }
  };
  const unlink = async () => {
    setError('');
    try {
      await api.request('cloud.telegram_unlink', {});
      setLink(undefined);
      status.refresh();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <section className="panel board telegram-panel">
      <div className="board-heading">
        <h2>
          <Send size={16} /> Telegram-бот
        </h2>
        {linked ? (
          <Button variant="outline" onClick={() => void unlink()}>
            <Unlink size={14} /> Отвязать
          </Button>
        ) : (
          <Button onClick={() => void connect()}>
            <Send size={14} /> {link ? 'Новый код' : 'Подключить Telegram'}
          </Button>
        )}
      </div>
      {linked ? (
        <p className="helper">
          Подключён{status.data?.username ? ` @${status.data.username}` : ''}
          {status.data?.bot ? ` к боту @${status.data.bot}` : ''}. В боте «🔎 Найти артистов» спросит аккаунт
          и сколько лидов найти, источники и настройки возьмёт из последнего облачного поиска. «✉️ Написать
          лидам» разошлёт сообщения новым лидам с аккаунта с прокси — сообщения и паузы из последней облачной
          рассылки. Ещё бот показывает статус, останавливает задачи и пишет, когда они закончены.
          {status.data?.notify === false ? ' Уведомления выключены (/notify в боте).' : ''}
        </p>
      ) : link ? (
        <p className="helper">
          В Telegram откроется @{link.bot} — нажмите «Запустить». Если не открылось, отправьте боту{' '}
          <code className="telegram-code">/start {link.code}</code>. Код действует 15 минут.
        </p>
      ) : (
        <p className="helper">
          Запускайте облачный парсер с телефона и получайте сообщение, когда он закончит.
        </p>
      )}
      <ErrorToast message={error || status.error || ''} />
    </section>
  );
}
