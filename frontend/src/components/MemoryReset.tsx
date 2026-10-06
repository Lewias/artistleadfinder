import { useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { api } from '../services/api';
import { errorText } from '../lib/errors';
import { Button } from './ui/button';
import { Modal } from './Modal';
import { ErrorToast } from './Toaster';

type Part = {
  method: string;
  title: string;
  text: string;
  forgets: string;
  keeps: string;
  done: (result: Record<string, number>) => string;
};

const parts: Part[] = [
  {
    method: 'scout.reset_memory',
    title: 'Парсер',
    text: 'Источники, публикации и профили снова будут проверяться с нуля.',
    forgets:
      'Разобранные профили и публикации, кэш профилей и AI, историю поисков, кулдаун источников, очередь источников и счётчики найденного у аккаунтов.',
    keeps: 'Лиды в базе, их данные и откуда они найдены, список источников, настройки.',
    done: () => 'Память парсера сброшена. Лиды на месте.',
  },
  {
    method: 'outreach.reset_memory',
    title: 'Рассылка Instagram',
    text: 'Рассылка забудет, кому уже писала, и сможет написать им снова.',
    forgets:
      'Кампании, очереди, отправленные сообщения, переписки, follow-up, состояние аккаунтов-отправителей и отметки «уже писали» у лидов.',
    keeps: 'Лиды, шаблоны, список рассылки, тексты, отмеченные аккаунты и CRM.',
    done: result => `Память рассылки сброшена. Снова доступны для рассылки: ${result.leads_reopened ?? 0}.`,
  },
  {
    method: 'imessage.reset_memory',
    title: 'iMessage',
    text: 'iMessage забудет, на какие номера уже отправляла, и сможет отправить снова.',
    forgets: 'Кампании, отправки по номерам и журнал iMessage.',
    keeps: 'Список номеров, тексты, шаблоны, вложения и настройки моста.',
    done: () => 'Память iMessage сброшена.',
  },
];

/** «Память»: forget the parser's, outreach's or iMessage's history; leads stay. */
export function MemoryReset() {
  const [open, setOpen] = useState<Part | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const reset = async (part: Part) => {
    setBusy(true);
    setError('');
    try {
      const result = await api.request<Record<string, number>>(part.method);
      setMessage(part.done(result ?? {}));
      setOpen(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel">
      <h2>Память</h2>
      <p className="helper">
        Сброс стирает историю работы, но не лидов. Пока идёт парсинг или рассылка, сбросить их память нельзя.
      </p>
      <div className="memory-reset">
        {parts.map(part => (
          <div className="memory-reset-row" key={part.method}>
            <div>
              <strong>{part.title}</strong>
              <span className="helper">{part.text}</span>
            </div>
            <Button
              variant="outline"
              onClick={() => {
                setError('');
                setMessage('');
                setOpen(part);
              }}
            >
              <RotateCcw size={14} /> Сбросить
            </Button>
          </div>
        ))}
      </div>
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      {open && (
        <Modal title={`Сбросить память: ${open.title}?`} onClose={() => !busy && setOpen(null)}>
          <p className="helper">
            <b>Удалится:</b> {open.forgets}
          </p>
          <p className="helper">
            <b>Останется:</b> {open.keeps}
          </p>
          <p className="helper">Отменить сброс нельзя.</p>
          <ErrorToast message={error} />
          <div className="actions">
            <Button variant="danger" disabled={busy} onClick={() => void reset(open)}>
              <RotateCcw size={15} /> Сбросить
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => setOpen(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </section>
  );
}
