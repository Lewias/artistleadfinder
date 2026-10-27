import { useState } from 'react';
import { KeyRound } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { AssistantState } from '../../services/types';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { errorText } from '../../lib/errors';
import { dollars } from '../../lib/format';

export function KeyForm({ onSaved, compact }: { onSaved: () => void; compact?: boolean }) {
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const save = async () => {
    setError('');
    try {
      await api.request('assistant.set_key', { key });
      setKey('');
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <div className="assistant-key">
      {!compact && (
        <p className="helper">
          Ассистент работает через API Anthropic. Создайте ключ на console.anthropic.com (раздел API Keys) и
          вставьте сюда — он хранится зашифрованным на этом компьютере. Обычная работа на Haiku 5.5 стоит
          центы в день; сложные ответы Haiku сам передаёт Sonnet 5.5.
        </p>
      )}
      <div className="assistant-key-row">
        <input
          type="password"
          placeholder="sk-ant-…"
          value={key}
          autoComplete="off"
          onChange={event => setKey(event.target.value)}
        />
        <Button disabled={!key.trim()} onClick={() => void save()}>
          <KeyRound size={14} /> Сохранить ключ
        </Button>
      </div>
      <ErrorToast message={error} />
    </div>
  );
}

/** «Настройки» → «Ассистент»: the key, the stronger model, access to Messages and the spend. */
export function AssistantSettings() {
  const state = useResource<AssistantState>('assistant.state');
  const data = state.data;
  const [changingKey, setChangingKey] = useState(false);
  const [error, setError] = useState('');
  const save = async (params: object) => {
    setError('');
    try {
      await api.request('assistant.settings', params);
      state.refresh();
    } catch (err) {
      setError(errorText(err));
    }
  };
  if (!data) return null;
  return (
    <section className="panel assistant-settings">
      <h2>Ассистент</h2>
      {!data.configured || changingKey ? (
        <KeyForm
          compact={data.configured}
          onSaved={() => {
            setChangingKey(false);
            state.refresh();
          }}
        />
      ) : (
        <>
          <div className="cloud-counters">
            <div>
              <span>Сегодня</span>
              <b>{dollars(data.usage.today)}</b>
            </div>
            <div>
              <span>30 дней</span>
              <b>{dollars(data.usage.month)}</b>
            </div>
            {Object.entries(data.usage.by_model).map(([model, value]) => (
              <div key={model}>
                <span>{model}</span>
                <b>{dollars(value)}</b>
              </div>
            ))}
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={data.escalation}
              onChange={event => void save({ escalation: event.target.checked })}
            />
            {data.models.main} сам передаёт сложные ответы {data.models.strong}
          </label>
          <Button variant="outline" onClick={() => setChangingKey(true)}>
            <KeyRound size={14} /> Сменить ключ Anthropic
          </Button>
        </>
      )}
      <p className={data.messages.available ? 'helper' : 'board-warning'}>
        {data.messages.available ? 'Переписка iMessage на этом Mac доступна.' : data.messages.reason}
      </p>
      <p className="helper">{data.mac_send.description}</p>
      <ErrorToast message={error || state.error} />
    </section>
  );
}
