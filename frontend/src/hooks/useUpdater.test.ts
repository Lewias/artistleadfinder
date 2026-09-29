import { describe, expect, test } from 'vitest';
import { trackDownload } from './useUpdater';

describe('updater download progress', () => {
  test('counts chunks against the announced size and ends at 100', () => {
    const progress = trackDownload();
    expect(progress({ event: 'Started', data: { contentLength: 1000 } })).toBe(0);
    expect(progress({ event: 'Progress', data: { chunkLength: 250 } })).toBe(25);
    expect(progress({ event: 'Progress', data: { chunkLength: 750 } })).toBe(99);
    expect(progress({ event: 'Finished' })).toBe(100);
  });

  test('has no percentage when the size is unknown', () => {
    const progress = trackDownload();
    progress({ event: 'Started', data: {} });
    expect(progress({ event: 'Progress', data: { chunkLength: 10 } })).toBeUndefined();
  });
});
