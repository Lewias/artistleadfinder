/* eslint-disable-next-line @typescript-eslint/no-unused-expressions -- evaluated as a function by Playwright */
async (args) => {
  // Followers / following dialog of a source: page through the list with a delay between
  // pages, stop at the limit, the end of the list or the time budget. Never retries blocks.
  const url = new URL(location.href);
  const pageSize = Math.max(5, Math.min(50, Number(args?.pageSize) || 12));
  const delayMs = Math.max(1000, Math.min(10000, Number(args?.delayMs) || 2000));
  const max = Math.max(1, Math.min(1000, Number(args?.max) || 100));
  const deadline = Date.now() + 30000;
  const parts = url.pathname.split('/').filter(Boolean);
  if (!['instagram.com', 'www.instagram.com'].includes(url.hostname)
      || parts.length !== 2 || !['followers', 'following'].includes(parts[1])) {
    return { url: url.href, ready: false, blocked: false };
  }
  const limitedText = /try again later|too many requests|wait a few minutes|подождите несколько минут|повторите попытку позже/i;
  const gate = () => {
    const body = document.body?.innerText || '';
    if (limitedText.test(body)) return { blocked: true, rate_limited: true };
    if (/\/(accounts|challenge)\//.test(location.pathname) || document.querySelector('input[type="password"]')) {
      return { blocked: true, rate_limited: false };
    }
    return null;
  };
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  let dialog = null;
  for (let attempt = 0; attempt < 10 && !dialog; attempt++) {
    const stop = gate();
    if (stop) return { url: url.href, ready: false, ...stop };
    dialog = document.querySelector('[role="dialog"]');
    if (!dialog) await wait(500);
  }
  if (!dialog) return { url: url.href, ready: false, blocked: false };
  const users = [];
  const collect = () => {
    for (const anchor of dialog.querySelectorAll('a[href]')) {
      try {
        const link = new URL(anchor.href, url);
        const name = link.pathname.match(/^\/([a-zA-Z0-9_.]{1,30})\/?$/)?.[1]?.toLowerCase();
        if (['instagram.com', 'www.instagram.com'].includes(link.hostname) && name
            && name !== parts[0].toLowerCase() && !/^(explore|reels?|p|tv|stories|accounts|direct)$/.test(name)
            && !users.includes(name)) {
          users.push(name);
        }
      } catch { /* Ignore malformed links. */ }
      if (users.length >= max) return;
    }
  };
  const scroller = () => [...dialog.querySelectorAll('div')].find(
    element => element.scrollHeight > element.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(element).overflowY),
  );
  let stable = 0;
  let pages = 0;
  let limited = false;
  while (users.length < max && Date.now() < deadline) {
    const before = users.length;
    collect();
    const stop = gate();
    if (stop) return { url: url.href, ready: false, ...stop, users };
    if (users.length >= max) { limited = true; break; }
    stable = users.length === before ? stable + 1 : 0;
    if (stable >= 2) break;
    const box = scroller();
    if (!box) break;
    // One page is roughly `pageSize` rows of the list.
    const row = Math.max(40, box.scrollHeight / Math.max(1, users.length));
    box.scrollTop += row * pageSize;
    pages += 1;
    await wait(delayMs);
    if (location.pathname !== url.pathname) return { ready: false, blocked: false, url: location.href };
  }
  if (Date.now() >= deadline) limited = true;
  return { url: url.href, ready: true, blocked: false, users: users.slice(0, max), pages, limited };
}
