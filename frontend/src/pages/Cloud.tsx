import { useEffect, useState } from 'react';
import { CloudUpload, ExternalLink, ListPlus, RefreshCw, Square } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type {
  BrowserProfile,
  CloudCategory,
  CloudJob,
  CloudJobDetail,
  CloudSession,
  CloudView,
  ScoutSourceRow,
} from '../services/types';
import { number, relativeTime, waitLabel } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { TelegramPanel } from '../components/cloud/TelegramPanel';
import { OutreachPanel } from '../components/cloud/OutreachPanel';
import { Button } from '../components/ui/button';
import { StatusBadge } from '../components/DataState';
import { ErrorToast } from '../components/Toaster';
import { errorText } from '../lib/errors';
import {
  activeStage,
  categoryLabels,
  kindLabels,
  outcomeLabels,
  outreachViewLabels,
  stageLabel,
  stageTone,
  stepLabels,
  viaLabels,
  viewLabels,
} from '../components/cloud/cloudText';

const CATEGORIES: CloudCategory[] = ['ARTIST', 'PRODUCER', 'MEDIA', 'OTHER', 'UNKNOWN'];

const toggle = <T,>(list: T[], value: T) =>
  list.includes(value) ? list.filter(item => item !== value) : [...list, value];

/** What the parser on the server is doing; only what it reports, no invented percent. */
function Progress({ job }: { job: CloudJob }) {
  const p = job.progress || {};
  if (job.stage === 'queued')
    return (
      <p className="helper">
        Ждёт своей очереди на сервере{job.kind === 'outreach' ? ' (или пока аккаунт закончит поиск)' : ''}.
      </p>
    );
  if (job.stage !== 'collecting') return null;
  if (job.kind === 'outreach') {
    const total = p.total || job.params.usernames?.length || 0;
    const limited = !!p.daily_limit && (p.sent_24h ?? 0) >= p.daily_limit;
    return (
      <div className="cloud-progress">
        <p className="autopilot-status">
          Отправлено {number(p.sent ?? 0)} из {number(total)} · пропущено {number(p.skipped ?? 0)} · не ушло{' '}
          {number(p.failed ?? 0)}
        </p>
        {p.sender_status === 'rate_limited' ? (
          <p className="board-warning">
            Instagram ограничил действия аккаунта — рассылка ждёт конца перерыва.
          </p>
        ) : limited ? (
          <p className="helper">
            Дневной лимит {number(p.daily_limit ?? 0)} сообщений — продолжит, когда пройдут сутки.
          </p>
        ) : (
          !!p.queued && <p className="helper">Следующее сообщение — после паузы между сообщениями.</p>
        )}
      </div>
    );
  }
  const step = p.step ? stepLabels[p.step] || p.step : null;
  return (
    <div className="cloud-progress">
      <p className="autopilot-status">
        Найдено {number(p.found ?? 0)} из {number(p.target ?? job.params.target ?? 0)} · кандидатов{' '}
        {number(p.candidates ?? 0)}
        {step ? ` · сейчас: ${step}` : ''}
      </p>
      {!!p.waiting && <p className="helper">Пауза между страницами: {waitLabel(p.waiting)}</p>}
      {p.status === 'paused' && p.error && <p className="board-warning">{p.error}</p>}
    </div>
  );
}

function Counters({ job }: { job: CloudJob }) {
  const c = job.counters || {};
  const cells: [string, number | undefined][] =
    job.kind === 'outreach'
      ? [
          ['Получателей', c.total ?? job.params.usernames?.length],
          ['Отправлено', c.sent],
          ['Пропущено', c.skipped],
          ['Не ушло', c.failed],
        ]
      : [
          ['Найдено', c.found],
          ['В CRM', c.passed],
          ['Новых', c.added],
          ['Обновлено', c.updated],
          ['Не прошли фильтр', c.filtered],
          ['Ошибок', c.errors],
        ];
  return (
    <div className="cloud-counters">
      {cells.map(([label, value]) => (
        <div key={label}>
          <span>{label}</span>
          <b>{number(value ?? 0)}</b>
        </div>
      ))}
    </div>
  );
}

