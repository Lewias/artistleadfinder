import { useState } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import type { UpdaterState } from '../hooks/useUpdater';
import { Button } from './ui/button';
import { Modal } from './Modal';

interface Props {
  state: UpdaterState;
  install: () => Promise<void>;
}

/** Topbar pill when a new version is out, and the install dialog. */
export function UpdateNotice({ state, install }: Props) {
  const [open, setOpen] = useState(false);
  const active = state.status === 'downloading' || state.status === 'installing';
  if (!state.version || !['available', 'downloading', 'installing', 'error'].includes(state.status))
    return null;
  return (
    <>
      <button type="button" className="update-pill" onClick={() => setOpen(true)}>
        <Download size={14} /> Обновление {state.version}
      </button>
      {open && (
        <Modal title={`Версия ${state.version}`} onClose={() => !active && setOpen(false)}>
          {state.notes && <p className="update-notes">{state.notes}</p>}
          <p className="helper">
            Приложение скачает обновление, установит его и перезапустится. База, лиды, списки рассылки и
            аккаунты сохранятся. Идущий парсинг прервётся; очередь рассылки продолжится после перезапуска,
            сообщение в момент отправки не будет отправлено повторно.
          </p>
          {active && (
            <div className="update-progress">
              <progress max={100} value={state.percent ?? undefined} />
              <span className="helper">
                {state.status === 'installing' ? 'Установка…' : `Загрузка ${state.percent ?? 0}%`}
              </span>
            </div>
          )}
          {state.status === 'error' && state.error && (
            <p role="alert" className="error-text">
              {state.error}
            </p>
          )}
          <div className="actions">
            <Button disabled={active} onClick={() => void install()}>
              <RefreshCw size={15} /> {state.status === 'error' ? 'Повторить' : 'Обновить и перезапустить'}
            </Button>
            <Button variant="outline" disabled={active} onClick={() => setOpen(false)}>
              Позже
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}

const statusText: Partial<Record<UpdaterState['status'], string>> = {
  checking: 'Проверяем…',
  none: 'Установлена последняя версия.',
  downloading: 'Загрузка обновления…',
  installing: 'Установка…',
};

/** Settings block: current version and a manual check. */
export function UpdatesPanel({
  version,
  state,
  check,
  install,
}: Props & { version?: string; check: () => Promise<void> }) {
  return (
    <section className="panel updates-panel">
      <div>
        <h2>Обновления</h2>
        <p className="helper">
          Версия {version ?? '—'}. Новые версии проверяются при запуске и каждые 6 часов и устанавливаются
          только по кнопке.
        </p>
        {state.status === 'available' && <p className="helper">Доступна версия {state.version}.</p>}
        {statusText[state.status] && <p className="helper">{statusText[state.status]}</p>}
        {state.status === 'error' && state.error && (
          <p role="alert" className="error-text">
            Не удалось проверить обновления: {state.error}
          </p>
        )}
      </div>
      <div className="actions">
        <Button
          variant="outline"
          disabled={['checking', 'downloading', 'installing'].includes(state.status)}
          onClick={() => void check()}
        >
          <RefreshCw size={15} /> Проверить обновления
        </Button>
        {state.status === 'available' && (
          <Button onClick={() => void install()}>
            <Download size={15} /> Обновить до {state.version}
          </Button>
        )}
      </div>
    </section>
  );
}
