/* eslint-disable-next-line @typescript-eslint/no-unused-expressions -- evaluated as a function by Playwright */
async (args) => {
  // Source grid (/<source>/) or tagged grid (/<source>/tagged/): collect publication links,
  // scrolling lazily only until enough unprocessed ones are found. Never scrolls forever.
  const SELECTORS = {
    gridLinks: 'main a[href]',
    title: 'meta[property="og:title"]',
    password: 'input[type="password"]',
  };
  const TEXT = {
    rateLimited: /try again later|too many requests|wait a few minutes|подождите несколько минут|повторите попытку позже/i,
    checkpoint: /confirm (?:it[’']?s|that it[’']?s) you|подтвердите.*личность|suspicious (?:login|activity)/i,
    loginWall: /log in to see|sign up to see|зарегистрируйтесь, чтобы|войдите, чтобы|restricted profile|ограниченный профиль|not available (?:to|for) certain audiences|недоступен для определ[её]нных аудиторий/i,
    unavailable: /sorry, this page isn[’']?t available|эта страница недоступна|page not found|страница не найдена/i,
    emptyGrid: /no posts yet|нет публикаций|no photos|нет фото|when people tag|когда люди отмечают|this account is private|это закрытый аккаунт/i,
  };
  // Missing or invalid numbers fall back to defaults; every loop bound stays finite.
  const num = (value, fallback, low, high) => {
    const parsed = Number(value);
    return Math.max(low, Math.min(high, Number.isFinite(parsed) ? parsed : fallback));
  };
  const maxPosts = num(args?.maxPosts, 12, 1, 200);
  const maxRounds = num(args?.maxScrollRounds, 15, 0, 40);
  const delayMs = num(args?.scrollDelayMs, 1200, 300, 5000);
  const maxNoProgress = num(args?.maxNoProgressRounds, 2, 1, 10);
  const skip = new Set((args?.skip || []).map(String));
  const deadline = Date.now() + 25000;
  const url = new URL(location.href);
  const parts = url.pathname.split('/').filter(Boolean);
  const hosts = ['instagram.com', 'www.instagram.com'];
  if (!hosts.includes(url.hostname) || !(parts.length === 1 || (parts.length === 2 && parts[1] === 'tagged'))) {
    return { url: url.href, ready: false, blocked: false };
  }
  const gate = () => {
    const body = (document.body?.innerText || '').slice(0, 30000);
    if (TEXT.rateLimited.test(body)) return 'rate_limited';
    if (/\/challenge\//.test(location.pathname) || TEXT.checkpoint.test(body)) return 'checkpoint';
    if (/\/accounts\/login/.test(location.pathname) || document.querySelector(SELECTORS.password) || TEXT.loginWall.test(body)) {
      return 'login';
    }
    return null;
  };
  const blockedResult = reason => ({ url: url.href, ready: false, blocked: true, block_reason: reason, rate_limited: reason === 'rate_limited' });
  const early = gate();
  if (early) return blockedResult(early);
  const body = () => (document.body?.innerText || '').slice(0, 30000);
  if (TEXT.unavailable.test(body())) return { url: url.href, ready: true, blocked: false, unavailable: true, posts: [] };
  const title = document.querySelector(SELECTORS.title)?.content || document.title;
  const username = title.match(/@([a-zA-Z0-9_.]{1,30})/)?.[1]?.toLowerCase();

  // Post URL detection: /p/, /reel/, /reels/, /tv/ with or without an author prefix.
  const postPath = /^\/(?:[\w.]+\/)?(p|reel|reels|tv)\/([\w-]+)\/?$/;
  const all = new Map();
  const collect = () => {
    for (const anchor of [...document.querySelectorAll(SELECTORS.gridLinks)].slice(0, 3000)) {
      try {
        const link = new URL(anchor.href, url);
        const match = link.pathname.match(postPath);
        if (!hosts.includes(link.hostname) || !match || all.has(match[2])) continue;
        const kind = match[1] === 'reels' ? 'reel' : match[1];
        const author = link.pathname.match(/^\/([\w.]+)\/(?:p|reel|reels|tv)\//)?.[1];
        all.set(match[2], `https://www.instagram.com/${author ? `${author}/` : ''}${kind}/${match[2]}/`);
      } catch { /* Ignore malformed links. */ }
    }
  };
  const fresh = () => [...all.entries()].filter(([code]) => !skip.has(code));
  let rounds = 0;
  let noProgress = 0;
  let endReason = 'enough';
  collect();
  while (fresh().length < maxPosts) {
    if (rounds >= maxRounds) { endReason = 'max_scroll_rounds'; break; }
    if (Date.now() > deadline) { endReason = 'time_budget'; break; }
    const before = all.size;
    globalThis.scrollTo?.(0, document.body?.scrollHeight || 0);
    rounds += 1;
    await new Promise(resolve => setTimeout(resolve, delayMs));
    if (location.href !== url.href) return { url: location.href, ready: false, blocked: false };
    const stop = gate();
    if (stop) return blockedResult(stop);
    collect();
    noProgress = all.size === before ? noProgress + 1 : 0;
    if (noProgress >= maxNoProgress) { endReason = 'end_of_grid'; break; }
  }
  const posts = fresh().slice(0, maxPosts).map(([, href]) => href);
  const empty = TEXT.emptyGrid.test(body());
  const ready = document.readyState === 'complete' && username === parts[0].toLowerCase() && (all.size > 0 || empty);
  const result = {
    url: url.href, ready, blocked: false, posts,
    seen: all.size, already_processed: all.size - fresh().length, rounds, end_reason: endReason,
  };
  if (args?.debug && !ready) {
    result.debug = { reason: username !== parts[0].toLowerCase() ? 'title_username_mismatch' : 'no_grid_links', title };
  }
  return result;
}
