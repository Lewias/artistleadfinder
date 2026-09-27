import { useState } from 'react';
import { api } from '../services/api';
import { Button } from './ui/button';

type Candidate = { url: string; username: string; title: string };
type Results = { results: Candidate[]; search_url: string };
export function CandidateSearch({ disabled, onSelect }: { disabled: boolean; onSelect: (urls: string[], query: string) => void }) {
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [results, setResults] = useState<Results | null>(null);
  const [completedQuery, setCompletedQuery] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const search = async () => {
    setBusy(true); setError(''); setResults(null); setSelected([]);
    try {
      const response = await api.browser<Results>('search', { query: query.trim() });
      setResults(response); setCompletedQuery(query.trim()); setSelected(response.results.map(item => item.url));
    } catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  };
  return <div className="panel">
    <h3>Найти новые профили</h3>
    <p className="helper">Поиск публичных Instagram-профилей через Brave Search. Укажите жанр, город или фразу из биографии. Запрос передаётся поисковику; cookies Instagram не нужны.</p>
    <label>Поисковый запрос<input maxLength={200} value={query} disabled={busy || disabled} placeholder="independent rapper London" onChange={event => setQuery(event.target.value)} /></label>
    <div className="actions"><Button disabled={busy || disabled || query.trim().length < 2} onClick={() => void search()}>{busy ? 'Ищем профили…' : 'Найти профили'}</Button>
      {busy && <Button variant="outline" onClick={() => void api.browser('search_cancel').catch(err => setError(String(err)))}>Отменить поиск</Button>}
    </div>
    {error && <p role="alert" className="error-text">{error}</p>}
    {results && <><p role="status">Найдено профилей: {results.results.length}. Запрос: {completedQuery}</p>
      <div className="candidate-results">{results.results.map(item => <label className="candidate-result" key={item.url}><input type="checkbox" checked={selected.includes(item.url)} disabled={disabled || busy} onChange={event => setSelected(current => event.target.checked ? [...current, item.url] : current.filter(url => url !== item.url))} /> @{item.username} — {item.title}</label>)}</div>
      {results.results.length > 0 ? <Button variant="outline" disabled={disabled || busy || !selected.length} onClick={() => onSelect(selected, completedQuery)}>Передать выбранные в очередь ({selected.length})</Button> : <p className="helper">Попробуйте более широкий запрос или другое написание жанра и города.</p>}
    </>}
    <p className="helper">До 20 профилей с одной страницы выдачи. Поисковая выдача может быть неполной или устаревшей; принадлежность к артистам проверяется при обработке профиля. Поиск по подпискам пока не поддерживается.</p>
  </div>;
}
