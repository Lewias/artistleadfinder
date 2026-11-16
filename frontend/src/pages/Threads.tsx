import { useEffect, useState } from 'react';
import { AtSign, Clock3, ExternalLink, Pause, Play, Plus, Send } from 'lucide-react';
import { api } from '../services/api';
import type { ThreadsAccount, ThreadsPost, ThreadsPostStatus } from '../services/types';
import { useThreads, type ThreadsResource } from '../hooks/useThreads';
import { errorText } from '../lib/errors';
import { PageHeader } from '../components/PageHeader';
import { DataState, StatusBadge } from '../components/DataState';
import { ErrorToast } from '../components/Toaster';
import { Modal } from '../components/Modal';
import { Button } from '../components/ui/button';
import './Threads.css';

const badge: Record<ThreadsPostStatus, string> = {
  draft: 'pending',
  scheduled: 'queued',
  publishing: 'sending',
  published: 'sent',
  failed: 'failed',
  review: 'paused',
  cancelled: 'cancelled',
  missed: 'paused',
};
const editable: ThreadsPostStatus[] = ['draft', 'scheduled', 'missed', 'failed', 'cancelled'];
const dateTime = (value: string | null) =>
  value
    ? new Date(value).toLocaleString('ru-RU', {
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';
const localInput = (value: string | null) => {
  const date = value ? new Date(value) : new Date(Date.now() + 60 * 60 * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

function PostEditor({
  post,
  accounts,
  zone,
  busy,
  call,
  onClose,
}: {
  post: ThreadsPost | null;
  accounts: ThreadsAccount[];
  zone: string;
  busy: boolean;
  call: ThreadsResource['call'];
  onClose: () => void;
}) {
  const [text, setText] = useState(post?.text ?? '');
  const [accountId, setAccountId] = useState(
    String(post?.account_id ?? accounts.find(a => a.status === 'active')?.id ?? ''),
  );
  const [mode, setMode] = useState<'slot' | 'at'>(post?.slot ? 'slot' : 'at');
  const [at, setAt] = useState(localInput(post?.scheduled_at ?? null));
  const [validation, setValidation] = useState<{ text: string; length: number; errors: string[] }>();
  const [validationError, setValidationError] = useState('');
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      void api
        .request<{ length: number; errors: string[] }>('threads.validate', { text })
        .then(value => {
          if (!cancelled) {
            setValidation({ ...value, text });
            setValidationError('');
          }
        })
        .catch(err => {
          if (!cancelled) setValidationError(errorText(err));
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [text]);
  const ready = validation?.text === text && !validation.errors.length && !!accountId && !validationError;
  const act = async (action: 'draft' | 'schedule' | 'publish') => {
    const params = {
      ...(post ? { id: post.id } : {}),
      text,
      account_id: accountId ? Number(accountId) : null,
    };
    let result: unknown;
    if (action === 'draft') result = await call('threads.post_save', params);
    else if (action === 'publish') result = await call('threads.post_publish_now', params);
    else if (mode === 'slot') result = await call('threads.post_schedule', { ...params, slot: true });
    else {
      const moment = new Date(at);
      if (!Number.isFinite(moment.getTime())) return;
      result = await call('threads.post_schedule', { ...params, at: moment.toISOString() });
    }
    if (result !== undefined) onClose();
  };
  const selected = accounts.find(a => a.id === Number(accountId));
  return (
    <Modal
      title={post ? 'Редактировать публикацию' : 'Новая публикация'}
      onClose={() => !busy && onClose()}
      wide
    >
      <div className="threads-composer">
        <fieldset disabled={busy}>
          <label>
            Аккаунт
            <select aria-label="Аккаунт" value={accountId} onChange={e => setAccountId(e.target.value)}>
              <option value="">Выберите аккаунт</option>
              {accounts.map(a => (
                <option key={a.id} value={a.id} disabled={a.status === 'disconnected'}>
                  @{a.username}
                  {a.status === 'reauth' ? ' · переподключите' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            Текст публикации
            <textarea
              aria-label="Текст публикации"
              autoComplete="off"
              rows={9}
              maxLength={4000}
              value={text}
              onChange={e => setText(e.target.value)}
              placeholder="Что хотите рассказать?"
            />
          </label>
          <div className="threads-validation" role="status">
            <span>{validation?.text === text ? `${validation.length} / 500` : 'Проверка текста…'}</span>
            {validation?.text === text &&
              validation.errors.map(item => (
                <span className="error-text" key={item}>
                  {item}
                </span>
              ))}
            {validationError && <span className="error-text">{validationError}</span>}
          </div>
          <div className="threads-schedule">
            <label>
              Когда опубликовать
              <select
                aria-label="Когда опубликовать"
                value={mode}
                onChange={e => setMode(e.target.value as 'slot' | 'at')}
              >
                <option value="at">Дата и время</option>
                <option value="slot">Следующее время по расписанию</option>
              </select>
            </label>
            {mode === 'at' ? (
              <label>
                Время · {zone}
                <input type="datetime-local" value={at} onChange={e => setAt(e.target.value)} />
              </label>
            ) : (
              <p className="helper">
                {selected?.slots.length
                  ? selected.slots.join(' · ')
                  : 'Сначала задайте расписание публикаций.'}
              </p>
            )}
          </div>
        </fieldset>
        <aside className="threads-preview" aria-label="Предпросмотр публикации">
          <AtSign size={22} />
          <div>
            <strong>{selected?.name || selected?.username || 'Ваш аккаунт'}</strong>
            <small>@{selected?.username || 'username'}</small>
            <p>{text || 'Здесь появится текст публикации.'}</p>
          </div>
        </aside>
      </div>
      {post?.status === 'scheduled' && (
        <p className="helper">
          «Сохранить» обновит текст и сохранит текущее время. «Запланировать» изменит расписание.
        </p>
      )}
      <div className="actions threads-editor-actions">
        <Button variant="outline" disabled={busy} onClick={() => void act('draft')}>
          Сохранить{post?.status === 'scheduled' ? '' : ' черновик'}
        </Button>
        <Button
          variant="outline"
          disabled={busy || !ready || (mode === 'at' ? !at : !selected?.slots.length)}
          onClick={() => void act('schedule')}
        >
          <Clock3 size={15} /> Запланировать
        </Button>
        <Button
          disabled={busy || !ready || selected?.status !== 'active'}
          onClick={() => void act('publish')}
        >
          <Send size={15} /> Опубликовать сейчас
        </Button>
      </div>
      {selected?.paused && (
        <p className="helper">
          Очередь аккаунта на паузе. «Опубликовать сейчас» выполнит разовую публикацию.
        </p>
      )}
    </Modal>
  );
}

function ReviewPost({
  post,
  resource,
  onClose,
}: {
  post: ThreadsPost;
  resource: ThreadsResource;
  onClose: () => void;
}) {
  const [permalink, setPermalink] = useState('');
  const resolve = async (published: boolean) => {
    if (await resource.call('threads.post_resolve', { id: post.id, published, permalink })) onClose();
  };
  const account = resource.data?.accounts.find(a => a.id === post.account_id);
  return (
    <Modal title="Проверить публикацию" onClose={() => !resource.busy && onClose()}>
      <p>Проверьте профиль: ответ на публикацию не был получен. Повторная отправка ожидает вашего решения.</p>
      {account && (
        <Button
          variant="outline"
          onClick={() => void resource.visit(`https://www.threads.com/@${account.username}`)}
        >
          Открыть профиль <ExternalLink size={14} />
        </Button>
      )}
      <blockquote className="threads-review-text">{post.text}</blockquote>
      <label>
        Ссылка на опубликованный пост, если есть
        <input
          type="url"
          value={permalink}
          onChange={e => setPermalink(e.target.value)}
          placeholder="https://www.threads.com/…"
        />
      </label>
      <div className="actions">
        <Button
          disabled={
            resource.busy || (!!permalink && !/^https:\/\/(?:www\.)?threads\.(?:com|net)\//.test(permalink))
          }
          onClick={() => void resolve(true)}
        >
          Пост опубликован
        </Button>
        <Button variant="outline" disabled={resource.busy} onClick={() => void resolve(false)}>
          Не опубликован · в черновики
        </Button>
      </div>
    </Modal>
  );
}

export function ThreadsPosts({ onAccounts }: { onAccounts: () => void }) {
  const resource = useThreads();
  const { data, busy, call } = resource;
  const [editor, setEditor] = useState<ThreadsPost | null | undefined>();
  const [review, setReview] = useState<ThreadsPost>();
  const [filter, setFilter] = useState('queue');
  const [accountFilter, setAccountFilter] = useState('');
  if (!data)
    return (
      <div className="screen">
        <PageHeader page="threads" />
        <DataState {...resource} retry={resource.refresh} />
      </div>
    );
  const queue = data.posts.filter(p => p.status === 'scheduled' || p.status === 'publishing');
  const missed = data.posts.filter(p => p.status === 'missed');
  const visible = data.posts.filter(
    p =>
      (!accountFilter || p.account_id === Number(accountFilter)) &&
      (filter === 'all' ||
        (filter === 'queue'
          ? ['scheduled', 'publishing', 'missed', 'review', 'failed'].includes(p.status)
          : filter === p.status)),
  );
  return (
    <div className="screen work-flow threads-page">
      <PageHeader
        page="threads"
        count={String(queue.length)}
        actions={
          <Button onClick={() => setEditor(null)}>
            <Plus size={16} /> Новый пост
          </Button>
        }
      />
      <ErrorToast message={resource.mutationError || resource.error} />
      {!data.accounts.some(a => a.status === 'active') && (
        <p className="board-warning">
          Подключите аккаунт Threads, чтобы публиковать посты.{' '}
          <Button variant="outline" onClick={onAccounts}>
            Открыть аккаунты
          </Button>
        </p>
      )}
      {data.accounts.some(a => a.status !== 'disconnected') && <PublicationSchedule resource={resource} />}
      <section className="panel board">
        <div className="threads-toolbar">
          <div>
            <h2>Очередь публикаций</h2>
            <p className="helper">
              {data.paused ? 'На паузе' : 'Расписание включено'} · {data.zone.name} · локально на этом
              компьютере
            </p>
          </div>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void call('threads.queue_control', { paused: !data.paused })}
          >
            {data.paused ? <Play size={15} /> : <Pause size={15} />}
            {data.paused ? 'Продолжить' : 'Пауза'}
          </Button>
        </div>
        {missed.length > 0 && (
          <div className="board-warning threads-toolbar">
            <span>Пропущено публикаций: {missed.length}. Они не будут отправлены подряд после простоя.</span>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void call('threads.missed_reschedule', { ids: missed.map(p => p.id) })}
            >
              Перенести по расписанию
            </Button>
          </div>
        )}
        <div className="threads-filters">
          <label>
            Показать
            <select aria-label="Показать" value={filter} onChange={e => setFilter(e.target.value)}>
              <option value="queue">Очередь и задачи</option>
              <option value="draft">Черновики</option>
              <option value="published">Опубликованные</option>
              <option value="cancelled">Отменённые</option>
              <option value="all">Все публикации</option>
            </select>
          </label>
          <label>
            Аккаунт
            <select
              aria-label="Фильтр аккаунтов"
              value={accountFilter}
              onChange={e => setAccountFilter(e.target.value)}
            >
              <option value="">Все аккаунты</option>
              {data.accounts.map(a => (
                <option key={a.id} value={a.id}>
                  @{a.username}
                </option>
              ))}
            </select>
          </label>
        </div>
        {visible.length ? (
          <div className="crm-table">
            <table>
              <thead>
                <tr>
                  <th>Публикация</th>
                  <th>Аккаунт</th>
                  <th>Статус</th>
                  <th>Время</th>
                  <th aria-label="Действия" />
                </tr>
              </thead>
              <tbody>
                {visible.map(post => (
                  <tr key={post.id}>
                    <td className="threads-post-text">
                      <p>{post.text || 'Без текста'}</p>
                      {post.error && <small className="error-text">{post.error}</small>}
                    </td>
                    <td>@{data.accounts.find(a => a.id === post.account_id)?.username || 'не выбран'}</td>
                    <td>
                      <StatusBadge value={badge[post.status]} label={post.status_label} />
                      {post.next_attempt_at && <small>Повтор {dateTime(post.next_attempt_at)}</small>}
                    </td>
                    <td>
                      {dateTime(post.published_at || post.scheduled_at)}
                      {post.slot && <small>По расписанию аккаунта</small>}
                    </td>
                    <td>
                      <div className="threads-row-actions">
                        {editable.includes(post.status) && (
                          <Button variant="outline" disabled={busy} onClick={() => setEditor(post)}>
                            Открыть
                          </Button>
                        )}
                        {post.status === 'review' && (
                          <Button variant="outline" disabled={busy} onClick={() => setReview(post)}>
                            Проверить
                          </Button>
                        )}
                        {['draft', 'scheduled', 'missed', 'failed'].includes(post.status) && (
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() => void call('threads.post_cancel', { id: post.id })}
                          >
                            Отменить
                          </Button>
                        )}
                        {post.permalink && (
                          <Button
                            variant="outline"
                            aria-label="Открыть опубликованный пост"
                            onClick={() => void resource.visit(post.permalink!)}
                          >
                            <ExternalLink size={14} />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="threads-empty">
            <Clock3 size={26} />
            <h3>
              {filter === 'draft'
                ? 'Черновиков пока нет'
                : filter === 'published'
                  ? 'Опубликованных постов пока нет'
                  : 'Здесь пока нет публикаций'}
            </h3>
            <p className="helper">Создайте пост и сохраните его или выберите время публикации.</p>
            <Button variant="outline" onClick={() => setEditor(null)}>
              Создать пост
            </Button>
          </div>
        )}
      </section>
      <p className="helper">
        Публикации выходят, пока компьютер включён, подключён к интернету и программа работает — в том числе в
        трее.
      </p>
      {editor !== undefined && (
        <PostEditor
          post={editor}
          accounts={data.accounts}
          zone={data.zone.name}
          busy={busy}
          call={call}
          onClose={() => setEditor(undefined)}
        />
      )}
      {review && <ReviewPost post={review} resource={resource} onClose={() => setReview(undefined)} />}
    </div>
  );
}

export { ThreadsAccounts } from './ThreadsAccounts';

function PublicationSchedule({ resource }: { resource: ThreadsResource }) {
  const data = resource.data!;
  return (
    <details className="panel threads-publication-schedule">
      <summary>
        <Clock3 size={16} /> Расписание публикаций
      </summary>
      <p className="helper">
        Ежедневное время · {data.zone.name}. Используется при выборе «Следующее время по расписанию».
      </p>
      <div className="threads-schedule-list">
        {data.accounts
          .filter(a => a.status !== 'disconnected')
          .map(account => (
            <AccountSchedule key={account.id} account={account} resource={resource} />
          ))}
      </div>
    </details>
  );
}

function AccountSchedule({ account, resource }: { account: ThreadsAccount; resource: ThreadsResource }) {
  const savedTimes = account.slots.join(', ');
  const [times, setTimes] = useState(savedTimes);
  useEffect(() => setTimes(savedTimes), [savedTimes]);
  return (
    <form
      className="threads-slot-form"
      onSubmit={e => {
        e.preventDefault();
        void resource.call('threads.account_slots', {
          id: account.id,
          slots: times.split(/[,;\s]+/).filter(Boolean),
        });
      }}
    >
      <label>
        Время публикаций для @{account.username}
        <input
          aria-label={`Время публикаций для @${account.username}`}
          required
          value={times}
          onChange={e => setTimes(e.target.value)}
          placeholder="10:00, 18:30"
          disabled={resource.busy}
        />
      </label>
      <Button variant="outline" type="submit" disabled={resource.busy}>
        Сохранить расписание
      </Button>
      <Button
        variant="outline"
        type="button"
        disabled={resource.busy || account.status !== 'active'}
        onClick={() =>
          void resource.call('threads.account_control', {
            id: account.id,
            action: account.paused ? 'resume' : 'pause',
          })
        }
      >
        {account.paused ? <Play size={14} /> : <Pause size={14} />}
        {account.paused ? 'Продолжить публикации' : 'Пауза публикаций'}
      </Button>
    </form>
  );
}
