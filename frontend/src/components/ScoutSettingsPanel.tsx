import { useEffect, useState } from 'react';
import { KeyRound, Save } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { ScoutMethod, SettingsData } from '../services/types';
import { Button } from './ui/button';

const methods: { id: ScoutMethod; label: string; hint?: string }[] = [
  { id: 'posts', label: 'Posts / Reels', hint: 'автор и соавторы публикаций' },
  { id: 'comments', label: 'Комментарии', hint: 'авторы комментариев' },
  { id: 'tagged', label: 'Tagged posts' },
  { id: 'stories', label: 'Stories', hint: 'пока отключено' },
  { id: 'followers', label: 'Followers' },
  { id: 'following', label: 'Following' },
];
const profileTypes = [
  { id: 'artists', label: 'Только артисты' },
  { id: 'artists_producers', label: 'Артисты + продюсеры' },
  { id: 'everyone', label: 'Все' },
] as const;
const aiModes = [
  { id: 'off', label: 'Выкл.' },
  { id: 'uncertain', label: 'Если не уверен' },
  { id: 'always', label: 'Всегда' },
] as const;

export function ScoutSettingsPanel() {
  const resource = useResource<SettingsData>('settings.get');
  const ai = useResource<{ configured: boolean }>('ai.status');
  const [settings, setSettings] = useState<SettingsData>();
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    if (resource.data) setSettings(resource.data);
  }, [resource.data]);
  if (!settings) return null;
  const update = <K extends keyof SettingsData>(name: K, value: SettingsData[K]) =>
    setSettings(current => (current ? { ...current, [name]: value } : current));
  const toggleMethod = (method: ScoutMethod, on: boolean) =>
    update(
      'scout_methods',
      on ? [...settings.scout_methods, method] : settings.scout_methods.filter(item => item !== method),
    );
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await operation();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  const follow = settings.scout_methods.includes('followers') || settings.scout_methods.includes('following');
  const numberField = (name: keyof SettingsData, label: string, min: number, max: number) => (
    <label>
      {label}
      <input
        type="number"
        min={min}
        max={max}
        value={settings[name] as number}
        disabled={busy}
        onChange={event => update(name, Number(event.target.value) as never)}
      />
    </label>
  );
  return (
    <details className="panel scout-settings" open>
      <summary>
        <h2>Настройки Scout</h2>
      </summary>
      <div className="scout-settings-grid">
        <section>
          <h3>Методы поиска</h3>
          <div className="check-list">
            {methods.map(method => (
              <label key={method.id} className={method.id === 'stories' ? 'muted' : ''}>
                <input
                  type="checkbox"
                  checked={settings.scout_methods.includes(method.id)}
                  disabled={busy || method.id === 'stories'}
                  onChange={event => toggleMethod(method.id, event.target.checked)}
                />
                {method.label}
                {method.hint && <small>{method.hint}</small>}
              </label>
            ))}
          </div>
          {follow && (
            <div className="field-row">
              {numberField('scout_follow_page_size', 'Строк на страницу', 5, 50)}
              {numberField('scout_follow_delay_seconds', 'Пауза между страницами, с', 1, 10)}
              {numberField('scout_follow_max', 'Максимум кандидатов', 1, 1000)}
            </div>
          )}
        </section>
        <section>
          <h3>Фильтры</h3>
          <div className="segmented" role="radiogroup" aria-label="Тип профиля">
            {profileTypes.map(type => (
              <button
                key={type.id}
                type="button"
                role="radio"
                aria-checked={settings.scout_profile_type === type.id}
                className={settings.scout_profile_type === type.id ? 'active' : ''}
                disabled={busy}
                onClick={() => update('scout_profile_type', type.id)}
              >
                {type.label}
              </button>
            ))}
          </div>
          <div className="field-row">
            {numberField('scout_min_followers', 'Подписчиков от', 0, 1_000_000_000)}
            {numberField('scout_max_followers', 'Подписчиков до', 0, 1_000_000_000)}
          </div>
          <div className="check-list">
            {(
              [
                ['scout_only_contacts', 'Только профили с контактами'],
                ['scout_skip_processed', 'Пропускать уже обработанные профили'],
                ['scout_skip_recent_sources', 'Пропускать недавно просканированные источники'],
              ] as const
            ).map(([name, label]) => (
              <label key={name}>
                <input
                  type="checkbox"
                  checked={settings[name]}
                  disabled={busy}
                  onChange={event => update(name, event.target.checked)}
                />
                {label}
              </label>
            ))}
          </div>
          <div className="field-row">
            {numberField('scout_source_cooldown_hours', 'Кулдаун источника, ч', 0, 2160)}
            {numberField('scout_sources_per_run', 'Источников за запуск', 1, 500)}
          </div>
        </section>
        <section>
          <h3>AI-классификация</h3>
          <div className="segmented" role="radiogroup" aria-label="Режим AI">
            {aiModes.map(mode => (
              <button
                key={mode.id}
                type="button"
                role="radio"
                aria-checked={settings.scout_ai_mode === mode.id}
                className={settings.scout_ai_mode === mode.id ? 'active' : ''}
                disabled={busy}
                onClick={() => update('scout_ai_mode', mode.id)}
              >
                {mode.label}
              </button>
            ))}
          </div>
          <label>
            Модель OpenRouter
            <input
              value={settings.scout_ai_model}
              maxLength={120}
              disabled={busy}
              onChange={event => update('scout_ai_model', event.target.value)}
            />
          </label>
          <label>
            Ключ OpenRouter {ai.data?.configured ? <small>· сохранён</small> : <small>· не задан</small>}
            <span className="input-with-icon">
              <KeyRound size={15} aria-hidden="true" />
              <input
                type="password"
                autoComplete="off"
                placeholder={ai.data?.configured ? 'Введите новый ключ, чтобы заменить' : 'sk-or-…'}
                value={key}
                disabled={busy}
                onChange={event => setKey(event.target.value)}
              />
            </span>
          </label>
          <div className="actions">
            <Button
              variant="outline"
              disabled={busy || !key.trim()}
              onClick={() =>
                void run(async () => {
                  await api.request('ai.set_key', { key });
                  setKey('');
                  ai.refresh();
                  setMessage('Ключ сохранён в защищённом хранилище Windows.');
                })
              }
            >
              Сохранить ключ
            </Button>
            {ai.data?.configured && (
              <Button
                variant="danger"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api.request('ai.set_key', { key: '' });
                    ai.refresh();
                    setMessage('Ключ удалён.');
                  })
                }
              >
                Удалить ключ
              </Button>
            )}
          </div>
          <p className="helper">
            AI только уточняет категорию; решение о лиде принимают фильтры. Без ключа используется локальная
            классификация. Профиль отправляется в OpenRouter только при включённом AI.
          </p>
        </section>
      </div>
      <div className="board-footer">
        <Button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const saved = await api.request<SettingsData>('settings.save', settings);
              setSettings(saved);
              setMessage('Настройки Scout сохранены. Применяются к следующим профилям и запускам.');
            })
          }
        >
          <Save size={16} /> Сохранить настройки
        </Button>
        {message && (
          <span role="status" className="board-status">
            {message}
          </span>
        )}
        {error && (
          <span role="alert" className="error-text">
            {error}
          </span>
        )}
      </div>
    </details>
  );
}
