import { useState } from 'react';
import { MessagesSquare, Send } from 'lucide-react';
import { Modal } from '../Modal';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { useResource } from '../../hooks/useResource';
import { time, useCall } from './emailUi';
import type { EmailThread, EmailThreadMessage } from '../../services/types';

const KIND_LABELS: Record<EmailThreadMessage['kind'], string> = {
  campaign: 'Письмо кампании',
  followup: 'Фоллоу-ап',
  reply: 'Ответ',
  manual: 'Из CRM',
};

/** The whole conversation with one address and an answer in the same Gmail thread. */
export function EmailThreadModal({ address, onClose }: { address: string; onClose: () => void }) {
  const thread = useResource<EmailThread>('email.thread', { address }, 30000);
  const { call, error } = useCall(thread.refresh);
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const data = thread.data;
  const send = async () => {
    setBusy(true);
    const result = await call<EmailThread>('email.reply', { address, body });
    setBusy(false);
    if (result) setBody('');
  };
  return (
    <Modal title={address} onClose={onClose} wide icon={<MessagesSquare size={18} />}>
      <div className="email-thread">
        {data?.messages.length === 0 && <p className="helper">Писем с этим адресом пока нет.</p>}
        {data?.messages.map((message, index) => (
          <article key={index} className={`email-thread-message ${message.direction}`}>
            <header>
              <strong>{message.direction === 'in' ? address : message.account || 'Наш ящик'}</strong>
              <span className="helper">
                {KIND_LABELS[message.kind]} · {time(message.at)}
              </span>
            </header>
            {index === 0 && <p className="email-thread-subject">{message.subject}</p>}
            <p className="email-thread-body">{message.body}</p>
          </article>
        ))}
      </div>
      {data?.unsubscribed && (
        <p className="helper">
          Этот адрес просил не писать: рассылки к нему не уходят, отвечайте только по делу.
        </p>
      )}
      {data && !data.can_reply && <p className="helper">{data.blocker}</p>}
      {data?.can_reply && (
        <div className="email-thread-reply">
          <label className="cloud-field">
            <span>Ответ с {data.from} в ту же ветку</span>
            <textarea
              rows={4}
              value={body}
              placeholder="Пишите как живой человек: коротко и по делу."
              onChange={event => setBody(event.target.value)}
            />
          </label>
          <div className="actions modal-actions">
            <Button disabled={busy || !body.trim()} onClick={() => void send()}>
              <Send size={14} /> {busy ? 'Отправляю…' : 'Отправить'}
            </Button>
          </div>
        </div>
      )}
      <ErrorToast message={error || thread.error} />
    </Modal>
  );
}
