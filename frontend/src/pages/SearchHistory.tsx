import { useState } from 'react';
import { useResource } from '../hooks/useResource';
import type { SearchJob } from '../services/types';
import { number, date, statusLabels } from '../lib/format';
import { DataState, StatusBadge } from '../components/DataState';
import { JobProgress } from '../components/JobProgress';
import { Button } from '../components/ui/button';
import { Leads } from './Leads';

function duration(job: SearchJob) {
  if (!job.started_at) return '—';
  const seconds = Math.max(0, Math.floor((new Date(job.completed_at || Date.now()).getTime() - new Date(job.started_at).getTime()) / 1000));
  return `${Math.floor(seconds / 60)} мин. ${seconds % 60} с.`;
}
export function SearchHistory() {
  const resource = useResource<SearchJob[]>('jobs.list', {}, 2000);
  const [selected, setSelected] = useState<number>();
  const job = resource.data?.find(item => item.id === selected);
  return <><DataState {...resource} retry={resource.refresh} />
    {resource.data && <><div className="table-container"><table><thead><tr><th>Название</th><th>Создан</th><th>Статус</th><th>Обнаружений</th><th>Артистов</th><th>Подходят</th><th>Длительность</th></tr></thead><tbody>{resource.data.map(item => <tr key={item.id}><td><button className="text-action" onClick={() => setSelected(item.id)}>{item.name}</button></td><td>{date(item.created_at)}</td><td><StatusBadge value={item.status} label={statusLabels[item.status]} /></td><td>{number(item.candidates_found)}</td><td>{number(item.artists_detected)}</td><td>{number(item.qualified_leads)}</td><td>{duration(item)}</td></tr>)}</tbody></table>{resource.data.length === 0 && <p className="empty-copy">Поисков пока нет. Создайте первый поиск в разделе «Поиск артистов».</p>}</div><p className="helper">Последние 200 поисков. Нажмите название, чтобы открыть параметры и профили.</p></>}
    {job && <div className="history-detail"><div className="section-heading"><h2>Результаты поиска</h2><Button variant="outline" onClick={() => setSelected(undefined)}>Скрыть</Button></div><JobProgress job={job} refresh={resource.refresh} /><section className="panel configuration"><h2>Параметры</h2><dl>{[{ name: 'Аккаунты', value: job.seed_accounts.join(', ') }, { name: 'Ключевые слова', value: job.keywords.join(', ') }, { name: 'Хештеги', value: job.hashtags.join(', ') }, { name: 'Жанры', value: job.genres.join(', ') || 'Все' }, { name: 'Аудитория', value: `${number(job.min_followers)}–${number(job.max_followers)}` }, { name: 'Активность', value: `${job.activity_days} дней` }, { name: 'Оценка от', value: String(job.minimum_score) }, { name: 'Цель', value: String(job.target_leads) }].map(item => <div key={item.name}><dt>{item.name}</dt><dd>{item.value || 'Не заданы'}</dd></div>)}</dl></section><div className="section-heading"><h2>Профили из этого поиска</h2></div><Leads key={job.id} jobId={job.id} /></div>}
  </>;
}
