import { invoke } from '@tauri-apps/api/core';
import type { BackgroundSettings } from './types';

export interface ApplicationService {
  request<T>(method: string, params?: object): Promise<T>;
  browser<T>(action: BrowserAction, params?: object): Promise<T>;
  openProfile(address: string): Promise<void>;
  openLogs(): Promise<void>;
  /** Sending paused from the tray: reads it, or pauses and resumes. */
  automation(paused?: boolean): Promise<boolean>;
  /** «Фоновая работа»: reads the settings, or changes the given ones. */
  background(values?: Partial<BackgroundSettings>): Promise<BackgroundSettings>;
}
export type BrowserAction =
  | 'list'
  | 'create'
  | 'update'
  | 'import'
  | 'import_cookies'
  | 'external_list'
  | 'external_add'
  | 'open'
  | 'threads_open'
  | 'save'
  | 'delete'
  | 'capture'
  | 'queue'
  | 'scout';
export const api: ApplicationService = {
  request: <T>(method: string, params: object = {}) => invoke<T>('core_request', { method, params }),
  browser: <T>(action: BrowserAction, params: object = {}) => invoke<T>('browser_action', { action, params }),
  openProfile: (address: string) => invoke('open_profile', { address }),
  openLogs: () => invoke('open_logs'),
  automation: (paused?: boolean) => invoke<boolean>('automation', paused === undefined ? {} : { paused }),
  background: (values?: Partial<BackgroundSettings>) =>
    invoke<BackgroundSettings>('background_settings', values ? { values } : {}),
};
