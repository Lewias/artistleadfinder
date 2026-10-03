import type { DiscoveryMetrics } from '../services/types';

const groupLabels: Record<string, string> = {
  profiles: 'Проверка профилей',
  posts: 'Posts / Reels',
  comments: 'Комментарии',
  tagged: 'Tagged',
  stories: 'Stories',
  followers: 'Followers',
  following: 'Following',
};
const columns: [keyof DiscoveryMetrics, string][] = [
  ['itemsSeen', 'Увидено'],
  ['itemsProcessed', 'Разобрано'],
  ['candidatesFound', 'Кандидатов'],
  ['duplicatesSkipped', 'Дублей'],
  ['alreadyProcessed', 'Уже было'],
  ['failures', 'Сбоев'],
];

/** Per-source, per-provider discovery counters of one Scout run. */
export function DiscoveryMetricsTable({
  providers,
}: {
  providers: Record<string, Record<string, DiscoveryMetrics>>;
}) {
  return (
    <div className="metrics-table">
      <table>
        <thead>
          <tr>
            <th>Источник</th>
            <th>Метод</th>
            {columns.map(([key, label]) => (
              <th key={key}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(providers).flatMap(([source, groups]) =>
            Object.entries(groups).map(([group, metrics], index) => (
              <tr key={`${source}-${group}`}>
                <td>{index === 0 ? `@${source}` : ''}</td>
                <td>{groupLabels[group] || group}</td>
                {columns.map(([key]) => (
                  <td key={key} className={key === 'failures' && metrics[key] ? 'bad' : ''}>
                    {metrics[key] ?? 0}
                  </td>
                ))}
              </tr>
            )),
          )}
        </tbody>
      </table>
    </div>
  );
}