function JobDetail({ id, onCancel }: { id: string; onCancel: (id: string) => void }) {
  const [view, setView] = useState<CloudView>('all');
  const [page, setPage] = useState(0);
  const [openError, setOpenError] = useState('');
  const detail = useResource<CloudJobDetail>('cloud.job', { id, view, page }, 5000);
  const job = detail.data?.job;
  // Links open in the system browser through the app, which only lets http(s) through.
  const open = (address: string) =>
    api.openProfile(address).then(
      () => setOpenError(''),
      err => setOpenError(errorText(err)),
    );
  if (!job) return <section className="panel cloud-detail">{detail.error || 'Загрузка…'}</section>;
  const items = detail.data?.items ?? [];
  const outreach = job.kind === 'outreach';
  const views = outreach ? outreachViewLabels : viewLabels;
  const names = outreach
    ? (job.params.usernames ?? []).map(name => '@' + name)
    : (job.params.sources ?? []).map(
        source => '@' + (source.match(/instagram\.com\/([^/?#]+)/)?.[1] ?? source),
      );
  return (
    <section className="panel cloud-detail">
      <div className="board-heading">
        <h2>
          {kindLabels[job.kind]} от {relativeTime(job.created_at)}{' '}
          <StatusBadge value={stageTone[job.stage]} label={stageLabel(job)} />
        </h2>
        {activeStage(job) && (
          <Button variant="outline" disabled={job.cancel_requested} onClick={() => onCancel(job.id)}>
            <Square size={14} /> {job.cancel_requested ? 'Отменяется…' : 'Отменить'}
          </Button>
        )}
      </div>
      <p className="helper">
        {outreach ? 'Получатели' : 'Источники'} ({names.length}): {names.slice(0, 8).join(', ')}
        {names.length > 8 ? ` и ещё ${names.length - 8}` : ''}
        {outreach && job.params.messages?.length
          ? ` · вариантов сообщения: ${job.params.messages.length}`
          : ''}
      </p>
      <Progress job={job} />
      <Counters job={job} />
      {job.error && <p className="board-warning">{job.error}</p>}
      <ErrorToast message={openError} />
      <div className="crm-tabs" role="tablist" aria-label="Результаты">
        {(Object.keys(views) as CloudView[]).map(key => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={view === key}
            className={view === key ? 'active' : ''}
            onClick={() => {
              setView(key);
              setPage(0);
            }}
          >
            {views[key]}
          </button>
        ))}
      </div>
      {items.length && outreach ? (
        <div className="crm-table cloud-table">
          <table>
            <thead>
              <tr>
                <th>Профиль</th>
                <th>Итог</th>
                <th>Почему</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.id}>
                  <td>
                    <button
                      type="button"
                      className="cloud-user"
                      onClick={() => open(`https://www.instagram.com/${item.username}/`)}
                    >
                      @{item.username} <ExternalLink size={12} />
                    </button>
                  </td>
                  <td>{item.outcome ? outcomeLabels[item.outcome] : '—'}</td>
                  <td className="cloud-reason">{item.note || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : items.length ? (
        <div className="crm-table cloud-table">
          <table>
            <thead>
              <tr>
                <th>Профиль</th>
                <th>Где найден</th>
                <th>Категория</th>
                <th>Почему</th>
                <th>Итог</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.id}>
                  <td>
                    <button
                      type="button"
                      className="cloud-user"
                      onClick={() => open(`https://www.instagram.com/${item.username}/`)}
                    >
                      @{item.username} <ExternalLink size={12} />
                    </button>
                    {item.profile?.full_name && <small>{item.profile.full_name}</small>}
                    {item.profile?.followers != null && (
                      <small>{number(item.profile.followers)} подписчиков</small>
                    )}
                    {!!item.profile?.emails?.length && <small>{item.profile.emails.join(', ')}</small>}
                  </td>
                  <td>
                    {item.origins[0]?.source ? `@${item.origins[0].source}` : '—'}
                    {item.via.length > 0 && (
                      <small>{item.via.map(via => viaLabels[via] || via).join(', ')}</small>
                    )}
                    {item.origins[0]?.post && (
                      <small>
                        <button
                          type="button"
                          className="cloud-link"
                          onClick={() => open(item.origins[0].post as string)}
                        >
                          публикация
                        </button>
                      </small>
                    )}
                  </td>
                  <td>
                    {item.category ? categoryLabels[item.category] : '—'}
                    {item.confidence != null && <small>{item.confidence}%</small>}
                  </td>
                  <td className="cloud-reason">{item.reason || item.note || '—'}</td>
                  <td>{item.outcome ? outcomeLabels[item.outcome] : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty-copy">
          {!activeStage(job)
            ? 'Здесь пусто.'
            : outreach
              ? 'Здесь появится, кому написали и кого пропустили.'
              : 'Лиды появятся здесь по мере того, как парсер их найдёт.'}
        </p>
      )}
      {(page > 0 || items.length === detail.data?.page_size) && (
        <div className="actions">
          <Button variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Назад
          </Button>
          <Button
            variant="outline"
            disabled={items.length < (detail.data?.page_size ?? 0)}
            onClick={() => setPage(page + 1)}
          >
            Дальше
          </Button>
        </div>
      )}
    </section>
  );
}

/** «Облачный парсер»: our Lead Scout on the server, leads straight into the CRM. */
export function Cloud() {
  const jobs = useResource<CloudJob[]>('cloud.jobs', {}, 5000);
  const sessions = useResource<CloudSession[]>('cloud.sessions', {}, 30000);
  const sources = useResource<ScoutSourceRow[]>('scout.source_list');
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [profile, setProfile] = useState('');
  const [sourceText, setSourceText] = useState('');
  const [target, setTarget] = useState(50);
  const [categories, setCategories] = useState<CloudCategory[]>(['ARTIST', 'PRODUCER']);
  const [selected, setSelected] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .browser<BrowserProfile[]>('list')
      .then(list => {
        setProfiles(list);
        setProfile(current => current || list.find(item => item.cookie_count > 0)?.id || list[0]?.id || '');
      })
      .catch(err => setError(errorText(err)));
  }, []);
  useEffect(() => {
    if (!selected && jobs.data?.length) setSelected(jobs.data[0].id);
  }, [jobs.data, selected]);

  const enabledSources = (sources.data ?? []).filter(row => row.enabled).map(row => row.username);
  const sourceCount = sourceText.split(/[\n,]+/).filter(line => line.trim()).length;
  const account = profiles.find(item => item.id === profile);
  const sent = sessions.data?.find(item => item.profile_id === profile);

  const start = async () => {
    setBusy(true);
    setError('');
    try {
      // One id per click: a repeated request returns the same job.
      const detail = await api.request<CloudJobDetail>('cloud.start', {
        profile_id: profile,
        sources: sourceText,
        target,
        categories,
        request_id: crypto.randomUUID(),
      });
      setSelected(detail.job.id);
      jobs.refresh();
      sessions.refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const cancel = async (id: string) => {
    setError('');
    try {
      await api.request('cloud.cancel', { id });
      jobs.refresh();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="screen work-flow">
      <PageHeader page="cloud" count={jobs.data ? `${number(jobs.data.length)} задач` : undefined} />
      <TelegramPanel />
      <section className="panel board cloud-form">
        <div className="board-heading">
          <h2>Новая задача</h2>
          <span className="helper">Парсер работает на сервере: приложение и компьютер можно выключить</span>
        </div>
        <div className="cloud-grid">
          <fieldset>
            <legend>Аккаунт Instagram</legend>
            <div className="cloud-stack">
              <select value={profile} onChange={event => setProfile(event.target.value)}>
                {profiles.map(item => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                    {item.cookie_count ? '' : ' — нет входа'}
                  </option>
                ))}
              </select>
              <small className="helper">
                {account?.proxy
                  ? `Через прокси аккаунта ${account.proxy.host}:${account.proxy.port}.`
                  : 'Без прокси: Instagram увидит IP сервера.'}{' '}
                Сессия аккаунта отправится на сервер при запуске
                {sent ? ` (последний раз ${relativeTime(sent.updated_at)})` : ''}. Пока парсер работает в
                облаке, не запускайте этим аккаунтом парсинг на компьютере.
              </small>
            </div>
          </fieldset>
          <fieldset>
            <legend>Сколько и кого</legend>
            <div className="cloud-stack">
              <label>
                <span>Найти новых лидов</span>
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={target}
                  onChange={event => setTarget(Number(event.target.value))}
                />
              </label>
              <div className="cloud-categories">
                {CATEGORIES.map(category => (
                  <label key={category} className="check-row">
                    <input
                      type="checkbox"
                      checked={categories.includes(category)}
                      onChange={() => setCategories(toggle(categories, category))}
                    />
                    {categoryLabels[category]}
                  </label>
                ))}
              </div>
              <small className="helper">
                Методы поиска и фильтры берутся из настроек парсера в приложении.
              </small>
            </div>
          </fieldset>
        </div>
        <label className="cloud-field">
          <span>Источники — по одному на строку</span>
          <textarea
            className="board-bulk"
            rows={5}
            placeholder={'rapgoat.tv\n@topdailyrap'}
            value={sourceText}
            onChange={event => setSourceText(event.target.value)}
          />
          <small>{sourceCount} из 500</small>
        </label>
        <ErrorToast message={error} />
        <div className="board-footer">
          <Button
            variant="outline"
            disabled={!enabledSources.length}
            onClick={() => setSourceText(enabledSources.join('\n'))}
          >
            <ListPlus size={15} /> Взять источники парсера ({enabledSources.length})
          </Button>
          <Button
            className="autopilot-go"
            disabled={busy || !profile || !sourceCount || !categories.length || target < 1}
            onClick={() => void start()}
          >
            <CloudUpload size={16} /> {busy ? 'Отправляю…' : 'Запустить на сервере'}
          </Button>
        </div>
      </section>

      <OutreachPanel
        profiles={profiles}
        onStarted={id => {
          setSelected(id);
          jobs.refresh();
          sessions.refresh();
        }}
      />

      <section className="panel cloud-history">
        <div className="board-heading">
          <h2>История задач</h2>
          <Button variant="outline" icon aria-label="Обновить" onClick={jobs.refresh}>
            <RefreshCw size={14} />
          </Button>
        </div>
        <ErrorToast message={jobs.error} />
        {jobs.data && !jobs.data.length && <p className="empty-copy">Задач пока не было.</p>}
        <div className="cloud-jobs">
          {(jobs.data ?? []).map(job => (
            <button
              key={job.id}
              type="button"
              className={`cloud-job${selected === job.id ? ' active' : ''}`}
              onClick={() => setSelected(job.id)}
            >
              <StatusBadge value={stageTone[job.stage]} label={stageLabel(job)} />
              {job.kind === 'outreach' ? (
                <>
                  <span className="cloud-job-sources">
                    Кому: {(job.params.usernames ?? []).slice(0, 3).join(', ')}
                  </span>
                  <span className="helper">
                    {relativeTime(job.created_at)} · отправлено {number(job.counters.sent ?? 0)} из{' '}
                    {number(job.params.usernames?.length ?? 0)}
                  </span>
                </>
              ) : (
                <>
                  <span className="cloud-job-sources">
                    {(job.params.sources ?? []).slice(0, 3).join(', ')}
                  </span>
                  <span className="helper">
                    {relativeTime(job.created_at)} · найдено {number(job.counters.found ?? 0)} · в CRM{' '}
                    {number(job.counters.passed ?? 0)} из {number(job.params.target ?? 0)}
                  </span>
                </>
              )}
            </button>
          ))}
        </div>
      </section>

      {selected && <JobDetail key={selected} id={selected} onCancel={id => void cancel(id)} />}
    </div>
  );
}
