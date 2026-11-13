import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import '@fontsource-variable/inter';
import './theme.css';
import { api } from './services/api';
import './design/specimen.css';

async function mount() {
  document.documentElement.dataset.platform = /Mac/.test(navigator.userAgent) ? 'macos' : 'windows';
  if (import.meta.env.DEV && import.meta.env.VITE_DESIGN_FIXTURES === '1') {
    Object.assign(api, (await import('./design/fixtures')).fixtureApi);
    document.documentElement.dataset.fixture = 'true';
  }
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
void mount();
