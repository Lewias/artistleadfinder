import type { ScoutAccountRow } from '../services/types';

export function runStatus(row: ScoutAccountRow): string {
  const run = row.run;
  if (row.found >= row.target) return 'Цель достигнута';
  if (!run) return 'Не запускался';
  if (run.stage === 'interrupted') return 'Прерван при закрытии приложения';
  if (run.status === 'running') return run.wait_seconds ? 'Ждёт по темпу' : 'Идёт поиск';
  if (run.status === 'paused') return run.error ? 'Требуется внимание' : 'Остановлен';
  if (run.status === 'completed') return 'Завершён';
  if (run.status === 'cancelled') return 'Отменён';
  return run.status;
}
