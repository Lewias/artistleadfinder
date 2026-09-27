import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X, ExternalLink } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { LeadDetail as Detail } from '../services/types';
import { number, activity, statusLabels } from '../lib/format';
import { DataState } from './DataState';
import { Button } from './ui/button';

export function LeadDetail({ id, close, refresh }: { id: number; close: () => void; refresh: () => void }) {
  const resource = useResource<Detail>('leads.detail', { id });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const lead = resource.data;
  const capture = lead?.analysis?.extracted_signals?.browser_capture;
  const action = async (status: string) => {
    setBusy(true); setError('');
    try { await api.request('leads.status', { id, status }); resource.refresh(); refresh(); }
    catch (err) { setError(String(err)); } finally { setBusy(false); }
  };
  const open = async (address: string) => { try { await api.openProfile(address); } catch (err) { setError(String(err)); } };
  return <Dialog.Root open onOpenChange={value => { if (!value) close(); }}><Dialog.Portal><Dialog.Overlay className="drawer-overlay" /><Dialog.Content className="drawer">
    <Dialog.Close className="drawer-close" aria-label="Закрыть профиль"><X size={20} /></Dialog.Close>
    <Dialog.Title className="drawer-title">{lead ? `@${lead.username}` : 'Профиль'}</Dialog.Title><Dialog.Description className="helper">Анализ профиля и причины оценки</Dialog.Description>
    {!lead ? <DataState {...resource} retry={resource.refresh} /> : <>
      <div className="avatar large">{lead.display_name.slice(0, 1) || lead.username.slice(0, 1)}</div><h2>{lead.display_name}</h2><p>{lead.genres.join(' / ') || 'Жанр не определён'}</p><p className="helper">{capture?.unknown_fields.includes('followers') ? 'Подписчики не прочитаны' : `${number(lead.followers)} подписчиков`} · {lead.platform === 'mock' ? 'Демонстрационный профиль' : lead.platform}{lead.is_private && ' · Закрытый аккаунт'}</p>
      {capture && <><h3>Данные со страницы</h3>{capture.discovery && <p className="helper">Найдено через Brave Search: {capture.discovery.query}</p>}<p className="helper">Проверено: {new Date(capture.captured_at).toLocaleString('ru-RU')}. Счётчики на сайте могут быть округлены.</p>{capture.unknown_fields.length > 0 && <p className="helper">Не прочитаны: {capture.unknown_fields.map(field => ({ followers: 'подписчики', following: 'подписки', bio: 'биография', last_activity_at: 'последняя активность' }[field] || field)).join(', ')}. Для нового профиля отсутствие данных снижает оценку.</p>}<details><summary>Сведения, использованные при анализе</summary><p className="bio">{capture.description}</p><p className="bio">{capture.header}</p></details></>}
      {lead.scout && <><h3>Что предложить</h3><p className="helper">{lead.scout.explanation} Оценка соответствия услуге, не вероятность покупки.</p>{Object.entries(lead.scout.services).map(([key, value]) => <div key={key}><strong>{({ beats: 'Биты', mixing: 'Сведение / мастеринг', promotion: 'Продвижение' }[key] || key)}: {value.score}/100</strong>{value.reasons.map((reason, i) => <p className="helper" key={i}>{reason.text}</p>)}</div>)}</>}
      <div className="detail-score"><span>ОБЩАЯ ОЦЕНКА ПРОФИЛЯ</span><strong>{lead.lead_score}<small> / 100</small></strong></div>
      <h3>Почему этот профиль?</h3>{lead.breakdown.map(item => <div className="breakdown" key={item.rule}><span>+{item.points}</span>{item.reason}</div>)}
      <h3>Сигналы анализа</h3>{lead.analysis?.signals.map(signal => <p className="helper" key={signal}>{signal}</p>)}<p className="helper">Вероятность артиста: {Math.round(lead.artist_probability * 100)}%</p>
      <h3>{capture?.bio_method === 'header_excerpt' ? 'Текст шапки профиля' : 'Биография'}</h3><p className="bio">{lead.bio || 'Не указана'}</p>
      {lead.external_url && <><h3>Внешняя ссылка</h3><button className="text-action link-wrap" onClick={() => void open(lead.external_url)}>{lead.external_url} <ExternalLink size={12} /></button></>}
      <h3>Обнаружен из</h3>{lead.sources.map(source => <div className="source-line" key={source.id}><span>{source.source_provider} / {source.source_type}</span><strong>{source.source_value}</strong></div>)}
      <h3>Последняя активность</h3><p className="helper">{activity(lead.last_activity_at)}</p>
      <label>Статус CRM<select value={lead.status} disabled={busy} onChange={event => void action(event.target.value)}>{['new', 'reviewed', 'qualified', 'rejected', 'contacted'].map(status => <option key={status} value={status}>{statusLabels[status]}</option>)}</select></label><p className="helper">«Контакт отмечен» — ручная отметка. Приложение никому не пишет.</p>
      <div className="actions"><Button variant="outline" disabled={!lead.profile_url} onClick={() => void open(lead.profile_url)}>Открыть профиль</Button><Button disabled={busy} onClick={() => void action('qualified')}>Подходит</Button><Button variant="outline" disabled={busy} onClick={() => void action('rejected')}>Отклонить</Button></div>
    </>}{error && <p role="alert" className="error-text">{error}</p>}
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
