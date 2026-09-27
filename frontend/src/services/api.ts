import { invoke } from '@tauri-apps/api/core';

export interface ApplicationService {
  request<T>(method: string, params?: object): Promise<T>;
  browser<T>(action: BrowserAction, params?: object): Promise<T>;
  openProfile(address: string): Promise<void>;
  openLogs(): Promise<void>;
}
export type BrowserAction =
  | 'list'
  | 'create'
  | 'update'
  | 'import'
  | 'import_cookies'
  | 'open'
  | 'save'
  | 'delete'
  | 'capture'
  | 'queue'
  | 'scout'
  | 'search'
  | 'search_cancel';
export const api: ApplicationService = {
  request: <T>(method: string, params: object = {}) => invoke<T>('core_request', { method, params }),
  browser: <T>(action: BrowserAction, params: object = {}) => invoke<T>('browser_action', { action, params }),
  openProfile: (address: string) => invoke('open_profile', { address }),
  openLogs: () => invoke('open_logs'),
};
