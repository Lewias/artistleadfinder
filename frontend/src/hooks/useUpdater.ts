import { errorText } from '../lib/errors';
import { useCallback, useEffect, useRef, useState } from 'react';
import { check, type DownloadEvent, type Update } from '@tauri-apps/plugin-updater';
import { relaunch } from '@tauri-apps/plugin-process';

export type UpdaterStatus =
  'idle' | 'checking' | 'none' | 'available' | 'downloading' | 'installing' | 'error';

export interface UpdaterState {
  status: UpdaterStatus;
  version?: string;
  notes?: string;
  /** 0..100 while downloading, when the size is known. */
  percent?: number;
  error?: string;
}

const RECHECK_MS = 6 * 60 * 60 * 1000;

/** Download progress from the plugin's events. */
export function trackDownload() {
  let total = 0;
  let received = 0;
  return (event: DownloadEvent): number | undefined => {
    if (event.event === 'Started') total = event.data.contentLength ?? 0;
    if (event.event === 'Progress') received += event.data.chunkLength;
    if (event.event === 'Finished') return 100;
    return total ? Math.min(99, Math.floor((received / total) * 100)) : undefined;
  };
}

const message = errorText;

/** Checks the release feed on start and every 6 hours; installs on request and restarts. */
export function useUpdater(checkFn = check, restart = relaunch) {
  const [state, setState] = useState<UpdaterState>({ status: 'idle' });
  const update = useRef<Update | null>(null);
  const busy = useRef(false);

  const run = useCallback(
    async (manual: boolean) => {
      if (busy.current) return;
      busy.current = true;
      setState(current => ({ ...current, status: 'checking', error: undefined }));
      try {
        update.current = await checkFn();
        setState(
          update.current
            ? { status: 'available', version: update.current.version, notes: update.current.body }
            : { status: 'none' },
        );
      } catch (err) {
        // Background checks fail quietly (offline, no release yet); a manual check reports it.
        setState(manual ? { status: 'error', error: message(err) } : { status: 'idle' });
      } finally {
        busy.current = false;
      }
    },
    [checkFn],
  );

  const install = useCallback(async () => {
    const pending = update.current;
    if (!pending || busy.current) return;
    busy.current = true;
    const progress = trackDownload();
    setState(current => ({ ...current, status: 'downloading', percent: 0 }));
    try {
      await pending.downloadAndInstall(event => {
        const percent = progress(event);
        setState(current => ({
          ...current,
          status: event.event === 'Finished' ? 'installing' : 'downloading',
          percent: percent ?? current.percent,
        }));
      });
      await restart();
    } catch (err) {
      setState(current => ({ ...current, status: 'error', error: message(err) }));
    } finally {
      busy.current = false;
    }
  }, [restart]);

  useEffect(() => {
    void run(false);
    const timer = setInterval(() => void run(false), RECHECK_MS);
    return () => clearInterval(timer);
  }, [run]);

  return { state, check: () => run(true), install };
}
