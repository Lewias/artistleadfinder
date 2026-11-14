import { ScoutDiscovery, type WorkTab } from '../components/ScoutDiscovery';
import { BrowserDiscovery } from '../components/BrowserDiscovery';

export function Discovery({ tab, onTab }: { tab: WorkTab; onTab: (tab: WorkTab) => void }) {
  return <ScoutDiscovery tab={tab} onTab={onTab} extra={<ExtraTools />} />;
}

function ExtraTools() {
  return (
    <details className="extra-tools">
      <summary>Дополнительно: ручной сбор ссылок</summary>
      <BrowserDiscovery />
    </details>
  );
}
