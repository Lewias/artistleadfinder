import type { ReactNode } from 'react';
import { pages, type PageId } from '../navigation';

/** Eyebrow, large title with a muted count, optional description and right-aligned actions. */
export function PageHeader({
  page,
  count,
  actions,
  children,
}: {
  page: PageId;
  count?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  const meta = pages.find(item => item.id === page)!;
  return (
    <header className="page-header">
      <div className="page-heading">
        <span className="eyebrow">{meta.eyebrow}</span>
        <h1>
          {meta.title}
          {count !== undefined && <span className="page-count">{count}</span>}
        </h1>
        {meta.description && <p className="page-description">{meta.description}</p>}
        {children}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
