import { useState } from 'react';
import { ArrowUpRight, Radar, SlidersHorizontal } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { ScoutAccountRow, ScoutSourceRow } from '../services/types';
import { Button } from './ui/button';
import { LeadDetail } from './LeadDetail';
import { PageHeader } from './PageHeader';
import { plural } from '../lib/format';
import { ScoutAccounts } from './ScoutAccounts';
import { ScoutActivity } from './ScoutActivity';
import { ScoutSettingsPanel } from './ScoutSettingsPanel';
import { ScoutSources } from './ScoutSourceTable';

type Evidence = { source: string; url: string; caption: string; published_at: string | null };
type ServiceScore = { score: number; reasons: { text: string; evidence: Evidence | null }[] };
type ScoutLead = {
  id: number;
  username: string;
  name: string;
  priority: number;
  contacts: string[];
  explanation: string;
  services: Record<string, ServiceScore>;
  evidence: Evidence[];
};
const labels: Record<string, string> = {
  beats: 'Биты',
  mixing: 'Сведение / мастеринг',
  promotion: 'Продвижение',
};

export function ScoutDiscovery() {
  const [error, setError] = useState('');
  const [lead, setLead] = useState<number | null>(null);
  const sources = useResource<ScoutSourceRow[]>('scout.source_list', {}, 5000);
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 2000);
  const results = useResource<ScoutLead[]>('scout.results', {}, 4000);
  const enabled = sources.data?.filter(row => row.enabled).length ?? 0;
  const openEvidence = (url: string) => void api.openProfile(url).catch(err => setError(String(err)));
  return (
    <section className="scout-workspace">
      <PageHeader
        page="discovery"
        count={plural(enabled, ['активный источник', 'активных источника', 'активных источников'])}
      />
      <ScoutSources rows={sources.data} refresh={sources.refresh} />
      <ScoutSettingsPanel />
      <ScoutAccounts
        rows={accounts.data}
        error={accounts.error}
        disabled={!enabled}
        refresh={() => {
          accounts.refresh();
          sources.refresh();
          results.refresh();
        }}
      />
      <ScoutActivity accounts={accounts.data || []} />
      {(error || results.error) && (
        <p role="alert" className="error-text">
          {error || results.error}
        </p>
      )}
      <details className="scout-limits">
        <summary>
          <SlidersHorizontal size={15} /> Как работает Lead Scout
        </summary>
        <p className="helper">
          Источник → поиск кандидатов выбранными методами (авторы и соавторы публикаций, комментаторы,
          отметки, подписчики) → проверка дублей → профиль → локальная классификация (артист / продюсер /
          медиа / другое) → при необходимости AI → фильтры (подписчики, тип профиля, контакты) → лид в базе.
          Разобранные публикации и профили запоминаются и не проверяются повторно. Источники сканируются по
          очереди, недавно просканированные пропускаются до конца кулдауна. При ограничении Instagram очередь
          встаёт на паузу и выдерживает перерыв. Сообщения не отправляются.
        </p>
      </details>
      <div className="section-title">
        <h2>
          Лиды для работы <span className="page-count">{results.data?.length ?? '—'}</span>
        </h2>
        <p className="helper">По приоритету предложения · без отклонённых и уже получивших сообщение</p>
      </div>
      {!results.data && !results.error && (
        <div className="notice" role="status">
          Загружаем лидов…
        </div>
      )}
      {results.data?.length === 0 && (
        <div className="scout-empty">
          <div className="empty-orbit">
            <Radar size={36} strokeWidth={1.4} />
          </div>
          <h3>Здесь начнутся новые знакомства</h3>
          <p>Добавьте источники и запустите Lead Scout на одном из аккаунтов.</p>
        </div>
      )}
      {results.data?.map(item => (
        <article className="panel scout-lead" key={item.id}>
          <div className="lead-card-heading">
            <div className="avatar">{(item.name || item.username).slice(0, 2).toUpperCase()}</div>
            <div>
              <h3>{item.name || item.username}</h3>
              <span className="helper">@{item.username}</span>
            </div>
            <Button variant="outline" onClick={() => setLead(item.id)}>
              Карточка <ArrowUpRight size={15} />
            </Button>
          </div>
          <p className="helper">{item.explanation}</p>
          <div className="scout-scores">
            {Object.entries(item.services).map(([key, service]) => (
              <div key={key}>
                <strong>
                  {labels[key]}{' '}
                  <span>
                    {service.score}
                    <small>/100</small>
                  </span>
                </strong>
                <div className="service-meter">
                  <i style={{ width: `${Math.max(0, Math.min(100, service.score))}%` }} />
                </div>
                <details>
                  <summary>Обоснование оценки</summary>
                  {service.reasons.length ? (
                    service.reasons.map((reason, i) => (
                      <div key={i}>
                        <p>{reason.text}</p>
                        {reason.evidence && (
                          <>
                            <blockquote>{reason.evidence.caption}</blockquote>
                            <Button variant="outline" onClick={() => openEvidence(reason.evidence!.url)}>
                              Комментарий ·{' '}
                              {reason.evidence.published_at
                                ? new Date(reason.evidence.published_at).toLocaleDateString('ru-RU')
                                : 'Дата неизвестна'}
                            </Button>
                          </>
                        )}
                      </div>
                    ))
                  ) : (
                    <p className="helper">Нет подтверждающих сигналов.</p>
                  )}
                </details>
              </div>
            ))}
          </div>
          <details>
            <summary>Где найден артист</summary>
            {item.evidence.map((e, i) => (
              <p key={i}>
                {e.source}{' '}
                <Button variant="outline" onClick={() => openEvidence(e.url)}>
                  Открыть публикацию
                </Button>
              </p>
            ))}
          </details>
          <p>Контакт из биографии: {item.contacts.join(', ') || 'не указан'}</p>
        </article>
      ))}
      {lead !== null && <LeadDetail id={lead} close={() => setLead(null)} refresh={results.refresh} />}
    </section>
  );
}
