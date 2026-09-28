import { ArrowRight, Compass } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { DashboardData } from '../services/types';
import { number, date, statusLabels } from '../lib/format';
import { DataState, StatusBadge } from '../components/DataState';
import { Button } from '../components/ui/button';
import type { PageId } from '../navigation';
import { PageHeader } from '../components/PageHeader';

export function Dashboard({ navigate }: { navigate: (page: PageId) => void }) {
  const resource = useResource<DashboardData>('dashboard.get', {}, 5000);
  const { data } = resource;
  if (!data)
    return (
      <>
        <PageHeader page="dashboard" />
        <DataState {...resource} retry={resource.refresh} />
      </>
    );
  return (
    <>
      <PageHeader page="dashboard" />
      {resource.error && <DataState {...resource} retry={resource.refresh} />}
      <div className="stat-cards">
        {[
          { label: 'Всего профилей', value: data.total },
          { label: 'Подходящие лиды', value: data.qualified },
          { label: 'Открыто сегодня', value: data.today },
          { label: 'Активные поиски', value: data.active },
        ].map(metric => (
          <div className="stat-card" key={metric.label}>
            <strong>{number(metric.value)}</strong>
            <span>{metric.label}</span>
          </div>
        ))}
      </div>
      {
        <div className="welcome-card">
          <div>
            <span className="tag">НОВЫЕ ВОЗМОЖНОСТИ</span>
            <h2>Найдите артистов для следующей коллаборации.</h2>
            <p>
              Добавьте музыкальные медиа и скаут-паблики. Получите артистов с обоснованием для каждой из трёх
              услуг.
            </p>
            <Button onClick={() => navigate('discovery')}>
              Перейти к скаутингу <ArrowRight size={16} />
            </Button>
          </div>
          <Compass size={96} strokeWidth={0.8} />
        </div>
      }
      <div className="dashboard-grid">
        <section className="panel">
          <div className="section-heading">
            <h2>Последние поиски</h2>
            <button className="text-action" onClick={() => navigate('history')}>
              Все поиски →
            </button>
          </div>
          {data.jobs.length ? (
            data.jobs.map(job => (
              <div className="summary-row" key={job.id}>
                <div>
                  <strong>{job.name}</strong>
                  <small>
                    {date(job.created_at)} · {number(job.qualified_leads)} подходящих
                  </small>
                </div>
                <StatusBadge value={job.status} label={statusLabels[job.status]} />
              </div>
            ))
          ) : (
            <p className="empty-copy">Поисков пока нет. Начните с раздела «Поиск артистов».</p>
          )}
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>Лучшие оценки</h2>
            <button className="text-action" onClick={() => navigate('leads')}>
              Открыть базу →
            </button>
          </div>
          {data.leads.length ? (
            data.leads.map(lead => (
              <div className="summary-row" key={lead.id}>
                <div>
                  <strong>@{lead.username}</strong>
                  <small>
                    {lead.primary_genre || 'Жанр не определён'} · {number(lead.followers)} подписчиков
                  </small>
                </div>
                <span className="score-pill">{lead.lead_score}</span>
              </div>
            ))
          ) : (
            <p className="empty-copy">Профили появятся после первого поиска.</p>
          )}
        </section>
        <section className="panel">
          <h2>Жанры в базе</h2>
          {data.genres.length ? (
            data.genres.map(genre => (
              <div className="chart-row" key={genre.name}>
                <span>{genre.name}</span>
                <div className="chart-track">
                  <i style={{ width: `${(genre.count / Math.max(1, data.genres[0].count)) * 100}%` }} />
                </div>
                <small>{number(genre.count)}</small>
              </div>
            ))
          ) : (
            <p className="empty-copy">Для диаграммы нужны результаты анализа.</p>
          )}
        </section>
        <section className="panel">
          <h2>Распределение оценок</h2>
          {data.distribution.map((count, index) => (
            <div className="chart-row" key={index}>
              <span>{['0–39', '40–69', '70–89', '90–100'][index]}</span>
              <div className="chart-track">
                <i style={{ width: `${(count / Math.max(1, ...data.distribution)) * 100}%` }} />
              </div>
              <small>{number(count)}</small>
            </div>
          ))}
        </section>
      </div>
    </>
  );
}
