/** Development-only specimen transport. Never imported by a production build. */
import type { ApplicationService } from '../services/api';
import type { Lead, LeadQuery } from '../services/types';
import { defaultQuery } from '../services/leadQuery';

const names = [
  'Amara Sol & The Midnight Orchestra — Live Sessions',
  'Северное сияние / Александра Миронова',
  'Theo Mercer',
  'Nia Blue',
  'Saint August',
  'Milo Park',
  'June & The Satellites',
  'Без имени',
];
let leads: Lead[] = Array.from({ length: 1606 }, (_, index) => {
  const id = index + 1;
  const genre = ['R&B', 'Indie', 'Hip-Hop', 'Soul', 'Trap'][index % 5];
  return {
    id,
    username:
      [
        'amara.sol',
        'severnoe.music',
        'theomercer',
        'nia.blue',
        'saintaugust',
        'milopark',
        'june.satellites',
        'unsigned.artist',
      ][index % 8] + (index < 8 ? '' : `.${id}`),
    display_name: names[index % names.length],
    platform: 'instagram',
    bio: 'Independent artist · New EP “After the quiet” out now. Songs about late nights and finding home. Booking and collaborations welcome.',
    profile_url: 'https://www.instagram.com/example/',
    avatar_url: '',
    external_url: '',
    followers: index % 7 === 0 ? 0 : 1280 + ((index * 7919) % 48000),
    following: 321,
    unknown_fields: index % 7 === 0 ? ['followers'] : [],
    is_private: false,
    is_verified: false,
    artist_probability: 0.94,
    primary_genre: genre,
    genres: [genre],
    lead_score: 96 - (index % 61),
    status: ['new', 'qualified', 'reviewed', 'contacted', 'rejected'][index % 5],
    do_not_contact: index % 13 === 4,
    last_activity_at: null,
    created_at: '2026-11-13T08:20:00Z',
    sources: [
      {
        id,
        source_provider: 'instagram_scout',
        source_type: 'post',
        source_value: '@independentmusic',
        search_job_id: 1,
      },
    ],
    scout_profile: {
      profile_type: 'artist',
      confidence: 94,
      emails:
        index % 3 === 0
          ? []
          : [`${index % 4 === 0 ? 'management.and.international.bookings' : 'hello'}@artist-${id}.example`],
      phones: [],
      source_username: 'independentmusic',
      discovery_method: 'post',
      last_seen_at: null,
    },
  };
});

export function queryFixture(query: Partial<LeadQuery> = {}) {
  const q = { ...defaultQuery, ...query };
  const filtered = leads.filter(
    l =>
      (!q.search ||
        `${l.username} ${l.display_name} ${l.bio}`.toLowerCase().includes(q.search.toLowerCase())) &&
      (!q.status || l.status === q.status) &&
      (!q.genre || l.primary_genre === q.genre) &&
      (!q.source || l.sources.some(s => s.source_provider === q.source)) &&
      l.followers >= q.min_followers &&
      l.followers <= q.max_followers &&
      l.lead_score >= q.minimum_score,
  );
  const key = q.sort === 'followers' ? 'followers' : q.sort === 'score' ? 'lead_score' : 'id';
  filtered.sort((a, b) => (a[key] - b[key]) * (q.descending ? -1 : 1));
  return { total: filtered.length, items: filtered.slice((q.page - 1) * q.page_size, q.page * q.page_size) };
}

export const fixtureApi: ApplicationService = {
  async request<T>(method: string, params: object = {}): Promise<T> {
    await new Promise(resolve => setTimeout(resolve, 90));
    const p = params as Record<string, unknown>;
    let result: unknown;
    if (method === 'account.state') result = { configured: false, ready: true };
    else if (method === 'system.info') result = { version: '0.11.0 · specimen' };
    else if (method === 'leads.list') {
      if (p.search === '!offline')
        throw new Error('Не удалось обновить базу артистов. Проверьте соединение и повторите запрос.');
      result = queryFixture(params);
    } else if (method === 'leads.detail') {
      const lead = leads.find(l => l.id === p.id);
      if (!lead) throw new Error('Профиль не найден');
      result = {
        ...lead,
        analysis: { signals: ['Независимый артист', 'Музыкальный релиз в биографии'], confidence: 0.94 },
        classification: {
          category: 'artist',
          confidence: 94,
          decided_by: 'local',
          reasons: ['В биографии указан музыкальный релиз', 'Профиль содержит признаки независимого артиста'],
          ai_model: null,
        },
        breakdown: [
          { rule: 'artist', reason: 'Признаки независимого артиста', points: 30 },
          { rule: 'music_bio', reason: 'Музыкальный релиз в биографии', points: 20 },
        ],
        found_via: [],
      };
    } else if (method === 'leads.status') {
      leads = leads.map(l => (l.id === p.id ? { ...l, status: String(p.status) } : l));
      result = {};
    } else if (method === 'leads.delete') {
      leads = leads.filter(l => !(p.ids as number[]).includes(l.id));
      result = {};
    } else if (method === 'dashboard.get')
      result = {
        total: leads.length,
        qualified: leads.filter(l => l.status === 'qualified').length,
        today: 28,
        active: 0,
        genres: [],
        distribution: [],
        jobs: [],
        leads: [],
      };
    else if (method === 'leads.export')
      result = {
        count: p.ids ? (p.ids as number[]).length : queryFixture(p.query as Partial<LeadQuery>).total,
      };
    else if (['scout.sources', 'scout.accounts', 'jobs.list', 'imessage.templates'].includes(method))
      result = [];
    else if (method === 'imessage.state') result = { workspace: { recipients: [] } };
    else if (method === 'crm.list') result = { counts: { all: 0 }, items: [] };
    else throw new Error(`Fixture не поддерживает ${method}; реальный сервер не вызывается.`);
    return result as T;
  },
  async browser() {
    throw new Error('Браузерные операции отключены в specimen');
  },
  async openProfile() {
    throw new Error('Внешние переходы отключены в specimen');
  },
  async openLogs() {
    throw new Error('Журналы пользователя недоступны в specimen');
  },
};
