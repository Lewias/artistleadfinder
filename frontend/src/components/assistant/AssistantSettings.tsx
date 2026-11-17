import { TelegramPanel } from './TelegramPanel';

/** The assistant is ready to use; its only user setting is the Telegram connection. */
export function AssistantSettings() {
  return (
    <section className="panel assistant-settings" aria-label="Подключение Telegram">
      <TelegramPanel />
    </section>
  );
}
