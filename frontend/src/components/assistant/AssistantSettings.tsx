import { useState } from 'react';
import { Cpu, KeyRound } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { AssistantProvider, AssistantState } from '../../services/types';
import { Button } from '../ui/button';
import { Dropdown } from '../ui/desktop';
import { ErrorToast } from '../Toaster';
import { errorText } from '../../lib/errors';
import { TelegramPanel } from './TelegramPanel';

/** OpenAI-compatible endpoints people use most; «Свой адрес» takes any other. */
const PRESETS = [
  { label: 'OpenRouter — любые модели', url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-5-mini' },
  { label: 'OpenAI', url: 'https://api.openai.com/v1', model: 'gpt-5-mini' },
  { label: 'DeepSeek', url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  {
    label: 'Google Gemini',
    url: 'https://generativelanguage.googleapis.com/v1beta/openai',
    model: 'gemini-2.5-flash',
  },
  { label: 'Groq', url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' },
  { label: 'Ollama (на этом компьютере)', url: 'http://localhost:11434/v1', model: 'qwen3' },
  { label: 'LM Studio (на этом компьютере)', url: 'http://localhost:1234/v1', model: '' },
];
const CUSTOM = 'custom';
const ANTHROPIC_MAIN = 'claude-haiku-5-5';
const ANTHROPIC_STRONG = 'claude-sonnet-5-5';

/** Provider, address, key and the two models. A saved key is never shown back; an empty
 * key field keeps it. */
export function ModelForm({ provider, onSaved }: { provider?: AssistantProvider; onSaved: () => void }) {
  const [kind, setKind] = useState<AssistantProvider['provider']>(provider?.provider || 'anthropic');
  const [baseUrl, setBaseUrl] = useState(provider?.base_url || PRESETS[0].url);
  const [main, setMain] = useState(provider?.main || ANTHROPIC_MAIN);
  const [strong, setStrong] = useState(provider?.strong ?? ANTHROPIC_STRONG);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const preset = PRESETS.find(item => item.url === baseUrl);
  const hasKey = kind === 'anthropic' ? provider?.anthropic_key : provider?.compatible_key;
  const switchKind = (next: AssistantProvider['provider']) => {
    setKind(next);
    if (next === 'anthropic') {
      setMain(ANTHROPIC_MAIN);
      setStrong(ANTHROPIC_STRONG);
    } else {
      const start = PRESETS.find(item => item.url === provider?.base_url) || PRESETS[0];
      setBaseUrl(provider?.provider === 'compatible' ? provider.base_url : start.url);
      setMain(provider?.provider === 'compatible' ? provider.main : start.model);
      setStrong(provider?.provider === 'compatible' ? provider.strong : '');
    }
  };
  const save = async () => {
    setError('');
    setBusy(true);
    try {
      await api.request('assistant.model', {
        provider: kind,
        base_url: kind === 'compatible' ? baseUrl : '',
        main,
        strong,
        key,
      });
      setKey('');
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="assistant-model">
      <div className="segmented" role="radiogroup" aria-label="Провайдер модели">
        {(
          [
            ['anthropic', 'Anthropic (Claude)'],
            ['compatible', 'Любая модель — OpenAI-совместимый API'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={kind === value}
            className={kind === value ? 'active' : ''}
            onClick={() => switchKind(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="helper">
        {kind === 'anthropic'
          ? 'Ключ с console.anthropic.com (раздел API Keys). Модель можно вписать любую из Claude.'
          : 'OpenRouter даёт доступ почти ко всем моделям по одному ключу. Подойдёт любой сервис с API в формате OpenAI, в том числе локальная модель — ей ключ не нужен. Модель должна уметь вызывать инструменты (tool calling).'}
      </p>
      <div className="assistant-model-grid">
        {kind === 'compatible' && (
          <>
            <label>
              Сервис
              <Dropdown
                label="Сервис"
                value={preset ? preset.url : CUSTOM}
                options={[
                  ...PRESETS.map(item => ({ value: item.url, label: item.label })),
                  { value: CUSTOM, label: 'Свой адрес' },
                ]}
                onChange={value => {
                  const next = PRESETS.find(item => item.url === value);
                  if (!next) {
                    setBaseUrl('https://');
                    return;
                  }
                  setBaseUrl(next.url);
                  if (next.model) setMain(next.model);
                }}
              />
            </label>
            <label>
              Адрес API
              <input value={baseUrl} spellCheck={false} onChange={event => setBaseUrl(event.target.value)} />
            </label>
          </>
        )}
        <label>
          Основная модель
          <input
            value={main}
            spellCheck={false}
            placeholder={kind === 'anthropic' ? ANTHROPIC_MAIN : 'например, openai/gpt-5-mini'}
            onChange={event => setMain(event.target.value)}
          />
        </label>
        <label>
          Сильная модель <small>необязательно — для сложных ответов</small>
          <input
            value={strong}
            spellCheck={false}
            placeholder={kind === 'anthropic' ? ANTHROPIC_STRONG : 'например, openai/gpt-5'}
            onChange={event => setStrong(event.target.value)}
          />
        </label>
        <label className="assistant-model-key">
          Ключ API
          <input
            type="password"
            value={key}
            autoComplete="off"
            placeholder={
              hasKey
                ? 'Сохранён — оставьте пустым, чтобы не менять'
                : kind === 'anthropic'
                  ? 'sk-ant-…'
                  : 'Ключ сервиса (локальной модели не нужен)'
            }
            onChange={event => setKey(event.target.value)}
          />
        </label>
      </div>
      <div className="actions">
        <Button disabled={busy || !main.trim()} onClick={() => void save()}>
          <KeyRound size={14} /> Сохранить подключение
        </Button>
      </div>
      <ErrorToast message={error} />
    </div>
  );
}

/** «Настройки» → «Ассистент»: which model answers, the hand-over to the stronger one and
 * access to Messages. */
export function AssistantSettings() {
  const state = useResource<AssistantState>('assistant.state');
  const data = state.data;
  const [editing, setEditing] = useState(false);
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
  const provider = data.provider;
  return (
    <section className="panel assistant-settings">
      <h2>Ассистент</h2>
      {!data.configured || editing ? (
        <ModelForm
          provider={provider}
          onSaved={() => {
            setEditing(false);
            state.refresh();
          }}
        />
      ) : (
        <>
          <dl className="assistant-model-summary">
            <div>
              <dt>Подключение</dt>
              <dd>{provider.provider === 'anthropic' ? 'Anthropic' : provider.base_url}</dd>
            </div>
            <div>
              <dt>Основная модель</dt>
              <dd>{data.models.main}</dd>
            </div>
            <div>
              <dt>Сильная модель</dt>
              <dd>{data.models.strong || 'не подключена'}</dd>
            </div>
          </dl>
          {data.models.strong && (
            <label className="check-row">
              <input
                type="checkbox"
                checked={data.escalation}
                onChange={event => void save({ escalation: event.target.checked })}
              />
              {data.models.main} сам передаёт сложные ответы {data.models.strong}
            </label>
          )}
          <Button variant="outline" onClick={() => setEditing(true)}>
            <Cpu size={14} /> Сменить модель или ключ
          </Button>
        </>
      )}
      <p className={data.messages.available ? 'helper' : 'board-warning'}>
        {data.messages.available ? 'Переписка iMessage на этом Mac доступна.' : data.messages.reason}
      </p>
      <p className="helper">{data.mac_send.description}</p>
      <TelegramPanel />
      <ErrorToast message={error || state.error} />
    </section>
  );
}
