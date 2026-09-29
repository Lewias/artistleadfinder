import { useEffect, useRef, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { FollowUpSequence, OutreachTemplate, RenderedTemplate } from '../../services/types';
import { Button } from '../ui/button';
import { templateVariables } from './outreachText';

const EXAMPLE = 'Hey {{firstName}}, checked your music out — wanted to reach out real quick.';

export function TemplatesPanel() {
  const templates = useResource<OutreachTemplate[]>('outreach.templates');
  const [editing, setEditing] = useState<Partial<OutreachTemplate> | null>(null);
  return (
    <div className="outreach-columns">
      <section className="panel">
        <div className="section-heading">
          <h2>Шаблоны сообщений</h2>
          <Button onClick={() => setEditing({ name: '', body: EXAMPLE, enabled: true })}>
            <Plus size={15} /> Новый шаблон
          </Button>
        </div>
        {!templates.data?.length && <p className="empty-copy">Шаблонов пока нет.</p>}
        <ul className="template-list">
          {(templates.data || []).map(item => (
            <li key={item.id}>
              <button
                className={`template-item${editing?.id === item.id ? ' active' : ''}`}
                onClick={() => setEditing(item)}
              >
                <strong>{item.name}</strong>
                {!item.enabled && <small> · выключен</small>}
                <span>{item.body}</span>
              </button>
            </li>
          ))}
        </ul>
        {editing && (
          <TemplateEditor
            key={editing.id || 'new'}
            initial={editing}
            saved={() => {
              templates.refresh();
              setEditing(null);
            }}
          />
        )}
      </section>
      <SequencesPanel templates={(templates.data || []).filter(item => item.enabled)} />
    </div>
  );
}

function TemplateEditor({ initial, saved }: { initial: Partial<OutreachTemplate>; saved: () => void }) {
  const [name, setName] = useState(initial.name || '');
  const [body, setBody] = useState(initial.body || '');
  const [enabled, setEnabled] = useState(initial.enabled ?? true);
  const [preview, setPreview] = useState<RenderedTemplate>();
  const [error, setError] = useState('');
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .request<RenderedTemplate>('outreach.template_render', { body })
        .then(setPreview)
        .catch(err => setError(String(err)));
    }, 300);
    return () => clearTimeout(timer);
  }, [body]);
  const insert = (variable: string) => {
    const element = area.current;
    const token = `{{${variable}}}`;
    if (!element) return setBody(value => value + token);
    const start = element.selectionStart;
    const end = element.selectionEnd;
    setBody(value => value.slice(0, start) + token + value.slice(end));
  };
  const save = async () => {
    setError('');
    try {
      await api.request('outreach.template_save', { id: initial.id, name: name.trim(), body, enabled });
      saved();
    } catch (err) {
      setError(String(err));
    }
  };
  return (
    <div className="template-editor">
      <label>
        Название
        <input maxLength={120} value={name} onChange={event => setName(event.target.value)} />
      </label>
      <label>
        Текст
        <textarea
          ref={area}
          rows={5}
          maxLength={4000}
          value={body}
          onChange={event => setBody(event.target.value)}
        />
      </label>
      <div className="variable-chips">
        {templateVariables.map(variable => (
          <button type="button" key={variable} className="chip" onClick={() => insert(variable)}>
            {`{{${variable}}}`}
          </button>
        ))}
      </div>
      <p className="helper">
        Своё значение по умолчанию: <code>{'{{firstName|friend}}'}</code>. Без него пустое имя заменяется на
        «there», пустой artistName — на username.
      </p>
      {preview && (
        <div className="preview-card">
          <header>
            <span>Пример для Jay Carter (@jaycarter)</span>
            <span>{preview.length} / 1000</span>
          </header>
          <p>{preview.text || '—'}</p>
          {preview.errors.map(item => (
            <small key={item} className="error-text">
              {item}
            </small>
          ))}
        </div>
      )}
      <label className="inline-check">
        <input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} />
        Шаблон включён
      </label>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <div className="actions">
        <Button disabled={!name.trim() || !preview?.valid} onClick={() => void save()}>
          Сохранить шаблон
        </Button>
      </div>
    </div>
  );
}

function SequencesPanel({ templates }: { templates: OutreachTemplate[] }) {
  const sequences = useResource<FollowUpSequence[]>('outreach.sequences');
  const [name, setName] = useState('');
  const [steps, setSteps] = useState<{ delay_days: number; template_id: number }[]>([]);
  const [error, setError] = useState('');
  const save = async () => {
    setError('');
    try {
      await api.request('outreach.sequence_save', { name: name.trim(), steps });
      setName('');
      setSteps([]);
      sequences.refresh();
    } catch (err) {
      setError(String(err));
    }
  };
  return (
    <section className="panel">
      <h2>Follow-up цепочки</h2>
      <p className="helper">
        Цепочка планируется после успешного первого сообщения и отменяется, когда лид отвечает. Отправку
        follow-up выполнит отдельный модуль; здесь они только планируются.
      </p>
      <ul className="template-list">
        {(sequences.data || []).map(item => (
          <li key={item.id} className="template-item static">
            <strong>{item.name}</strong>
            <span>
              {item.steps
                .map(
                  step =>
                    `через ${step.delay_days} дн. — ${templates.find(t => t.id === step.template_id)?.name || 'шаблон'}`,
                )
                .join(' → ')}
            </span>
          </li>
        ))}
      </ul>
      <div className="template-editor">
        <label>
          Название цепочки
          <input maxLength={120} value={name} onChange={event => setName(event.target.value)} />
        </label>
        {steps.map((step, index) => (
          <div className="field-row sequence-step" key={index}>
            <label>
              Через, дней
              <input
                type="number"
                min={1}
                max={60}
                value={step.delay_days}
                onChange={event =>
                  setSteps(current =>
                    current.map((item, i) =>
                      i === index ? { ...item, delay_days: Number(event.target.value) } : item,
                    ),
                  )
                }
              />
            </label>
            <label>
              Шаблон
              <select
                value={step.template_id}
                onChange={event =>
                  setSteps(current =>
                    current.map((item, i) =>
                      i === index ? { ...item, template_id: Number(event.target.value) } : item,
                    ),
                  )
                }
              >
                {templates.map(item => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <Button
              icon
              variant="outline"
              aria-label="Удалить шаг"
              onClick={() => setSteps(current => current.filter((_, i) => i !== index))}
            >
              <Trash2 size={14} />
            </Button>
          </div>
        ))}
        <div className="actions">
          <Button
            variant="outline"
            disabled={!templates.length || steps.length >= 5}
            onClick={() => setSteps(current => [...current, { delay_days: 3, template_id: templates[0].id }])}
          >
            <Plus size={14} /> Шаг
          </Button>
          <Button disabled={!name.trim() || !steps.length} onClick={() => void save()}>
            Сохранить цепочку
          </Button>
        </div>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
      </div>
    </section>
  );
}
