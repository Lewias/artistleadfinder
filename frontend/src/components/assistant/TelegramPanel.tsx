import { useState } from 'react';
import { Send, Unlink } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { TelegramLink, TelegramStatus } from '../../services/types';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { errorText } from '../../lib/errors';

/** The assistant in Telegram: a linked chat asks it questions and gets its answers. */
export function TelegramPanel() {
  const [link, setLink] = useState<TelegramLink>();
  const [error, setError] = useState('');
  // While a code is shown the state is checked often, so the panel sees the link at once.
  const status = useResource<TelegramStatus>('telegram.state', {}, link ? 4000 : 60000);
  const linked = status.data?.linked === true;

  const connect = async () => {
    setError('');
    try {
      const fresh = await api.request<TelegramLink>('telegram.link', {});
      setLink(fresh);
      await api.openProfile(fresh.url);
    } catch (err) {
      setError(errorText(err));
    }
  };
  const unlink = async () => {
    setError('');
    try {
      await api.request('telegram.unlink', {});
      setLink(undefined);
      status.refresh();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="telegram-panel">
      <div className="board-heading">
        <h3>
          <Send size={15} /> Ассистент в Telegram
        </h3>
        {linked ? (
          <Button variant="outline" onClick={() => void unlink()}>
            <Unlink size={14} /> Отвязать
          </Button>
        ) : (
          <Button variant="outline" onClick={() => void connect()}>
            <Send size={14} /> {link ? 'Новый код' : 'Подключить Telegram'}
          </Button>
        )}
      </div>
      {linked ? (
        <p className="helper">
          Подключён{status.data?.username ? ` @${status.data.username}` : ''}
          {status.data?.bot ? ` к боту @${status.data.bot}` : ''}. Напишите боту вопрос — он придёт
          ассистенту в это приложение, а ответ вернётся в чат. Ассистент отвечает, пока приложение
          запущено; иначе вопрос ждёт до суток. Сообщения артистам он только готовит: отправляете вы
          кнопкой в разделе «Ассистент». /new в боте начинает новый чат.
        </p>
      ) : link ? (
        <p className="helper">
          В Telegram откроется @{link.bot} — нажмите «Запустить». Если не открылось, отправьте боту{' '}
          <code className="telegram-code">/start {link.code}</code>. Код действует 15 минут.
        </p>
      ) : (
        <p className="helper">
          Задавайте вопросы ассистенту с телефона и получайте ответы в Telegram, пока приложение
          запущено на компьютере.
        </p>
      )}
      <ErrorToast message={error || status.error || ''} />
    </div>
  );
}
