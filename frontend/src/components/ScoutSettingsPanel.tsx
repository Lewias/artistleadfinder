import { ErrorToast } from './Toaster';
import { errorText } from '../lib/errors';
import { useEffect, useState, type ReactNode } from 'react';
import { KeyRound, Save } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { ScoutMethod, SettingsData } from '../services/types';
import { Button } from './ui/button';

const methods: { id: ScoutMethod; title: string; text: string; experimental?: boolean }[] = [
  {
    id: 'profiles',
    title: 'Проверить профили',
    text: 'Каждый username из списка проверяется как конечный профиль. Посты и связи источника не обходятся.',
  },
  { id: 'posts', title: 'Посты', text: 'Последние посты/рилсы, авторы и соавторы.' },
  { id: 'tagged', title: 'Отметки', text: 'Проверяет посты, где источник отмечен.' },
  {
    id: 'followers',
    title: 'Подписчики',
    text: 'Список собирается пакетами; каждый профиль проверяется отдельно.',
    experimental: true,
  },
  {
    id: 'following',
    title: 'Подписки',
    text: 'Список собирается пакетами; каждый профиль проверяется отдельно.',
    experimental: true,
  },
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

const hours = (value: number) => {
  const tens = value % 100;
  const ones = value % 10;
  if (ones === 1 && tens !== 11) return `${value} час`;
  if (ones >= 2 && ones <= 4 && (tens < 12 || tens > 14)) return `${value} часа`;
  return `${value} часов`;
};

function Option(props: {
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
  title: string;
  text: string;
  badge?: string;
  muted?: boolean;
}) {
  return (
    <label className={`scout-option${props.muted ? ' muted' : ''}`}>
      <input
        type="checkbox"
        checked={props.checked}
        disabled={props.disabled}
        onChange={event => props.onChange(event.target.checked)}
      />
      <span className="scout-option-title">
        {props.title}
        {props.badge && <span className="scout-badge">{props.badge}</span>}
      </span>
      <small>{props.text}</small>
    </label>
  );
}

function Block({ title, wide, children }: { title: string; wide?: boolean; children: ReactNode }) {
  return (
    <section className={`scout-block${wide ? ' wide' : ''}`}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

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
  const has = (method: ScoutMethod) => settings.scout_methods.includes(method);
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
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const profileCheck = has('profiles');
  const numberField = (
    name: keyof SettingsData,
    label: string,
    min: number,
    max: number,
    options: { step?: number; hint?: string; disabled?: boolean } = {},
  ) => (
    <label>
      {label}
      <input
        type="number"
        min={min}
        max={max}
        step={options.step ?? 1}
        value={settings[name] as number}
        disabled={busy || options.disabled}
        onChange={event => update(name, Number(event.target.value) as never)}
      />
      {options.hint && <small>{options.hint}</small>}
    </label>
  );
  const option = (
    name: 'scout_only_contacts' | 'scout_add_to_outreach' | 'scout_use_followers_range',
    title: string,
    text: string,
  ) => (
    <Option
      checked={settings[name]}
      disabled={busy}
      onChange={checked => update(name, checked)}
      title={title}
      text={text}
    />
  );
  return (
    <div className="scout-settings">
      <Block title="Что проверять" wide>
        <div className="scout-options">
          {methods.map(method => (
            <Option
              key={method.id}
              checked={has(method.id)}
              disabled={busy}
              onChange={checked => toggleMethod(method.id, checked)}
              title={method.title}
              text={method.text}
              badge={method.experimental ? 'экспериментально' : undefined}
              muted={profileCheck && method.id !== 'profiles'}
            />
          ))}
        </div>
        {profileCheck && (
          <p className="helper">
            Включена проверка профилей: источники проверяются сами, их сторис, посты и подписки не обходятся.
          </p>
        )}
      </Block>

      <div className="scout-blocks">
        <Block title="Кого сохранять">
          <label>
            Сохранять
            <select
              value={settings.scout_profile_type}
              disabled={busy}
              onChange={event =>
                update('scout_profile_type', event.target.value as SettingsData['scout_profile_type'])
              }
            >
              {profileTypes.map(type => (
                <option key={type.id} value={type.id}>
                  {type.label}
                </option>
              ))}
            </select>
            <small>Этот режим решает, какие найденные профили попадут в Лиды.</small>
          </label>
          {option(
            'scout_only_contacts',
            'Только с контактами',
            'Сохранять только лиды, где найден email или телефон.',
          )}
          {option(
            'scout_add_to_outreach',
            'В «Первичную рассылку»',
            'Новые лиды сразу попадают в список рассылки. Сами по себе сообщения не отправляются.',
          )}
        </Block>

        <Block title="Диапазон подписчиков">
          {option(
            'scout_use_followers_range',
            'Учитывать подписчиков',
            'Сохранять только профили внутри этого диапазона.',
          )}
          <div className="field-row">
            {numberField('scout_min_followers', 'От', 0, 1_000_000_000, {
              disabled: !settings.scout_use_followers_range,
            })}
            {numberField('scout_max_followers', 'До', 0, 1_000_000_000, {
              disabled: !settings.scout_use_followers_range,
            })}
          </div>
        </Block>

        <Block title="Сколько проверять">
          {numberField('scout_max_posts_per_source', 'Публикаций на источник', 1, 200, {
            hint: 'Например: 6 значит до 6 постов или отметок с каждого источника. Максимум 200.',
          })}
          {numberField('scout_followers_max', 'Подписчиков на источник', 1, 1000, {
            hint: 'Экспериментально. Безопасный старт: 100 на источник; повышайте постепенно. Максимум 1000.',
            disabled: !has('followers'),
          })}
          {numberField('scout_following_max', 'Подписок на источник', 1, 1000, {
            hint: 'Экспериментально. Безопасный старт: 100 на источник; повышайте постепенно. Максимум 1000.',
            disabled: !has('following'),
          })}
        </Block>
      </div>

      <Block title="Память" wide>
        <div className="scout-options">
          <Option
            checked={settings.scout_skip_recent_sources}
            disabled={busy}
            onChange={checked => update('scout_skip_recent_sources', checked)}
            title={`Не трогать SMM ${hours(settings.scout_source_cooldown_hours)}`}
            text={`Когда включено: если SMM-источник уже проверяли за последние ${hours(
              settings.scout_source_cooldown_hours,
            )}, парсер не откроет его повторно.`}
          />
          <Option
            checked={settings.scout_skip_processed}
            disabled={busy}
            onChange={checked => update('scout_skip_processed', checked)}
            title="Искать новые дальше по списку"
            text="Когда включено: если верхние публикации или профили уже обработаны, парсер продолжает дальше по списку, пока не найдёт новые."
          />
          <Option
            checked={settings.scout_rotate_sources}
            disabled={busy}
            onChange={checked => update('scout_rotate_sources', checked)}
            title="Идти по новым SMM-источникам"
            text="Когда включено: парсер берёт источники, которые ещё не проходил в этом круге, и возвращается к началу только после всего списка."
          />
        </div>
      </Block>

      <Block title="AI-классификация" wide>
        <div className="scout-ai">
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
            <span>
              Ключ OpenRouter {ai.data?.configured ? <small>· сохранён</small> : <small>· не задан</small>}
            </span>
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
                  setMessage('Ключ сохранён в защищённом хранилище системы.');
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
        </div>
        <p className="helper">
          AI только уточняет категорию; решение о лиде принимают фильтры. Без ключа используется локальная
          классификация. Профиль отправляется в OpenRouter только при включённом AI.
        </p>
      </Block>

      <details className="scout-advanced">
        <summary>Дополнительно: источники, профили, прокрутка, повторы, отладка</summary>
        <div className="scout-settings-grid">
          <section>
            <h3>Источники и лиды</h3>
            <div className="check-list">
              <label>
                <input
                  type="checkbox"
                  checked={settings.scout_allow_unknown_followers}
                  disabled={busy}
                  onChange={event => update('scout_allow_unknown_followers', event.target.checked)}
                />
                Принимать профили, у которых не прочитаны подписчики
              </label>
            </div>
            <div className="field-row">
              {numberField('scout_sources_per_run', 'Источников за запуск', 1, 500)}
              {numberField('scout_source_cooldown_hours', 'Не трогать источник, ч', 0, 2160)}
            </div>
            <div className="field-row">
              {numberField('scout_min_confidence', 'Мин. уверенность типа, % (0 — выкл.)', 0, 100)}
              <label>
                Статус нового лида
                <select
                  value={settings.scout_lead_status}
                  disabled={busy}
                  onChange={event =>
                    update('scout_lead_status', event.target.value as SettingsData['scout_lead_status'])
                  }
                >
                  <option value="new">Новый</option>
                  <option value="reviewed">Просмотрен</option>
                  <option value="qualified">Подходит</option>
                </select>
              </label>
            </div>
          </section>
          <section>
            <h3>Профили</h3>
            <div className="check-list">
              <label>
                <input
                  type="checkbox"
                  checked={settings.scout_profile_api}
                  disabled={busy}
                  onChange={event => update('scout_profile_api', event.target.checked)}
                />
                Сначала Instagram web API
                <small>
                  Один запрос из открытой вкладки; страница профиля открывается, только если данных мало.
                </small>
              </label>
            </div>
            <div className="field-row">
              {numberField('scout_profile_cache_hours', 'Кэш профиля, ч (0 — выкл.)', 0, 168)}
              {numberField('scout_recent_captions', 'Подписей к постам', 0, 12)}
            </div>
          </section>
          <section>
            <h3>AI-классификация</h3>
            <div className="field-row">
              {numberField('scout_ai_min_confidence', 'AI, если уверенность ниже, %', 0, 100)}
              {numberField('scout_ai_timeout_seconds', 'Таймаут AI, с', 5, 60)}
              {numberField('scout_ai_concurrency', 'Параллельных запросов AI', 1, 5)}
            </div>
          </section>
          <section>
            <h3>Посты и отметки</h3>
            <div className="field-row">
              {numberField('scout_max_scroll_rounds', 'Раундов прокрутки', 0, 40)}
              {numberField('scout_scroll_delay_ms', 'Пауза прокрутки, мс', 300, 5000)}
              {numberField('scout_max_no_progress_rounds', 'Раундов без новых', 1, 10)}
            </div>
          </section>
          <section>
            <h3>Подписчики и подписки</h3>
            <div className="field-row">
              {numberField('scout_follow_page_size', 'Строк на страницу', 5, 50)}
              {numberField('scout_follow_delay_seconds', 'Пауза между страницами, с', 1, 10)}
            </div>
          </section>
          <section>
            <h3>Ошибки и отладка</h3>
            <div className="field-row">
              {numberField('scout_max_retries', 'Повторов при сбое загрузки', 0, 5)}
              {numberField('scout_max_item_failures', 'Попыток на публикацию', 1, 10)}
            </div>
            <div className="check-list">
              <label>
                <input
                  type="checkbox"
                  checked={settings.scout_debug}
                  disabled={busy}
                  onChange={event => update('scout_debug', event.target.checked)}
                />
                Режим отладки Scout
                <small>
                  При сбое разбора сохраняет URL, причину, очищенный HTML и скриншот в папку данных.
                </small>
              </label>
            </div>
            <label>
              Не считать кандидатами
              <textarea
                rows={3}
                value={settings.scout_ignore_usernames.join('\n')}
                disabled={busy}
                placeholder={'@label_account\n@own_brand'}
                onChange={event =>
                  update(
                    'scout_ignore_usernames',
                    event.target.value
                      .split(/[\s,]+/)
                      .map(value => value.trim().replace(/^@/, ''))
                      .filter(Boolean),
                  )
                }
              />
            </label>
          </section>
        </div>
      </details>
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
        <ErrorToast message={error} />
      </div>
    </div>
  );
}
