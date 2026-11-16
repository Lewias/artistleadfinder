import { useEffect, useState } from 'react';
import { Eye, Pause, Play, Plus, Square, SquarePen, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type {
  FollowUpRun,
  FollowUpRuns,
  FollowUpSequence,
  FollowUpStep,
  OutreachWorkspace,
  SequencePreview,
} from '../../services/types';
import { number, plural } from '../../lib/format';
import { Button } from '../ui/button';
import { Modal } from '../Modal';
import { StatusBadge } from '../DataState';
import { ErrorToast } from '../Toaster';
import { errorText } from '../accountRuns';
import { dateTime, runLine, runStatusLabels, runStatusTone } from './outreachText';

const MAX_STEPS = 5;
const blankStep = (): FollowUpStep => ({ delay_days: 3, text: '' });

/** The buttons of one chain: pause, resume, stop, and the answer to an unconfirmed step. */
export function RunActions({ run, onChanged }: { run: FollowUpRun; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const act = async (method: string, params: object) => {
    setBusy(true);
    setError('');
    try {
      await api.request(method, params);
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const control = (action: string) => act('outreach.followup_control', { run_id: run.id, action });
  const resolve = (sent: boolean, retry = false) =>
    act('outreach.followup_resolve', { job_id: run.review_job_id, sent, retry });
  return (
    <>
      <span className="actions followup-actions">
        {run.status === 'review' && run.review_job_id && (
          <>
            <Button variant="outline" disabled={busy} onClick={() => void resolve(true)}>
              Сообщение ушло
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => void resolve(false, true)}>
              Не ушло — повторить
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => void resolve(false)}>
              Не ушло — остановить
            </Button>
          </>
        )}
        {run.status === 'active' && (
          <Button variant="outline" disabled={busy} onClick={() => void control('pause')}>
            <Pause size={14} /> Пауза
          </Button>
        )}
        {run.status === 'paused' && (
          <Button variant="outline" disabled={busy} onClick={() => void control('resume')}>
            <Play size={14} /> {run.reason === 'LEGACY' ? 'Запустить' : 'Продолжить'}
          </Button>
        )}
        {(run.status === 'active' || run.status === 'paused') && (
          <Button variant="outline" disabled={busy} onClick={() => void control('stop')}>
            <Square size={13} /> Остановить
          </Button>
        )}
      </span>
      <ErrorToast message={error} />
    </>
  );
}

/** Creating or editing a chain, with each step's text for a sample lead. */
export function SequenceEditor({
  sequence,
  onClose,
  onSaved,
}: {
  sequence?: FollowUpSequence;
  onClose: () => void;
  onSaved: (saved: FollowUpSequence) => void;
}) {
  const [name, setName] = useState(sequence?.name ?? '');
  const [steps, setSteps] = useState<FollowUpStep[]>(sequence?.steps.length ? sequence.steps : [blankStep()]);
  const [preview, setPreview] = useState<SequencePreview>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const change = (index: number, patch: Partial<FollowUpStep>) => {
    setPreview(undefined);
    setSteps(steps.map((step, i) => (i === index ? { ...step, ...patch } : step)));
  };
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const showPreview = () =>
    run(async () => setPreview(await api.request<SequencePreview>('outreach.sequence_preview', { steps })));
  const save = () =>
    run(async () => {
      const saved = await api.request<FollowUpSequence>('outreach.sequence_save', {
        id: sequence?.id,
        name: name.trim(),
        steps: steps.map(step => ({ delay_days: Number(step.delay_days), text: step.text })),
      });
      onSaved(saved);
    });
  return (
    <Modal title={sequence ? 'Цепочка follow-up' : 'Новая цепочка follow-up'} onClose={onClose} wide>
      <p className="helper">
        Шаги уходят в тот же диалог после подтверждённого первого сообщения. Ответ собеседника останавливает
        цепочку.
        {sequence?.open_runs
          ? ` Уже идущие цепочки (${number(sequence.open_runs)}) сохранят свои тексты.`
          : ''}
      </p>
      <div className="modal-fields">
        <label>
          <span>Название</span>
          <input value={name} maxLength={120} onChange={event => setName(event.target.value)} />
        </label>
      </div>
      <ol className="followup-steps">
        {steps.map((step, index) => (
          <li key={index} className="followup-step">
            <div className="followup-step-head">
              <strong>Шаг {index + 1}</strong>
              <label>
                через
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={step.delay_days}
                  aria-label={`Дней до шага ${index + 1}`}
                  onChange={event => change(index, { delay_days: Number(event.target.value) })}
                />
                дн. {index ? `после шага ${index}` : 'после первого сообщения'}
              </label>
              {steps.length > 1 && (
                <Button
                  variant="danger"
                  icon
                  aria-label={`Удалить шаг ${index + 1}`}
                  title="Удалить шаг"
                  onClick={() => {
                    setPreview(undefined);
                    setSteps(steps.filter((_, i) => i !== index));
                  }}
                >
                  <Trash2 size={14} />
                </Button>
              )}
            </div>
            <textarea
              rows={3}
              value={step.text}
              aria-label={`Текст шага ${index + 1}`}
              placeholder="Hey {{firstName}}, just bumping this up"
              onChange={event => change(index, { text: event.target.value })}
            />
            {preview?.steps[index] && (
              <div className={`followup-preview ${preview.steps[index].valid ? '' : 'error-text'}`}>
                <small>День {preview.steps[index].day} · для @jaycarter</small>
                <p>
                  {preview.steps[index].valid
                    ? preview.steps[index].text
                    : preview.steps[index].errors.join(' ')}
                </p>
              </div>
            )}
          </li>
        ))}
      </ol>
      <p className="helper">Переменные: {'{{firstName}}, {{username}}, {{artistName}}, {{followers}}'}.</p>
      <ErrorToast message={error} />
      <div className="actions">
        <Button
          disabled={busy || !name.trim() || steps.some(step => !step.text.trim())}
          onClick={() => void save()}
        >
          Сохранить
        </Button>
        <Button
          variant="outline"
          disabled={busy || steps.some(step => !step.text.trim())}
          onClick={() => void showPreview()}
        >
          <Eye size={15} /> Предпросмотр
        </Button>
        <Button
          variant="outline"
          disabled={steps.length >= MAX_STEPS}
          onClick={() => {
            setPreview(undefined);
            setSteps([...steps, blankStep()]);
          }}
        >
          <Plus size={15} /> Шаг
        </Button>
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
      </div>
    </Modal>
  );
}

/** Saved chains: create, edit, remove. */
export function SequencesModal({ onClose, onChanged }: { onClose: () => void; onChanged?: () => void }) {
  const sequences = useResource<FollowUpSequence[]>('outreach.sequences');
  const [editing, setEditing] = useState<FollowUpSequence | 'new' | null>(null);
  const [error, setError] = useState('');
  const remove = async (item: FollowUpSequence) => {
    setError('');
    try {
      await api.request('outreach.sequence_save', { id: item.id, name: item.name, enabled: false });
      sequences.refresh();
      onChanged?.();
    } catch (err) {
      setError(errorText(err));
    }
  };
  if (editing)
    return (
      <SequenceEditor
        sequence={editing === 'new' ? undefined : editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          sequences.refresh();
          onChanged?.();
        }}
      />
    );
  return (
    <Modal title="Цепочки follow-up" onClose={onClose}>
      <div className="actions">
        <Button onClick={() => setEditing('new')}>
          <Plus size={15} /> Новая цепочка
        </Button>
      </div>
      <ErrorToast message={error || sequences.error} />
      <div className="template-pick">
        {(sequences.data ?? []).map(item => (
          <div className="message-card followup-card" key={item.id}>
            <div>
              <strong>{item.name}</strong>
              <p className="helper">
                {item.steps.map(step => `+${step.delay_days} дн.`).join(' → ')}
                {item.open_runs
                  ? ` · идёт в ${plural(item.open_runs, ['диалоге', 'диалогах', 'диалогах'])}`
                  : ''}
              </p>
            </div>
            <div className="actions">
              <Button variant="outline" onClick={() => setEditing(item)}>
                <SquarePen size={14} /> Изменить
              </Button>
              <Button variant="outline" onClick={() => void remove(item)}>
                Удалить
              </Button>
            </div>
          </div>
        ))}
        {sequences.data && !sequences.data.length && (
          <p className="empty-copy">
            Цепочек нет. Создайте первую — она начнёт работать только в рассылке, где её выбрали.
          </p>
        )}
      </div>
    </Modal>
  );
}

/** The chain the next outreach starts with (the list and «Найти и написать» share it). */
export function FollowUpPick({ disabled = false }: { disabled?: boolean }) {
  const workspace = useResource<OutreachWorkspace>('outreach.workspace', {}, 5000);
  const sequences = useResource<FollowUpSequence[]>('outreach.sequences', {}, 15000);
  const [value, setValue] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (workspace.data) setValue(workspace.data.followup_sequence_id);
  }, [workspace.data]);
  const running = !!workspace.data?.running;
  const choose = async (id: number | null) => {
    setError('');
    try {
      const next = await api.request<OutreachWorkspace>('outreach.workspace_update', {
        followup_sequence_id: id,
      });
      setValue(next.followup_sequence_id);
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <>
      <label className="followup-pick">
        <span>Follow-up</span>
        <select
          value={value ?? ''}
          disabled={disabled || running}
          title={running ? 'Цепочка выбирается до запуска рассылки' : undefined}
          onChange={event => {
            if (event.target.value === 'new') return setEditing(true);
            void choose(event.target.value ? Number(event.target.value) : null);
          }}
        >
          <option value="">Без follow-up</option>
          {(sequences.data ?? []).map(item => (
            <option key={item.id} value={item.id}>
              {item.name} · {plural(item.steps.length, ['шаг', 'шага', 'шагов'])}
            </option>
          ))}
          <option value="new">Новая цепочка…</option>
        </select>
      </label>
      {editing && (
        <SequenceEditor
          onClose={() => setEditing(false)}
          onSaved={saved => {
            setEditing(false);
            sequences.refresh();
            void choose(saved.id);
          }}
        />
      )}
      <ErrorToast message={error} />
    </>
  );
}

