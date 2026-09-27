import { Button } from './ui/button';
export function DataState({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  if (error)
    return (
      <div role="alert" className="error-banner">
        {error}{' '}
        <Button variant="outline" onClick={retry}>
          Повторить
        </Button>
      </div>
    );
  return loading ? (
    <div role="status" aria-label="Загрузка данных" className="skeleton">
      <div />
      <div />
      <div />
    </div>
  ) : null;
}
export function StatusBadge({ value, label }: { value: string; label: string }) {
  return <span className={`status-badge status-${value}`}>{label}</span>;
}
