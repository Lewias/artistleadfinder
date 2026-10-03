import type { ReactNode } from 'react';

/** Page reveal: a new `sectionKey` remounts the inner container, which replays the CSS
 * animation. Only the switched page remounts; the sidebar and topbar stay mounted. */
export function SectionTransition({ sectionKey, children }: { sectionKey: string; children: ReactNode }) {
  return (
    <div className="section-transition-wrapper">
      <div key={sectionKey} className="section-transition is-entering">
        {children}
      </div>
    </div>
  );
}
