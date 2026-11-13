import { useState } from 'react';
import { Download, PlugZap, RefreshCw, Save, ScanSearch, Trash2 } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { WarmupAccount, WarmupEvent, WarmupPartner, WarmupState } from '../services/types';
import { number, relativeTime } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { StatusBadge } from '../components/DataState';
import { ErrorToast } from '../components/Toaster';
import { time, useCall } from '../components/email/emailUi';

const PLACEMENTS: Record<NonNullable<WarmupEvent['placement']>, [string, string]> = {
  inbox: ['completed', 'Входящие'],
  spam: ['paused', 'Спам → вытащили'],
  missing: ['failed', 'Не дошло'],
};
const STEPS = ['Письмо', 'Ответ', 'Ответ на ответ'];
const PARTNER_BADGES: Record<WarmupPartner['status'], [string, string]> = {
  new: ['queued', 'Новый'],
  ok: ['running', 'Работает'],
  error: ['failed', 'Ошибка'],
};

/** The warm-up: the pool of our mailboxes, the Nstbrowser partners and what happened. */
export function EmailWarmup({ onAccounts }: { onAccounts: () => void }) {
  const state = useResource<WarmupState>('email.warmup_state', {}, 10000);
  const data = state.data;
  const accounts = data?.accounts ?? [];
  const enabled = accounts.filter(item => item.pool_warmup);
  const inbox = enabled.reduce((sum, item) => sum + item.inbox_7d, 0);
  const spam = enabled.reduce((sum, item) => sum + item.spam_7d, 0);
  const partners = (data?.partners ?? []).filter(item => item.enabled && item.address).length;
  return (
    <div className="screen work-flow">
      <PageHeader
        page="email-warmup"
        count={`${enabled.length}`}
        actions={
          <Button variant="outline" icon aria-label="Обновить" onClick={state.refresh}>
            <RefreshCw size={14} />
          </Button>
        }
      />
      <section className="panel board">
        <div className="cloud-counters">
          <div>
            <span>Во «Входящих» за 7 дней</span>
            <b>{inbox + spam ? `${Math.round((100 * inbox) / (inbox + spam))}%` : '—'}</b>
          </div>
          <div>
            <span>Вытащили из спама</span>
            <b>{number(spam)}</b>
          </div>
          <div>
            <span>Ящиков в прогреве</span>
            <b>{number(enabled.length)}</b>
          </div>
          <div>
            <span>Партнёров Nstbrowser</span>
            <b>{number(partners)}</b>
          </div>
        </div>
        {data && (
          <p className="helper">
            Письма прогрева уходят с {data.hours.start}:00 до {data.hours.end}:00 по {data.hours.zone}{' '}
            (часовой пояс кампаний){data.hours.open_now ? '' : ', сейчас ночь — прогрев ждёт утра'}. Каждый
            ящик начинает с {data.limits.start} писем в день и прибавляет по {data.limits.step}, пока не
            дойдёт до своей цели. Получатель находит письмо, достаёт из спама, читает и иногда отвечает.
          </p>
        )}
        {enabled.length === 1 && partners === 0 && (
          <p className="board-warning">
            Одному ящику писать некому: включите прогрев ещё на одном ящике или подключите партнёров из
            Nstbrowser.
          </p>
        )}
      </section>

      <section className="panel cloud-detail">
        <div className="board-heading">
          <h2>Ящики</h2>
        </div>
        {accounts.length === 0 ? (
          <p className="helper">
            Сначала добавьте Gmail в{' '}
            <button type="button" className="link-button" onClick={onAccounts}>
              «Ящики»
            </button>
            .
          </p>
        ) : (
          <div className="crm-table cloud-table">
            <table>
              <thead>
                <tr>
                  <th>Ящик</th>
                  <th>Прогрев</th>
                  <th>Цель в день</th>
                  <th>За 24 ч / сегодня</th>
                  <th>Входящие / спам, 7 дней</th>
                  <th>Следующее письмо</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map(account => (
                  <AccountRow key={account.id} account={account} onChange={state.refresh} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <NstPanel data={data} onChange={state.refresh} />

      {data && data.partners.length > 0 && (
        <section className="panel cloud-detail">
          <div className="board-heading">
            <h2>Партнёры</h2>
          </div>
          <div className="crm-table cloud-table">
            <table>
              <thead>
                <tr>
                  <th>Профиль</th>
                  <th>Gmail</th>
                  <th>Состояние</th>
                  <th>Писем за 7 дней</th>
                  <th>Последний раз</th>
                  <th>Участвует</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.partners.map(partner => (
                  <PartnerRow key={partner.id} partner={partner} onChange={state.refresh} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {data && data.recent.length > 0 && (
        <section className="panel cloud-detail">
          <div className="board-heading">
            <h2>Последние письма прогрева</h2>
            <span className="helper">ждут обработки: {number(data.pending)}</span>
          </div>
          <div className="crm-table cloud-table">
            <table>
              <thead>
                <tr>
                  <th>Когда</th>
                  <th>От</th>
                  <th>Кому</th>
                  <th>Шаг</th>
                  <th>Где оказалось</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map(event => (
                  <tr key={event.id}>
                    <td>{time(event.at)}</td>
                    <td>{event.from}</td>
                    <td>{event.to}</td>
                    <td>
                      {STEPS[event.depth] ?? 'Ответ'}
                      {event.answered && <small>ответили</small>}
                    </td>
                    <td>
                      {event.placement ? (
                        <StatusBadge
                          value={PLACEMENTS[event.placement][0]}
                          label={PLACEMENTS[event.placement][1]}
                        />
                      ) : event.status === 'pending' ? (
                        <StatusBadge value="queued" label="Ждёт получателя" />
                      ) : (
                        <StatusBadge value="failed" label="Ошибка" />
                      )}
                      {event.note && <small>{event.note}</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <ErrorToast message={state.error} />
    </div>
  );
}

function AccountRow({ account, onChange }: { account: WarmupAccount; onChange: () => void }) {
  const { call, error } = useCall(onChange);
  const total = account.inbox_7d + account.spam_7d;
  return (
    <tr>
      <td>
        {account.address}
        {account.status !== 'active' && <small className="board-warning">ящик не в работе</small>}
        {error && <small className="board-warning">{error}</small>}
      </td>
      <td>
        <input
          type="checkbox"
          aria-label="Прогрев"
          checked={account.pool_warmup}
          onChange={event =>
            void call('email.warmup_update', { id: account.id, pool_warmup: event.target.checked })
          }
        />
      </td>
      <td>
        <input
          type="number"
          min={1}
          max={40}
          defaultValue={account.warm_target}
          style={{ width: 70 }}
          onBlur={event => {
            const value = Number(event.target.value);
            if (value !== account.warm_target)
              void call('email.warmup_update', { id: account.id, warm_target: value });
          }}
        />
      </td>
      <td>{account.pool_warmup ? `${number(account.sent_24h)} / ${number(account.cap_today)}` : '—'}</td>
      <td>
        {total ? (
          <>
            {account.inbox_rate}% во «Входящих»
            <small>
              {number(account.inbox_7d)} / {number(account.spam_7d)}
              {account.missing_7d ? `, не дошло ${number(account.missing_7d)}` : ''}
            </small>
          </>
        ) : (
          '—'
        )}
      </td>
      <td>{account.pool_warmup ? time(account.next_at) : '—'}</td>
    </tr>
  );
}

function NstPanel({ data, onChange }: { data?: WarmupState; onChange: () => void }) {
  const { call, error, notice, setNotice } = useCall(onChange);
  const [key, setKey] = useState('');
  const [url, setUrl] = useState<string>();
  const [busy, setBusy] = useState('');
  const run = async (name: string, action: () => Promise<unknown>) => {
    setBusy(name);
    await action();
    setBusy('');
  };
  const detect = data?.detect;
  return (
    <section className="panel board cloud-form">
      <div className="board-heading">
        <h2>Партнёры из Nstbrowser</h2>
      </div>
      <p className="helper">
        Gmail-аккаунты, в которые уже выполнен вход в профилях Nstbrowser, становятся партнёрами: они получают
        письма прогрева, достают их из спама и отвечают. Рассылки с них не уходят. Nstbrowser должен быть
        открыт на этом Mac. API-ключ возьмите в приложении Nstbrowser (раздел API) — он хранится
        зашифрованным.
      </p>
      <div className="cloud-grid">
        <label className="cloud-field">
          <span>API-ключ{data?.nst.configured ? ' (сохранён, вставьте новый, чтобы заменить)' : ''}</span>
          <input
            type="password"
            autoComplete="off"
            value={key}
            placeholder={data?.nst.configured ? '••••••••' : 'Ключ из Nstbrowser'}
            onChange={event => setKey(event.target.value)}
          />
        </label>
        <label className="cloud-field">
          <span>Адрес API</span>
          <input value={url ?? data?.nst.url ?? ''} onChange={event => setUrl(event.target.value)} />
        </label>
      </div>
      {data?.nst.note && <p className="board-warning">{data.nst.note}</p>}
      {detect?.running && (
        <p className="helper">
          Открываю профили и ищу адреса Gmail: {number(detect.done)} из {number(detect.total)}…
        </p>
      )}
      {detect && !detect.running && detect.total > 0 && (
        <p className="helper">
          Адреса найдены в {number(detect.found)} профилях из {number(detect.total)}. Где не нашёл — проверьте
          вход в Gmail в профиле или впишите адрес вручную.
        </p>
      )}
      <ErrorToast message={error} />
      <div className="board-footer">
        {notice && <span className="helper">{notice}</span>}
        <div className="actions">
          <Button
            variant="outline"
            disabled={!!busy}
            onClick={() =>
              void run('save', async () => {
                const result = await call('email.warmup_nst_save', {
                  api_key: key,
                  url: url ?? data?.nst.url,
                });
                if (result) {
                  setKey('');
                  setNotice('Сохранено.');
                }
              })
            }
          >
            <Save size={14} /> Сохранить
          </Button>
          <Button
            variant="outline"
            disabled={!!busy || !data?.nst.configured}
            onClick={() =>
              void run('test', async () => {
                const result = await call<{ message: string }>('email.warmup_nst_test');
                if (result) setNotice(result.message);
              })
            }
          >
            <PlugZap size={14} /> {busy === 'test' ? 'Проверяю…' : 'Проверить связь'}
          </Button>
          <Button
            variant="outline"
            disabled={!!busy || !data?.nst.configured}
            onClick={() =>
              void run('load', async () => {
                const result = await call<{ added: number; total: number }>('email.warmup_partners_load');
                if (result) setNotice(`Профилей: ${result.total}, новых: ${result.added}.`);
              })
            }
          >
            <Download size={14} /> {busy === 'load' ? 'Загружаю…' : 'Загрузить профили'}
          </Button>
          <Button
            disabled={!!busy || !data?.nst.configured || !data?.partners.length || detect?.running}
            onClick={() => void call('email.warmup_detect')}
          >
            <ScanSearch size={14} /> Определить адреса
          </Button>
        </div>
      </div>
    </section>
  );
}

function PartnerRow({ partner, onChange }: { partner: WarmupPartner; onChange: () => void }) {
  const { call, error } = useCall(onChange);
  const [badge, label] = PARTNER_BADGES[partner.status];
  return (
    <tr>
      <td>
        {partner.profile_name || partner.profile_id}
        <small>{partner.profile_id}</small>
      </td>
      <td>
        <input
          type="email"
          defaultValue={partner.address}
          placeholder="name@gmail.com"
          style={{ minWidth: 260 }}
          onBlur={event => {
            const value = event.target.value.trim();
            if (value !== partner.address)
              void call('email.warmup_partner_update', { id: partner.id, address: value });
          }}
        />
        {error && <small className="board-warning">{error}</small>}
      </td>
      <td>
        <StatusBadge value={badge} label={label} />
        {partner.note && <small>{partner.note}</small>}
      </td>
      <td>{number(partner.handled_7d)}</td>
      <td>{relativeTime(partner.last_used_at, '—')}</td>
      <td>
        <input
          type="checkbox"
          aria-label="Участвует в прогреве"
          checked={partner.enabled}
          onChange={event =>
            void call('email.warmup_partner_update', { id: partner.id, enabled: event.target.checked })
          }
        />
      </td>
      <td className="email-actions">
        <Button
          variant="outline"
          icon
          aria-label="Убрать партнёра"
          onClick={() => void call('email.warmup_partner_remove', { id: partner.id })}
        >
          <Trash2 size={14} />
        </Button>
      </td>
    </tr>
  );
}
