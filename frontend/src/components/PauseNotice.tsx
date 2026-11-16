import { useEffect, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { api } from '../services/api';
import { reportError } from '../lib/toast';

/** Sending paused from the tray: said in the window too, with the way back. */
export function PauseNotice() {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    let gone = false;
    let off: (() => void) | undefined;
    api
      .automation()
      .then(value => !gone && setPaused(value))
      .catch(() => undefined);
    listen<boolean>('automation-paused', event => setPaused(event.payload))
      .then(unlisten => (gone ? unlisten() : (off = unlisten)))
      .catch(() => undefined);
    return () => {
      gone = true;
      off?.();
    };
  }, []);
  if (!paused) return null;
  return (
    <span role="status" className="core-status pending">
      <i />
      Рассылка на паузе ·
      <button
        type="button"
        className="link-button"
        onClick={() => void api.automation(false).then(setPaused).catch(reportError)}
      >
        продолжить
      </button>
    </span>
  );
}