const groups = [
  ['open', 'Идут'],
  ['finished', 'Завершённые'],
  ['all', 'Все'],
] as const;

/** «Follow-up»: the started chains, what each one does next, and their controls. */
export function FollowUpPanel() {
  const [group, setGroup] = useState<(typeof groups)[number][0]>('open');
  const runs = useResource<FollowUpRuns>('outreach.followups', { group }, 5000);
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [state, setState] = useState<FollowUpRuns>();
  useEffect(() => {
    if (runs.data) setState(runs.data);
  }, [runs.data]);
  const counts = state?.counts ?? {};
  const bulk = async (action: string) => {
    setBusy(true);
    setError('');
    try {
      await api.request('outreach.followup_control', { all: true, action });
      runs.refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const items = state?.items ?? [];
  const summary = [
    counts.active && `ждут шага: ${number(counts.active)}`,
    counts.review && `проверить отправку: ${number(counts.review)}`,
    counts.paused && `на паузе: ${number(counts.paused)}`,
  ].filter(Boolean);
  return (
    <div className="inbox-panel">
      <section className="panel inbox-controls">
        <div className="inbox-run">
          <label>
            <span>Показать</span>
            <select value={group} onChange={event => setGroup(event.target.value as typeof group)}>
              {groups.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <Button variant="outline" onClick={() => setDialog(true)}>
            <SquarePen size={15} /> Цепочки
          </Button>
          <span className="inbox-actions-buttons">
            <Button variant="outline" disabled={busy || !counts.active} onClick={() => void bulk('pause')}>
              <Pause size={14} /> Пауза всех
            </Button>
            <Button variant="outline" disabled={busy || !counts.paused} onClick={() => void bulk('resume')}>
              <Play size={14} /> Продолжить все
            </Button>
            <Button
              variant="outline"
              disabled={busy || !(counts.active || counts.paused || counts.review)}
              onClick={() => void bulk('stop')}
            >
              <Square size={13} /> Остановить все
            </Button>
          </span>
        </div>
        <p className="inbox-status">
          {!summary.length
            ? 'Автоматических follow-up сейчас нет'
            : `${counts.active ? 'Автоматические follow-up идут' : 'Автоматические follow-up стоят'} · ${summary.join(' · ')}`}
        </p>
        <p className="helper">
          Шаги уходят с того же аккаунта в тот же диалог. Ответ собеседника останавливает цепочку.
        </p>
      </section>
      <ErrorToast message={error || runs.error} />
      {items.length > 0 ? (
        <div className="crm-table inbox-table">
          <table>
            <thead>
              <tr>
                <th>Артист</th>
                <th>Аккаунт</th>
                <th>Цепочка</th>
                <th>Состояние</th>
                <th>Что дальше</th>
              </tr>
            </thead>
            <tbody>
              {items.map(run => (
                <tr key={run.id}>
                  <td>
                    <strong>@{run.username}</strong>
                  </td>
                  <td className="helper">{run.sender_name}</td>
                  <td>
                    <details>
                      <summary>{run.sequence_name || 'Цепочка'}</summary>
                      <ol className="followup-history">
                        {run.steps.map((step, index) => (
                          <li key={index}>
                            <small>
                              +{step.delay_days} дн. ·{' '}
                              {step.status === 'sent'
                                ? `отправлен ${dateTime(step.sent_at)}`
                                : step.status === 'unconfirmed'
                                  ? 'не подтверждён'
                                  : step.status === 'cancelled'
                                    ? 'отменён'
                                    : step.status === 'failed'
                                      ? 'ошибка'
                                      : 'ещё не отправлен'}
                            </small>
                            <p>{step.text}</p>
                          </li>
                        ))}
                      </ol>
                    </details>
                  </td>
                  <td>
                    <StatusBadge value={runStatusTone[run.status]} label={runStatusLabels[run.status]} />
                  </td>
                  <td className="followup-next">
                    <span>{runLine(run)}</span>
                    {run.details && (run.status === 'failed' || run.reason === 'THREAD_MISMATCH') && (
                      <small className="helper">{run.details}</small>
                    )}
                    <RunActions run={run} onChanged={runs.refresh} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        state && (
          <p className="empty-copy">
            {group === 'open'
              ? 'Идущих цепочек нет. Выберите цепочку в «Рассылке по списку»: она начнётся после каждого подтверждённого первого сообщения.'
              : 'Здесь пока пусто.'}
          </p>
        )
      )}
      {dialog && <SequencesModal onClose={() => setDialog(false)} />}
    </div>
  );
}
