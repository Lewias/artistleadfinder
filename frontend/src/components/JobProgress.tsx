import { ErrorToast } from './Toaster';
import { errorText } from '../lib/errors';
import { useState } from 'react';
import { api } from '../services/api';
import type { SearchJob } from '../services/types';
import { number, statusLabels } from '../lib/format';
import { StatusBadge } from './DataState';
import { Button } from './ui/button';

export function JobProgress({ job, refresh }: { job: SearchJob; refresh: () => void }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const active = ['queued', 'running', 'paused'].includes(job.status) && job.stage !== 'interrupted';
  const control = async (action: string) => {
    setBusy(true);
    setError('');
    try {
      await api.request('jobs.control', { id: job.id, action });
      refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel job-progress">
      <div className="section-heading">
        <h2>{job.name}</h2>
        <StatusBadge value={job.status} label={statusLabels[job.status]} />
      </div>
      <div className="metrics compact">
        {[
          { label: 'Обнаружений', value: job.candidates_found },
          { label: 'Профилей разобрано', value: job.profiles_analyzed },
          { label: 'Артистов', value: job.artists_detected },
          { label: 'Подходят', value: job.qualified_leads },
        ].map(item => (
          <div key={item.label}>
            <span>{item.label}</span>
            <strong>{number(item.value)}</strong>
          </div>
        ))}
      </div>
      <div className="progress-caption">
        <span>Целевой набор лидов</span>
        <span>
          {job.qualified_leads} / {job.target_leads}
        </span>
      </div>
      <progress value={job.qualified_leads} max={job.target_leads} />
      <p className="helper">
        {job.stage === 'interrupted'
          ? 'Поиск прерван при закрытии приложения. Создайте новый поиск.'
          : active
            ? 'Обнаружение и анализ профилей. Показаны реальные счётчики.'
            : 'Обработка завершена. Результатов может быть меньше заданной цели.'}
      </p>
      {active && (
        <div className="actions">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void control(job.status === 'paused' ? 'resume' : 'pause')}
          >
            {job.status === 'paused' ? 'Продолжить' : 'Пауза'}
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => void control('cancel')}>
            Остановить
          </Button>
        </div>
      )}
      <ErrorToast message={error} />
      {job.errors.map((err, index) => (
        <p className="error-text" key={index}>
          {err.provider && `${err.provider}: `}
          {err.message}
        </p>
      ))}
    </section>
  );
}
