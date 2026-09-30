import { api } from '../services/api';
import type { ScoutAccountRow } from '../services/types';

export const errorText = (err: unknown) => String(err).replace(/^Error:\s*/, '');

/** A Scout run that holds the account (running, or paused waiting for the user). */
export const scoutActive = (row?: ScoutAccountRow | null) =>
  Boolean(row?.run && ['running', 'paused'].includes(row.run.status) && row.run.stage !== 'interrupted');

/** Opens the account's window and queues its next Scout run; a reached goal starts over. */
export async function startScout(row: ScoutAccountRow) {
  const id = row.profile.id;
  if (row.found >= row.target) {
    await api.request('scout.account_target', { profile_id: id, target: row.target, reset: true });
  }
  await api.browser('open', { id });
  // The core picks the next sources by rotation and cooldown.
  await api.browser('scout', { id });
}

export async function controlScout(row: ScoutAccountRow, action: 'pause' | 'resume' | 'cancel') {
  if (action === 'resume') await api.browser('open', { id: row.profile.id });
  await api.request('jobs.control', { id: row.run!.id, action });
}

/** Board status of the accounts' Scout runs: «Dmitrii 37/100, Dima2 — пауза». */
export function scoutRunsLabel(rows: ScoutAccountRow[]): string {
  return rows
    .map(row => {
      const state = row.run?.status === 'paused' ? (row.run.error ? ' — нужна проверка' : ' — пауза') : '';
      return `${row.profile.name} ${row.found}/${row.target}${state}`;
    })
    .join(', ');
}
