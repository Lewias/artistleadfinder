/* eslint-disable-next-line @typescript-eslint/no-unused-expressions -- evaluated as a function by Playwright */
async (args) => {
  // Publication page (/p/, /reel/, /reels/, /tv/): author, collaborators and, when the
  // comments method is on, comment authors. All Instagram DOM assumptions live in SELECTORS.
  const SELECTORS = {
    structuredData: 'script[type="application/ld+json"]',
    headers: ['article header', 'main header', '[role="dialog"] header'],
    caption: 'article h1',
    time: 'article time[datetime]',
    commentPermalinks: 'a[href*="/c/"]',
    buttons: 'button,[role="button"]',
    password: 'input[type="password"]',
    dialog: '[role="dialog"]',
  };
  const TEXT = {
    rateLimited: /try again later|too many requests|wait a few minutes|подождите несколько минут|повторите попытку позже/i,
    checkpoint: /confirm (?:it[’']?s|that it[’']?s) you|подтвердите.*личность|suspicious (?:login|activity)/i,
    loginWall: /sign up to see|log in to see|зарегистрируйтесь, чтобы|смотрите фото, видео и другой контент|restricted profile|ограниченный профиль|not available (?:to|for) certain audiences|недоступен для определ[её]нных аудиторий/i,
    loginDialog: /log in|sign up|войти|зарегистрир/i,
    unavailable: /sorry, this page isn[’']?t available|эта страница недоступна|post isn[’']?t available|публикация недоступна/i,
    moreAuthors: /\b(?:and|и)\s+(?:\d+|ещё \d+)\s+(?:others?|друг\w*)/i,
    loadMore: /^(?:(?:load|view|show) (?:all |more |previous )?(?:comments|replies)|(?:показать|смотреть|загрузить) (?:все |ещ[её] |предыдущие )?(?:комментарии|ответы))/i,
  };
  const hosts = ['instagram.com', 'www.instagram.com'];
  const reserved = /^(accounts|explore|reels?|p|tv|direct|stories|challenge|about|developer|legal)$/i;
  const url = new URL(location.href);
  const parts = url.pathname.split('/').filter(Boolean);
  const media = ['p', 'reel', 'reels', 'tv'];
  if (!hosts.includes(url.hostname)
      || !((parts.length === 2 && media.includes(parts[0])) || (parts.length === 3 && media.includes(parts[1])))) {
    return { url: url.href, ready: false, blocked: false };
  }
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const bodyText = () => (document.body?.innerText || '').slice(0, 30000);
  const gate = () => {
    const body = bodyText();
    if (TEXT.rateLimited.test(body)) return 'rate_limited';
    if (/\/challenge\//.test(location.pathname) || TEXT.checkpoint.test(body)) return 'checkpoint';
    const dialog = document.querySelector(SELECTORS.dialog)?.innerText || '';
    if (/\/accounts\/login/.test(location.pathname) || document.querySelector(SELECTORS.password)
        || TEXT.loginWall.test(body) || TEXT.loginDialog.test(dialog)) return 'login';
    return null;
  };
  const blocked = reason => ({ url: url.href, ready: false, blocked: true, block_reason: reason, rate_limited: reason === 'rate_limited' });
  const profileName = anchor => {
    try {
      const link = new URL(anchor.getAttribute('href') || anchor.href, url);
      const name = link.pathname.match(/^\/([a-zA-Z0-9_.]{1,30})\/?$/)?.[1];
      return hosts.includes(link.hostname) && name && !reserved.test(name) ? name.toLowerCase() : null;
    } catch { return null; }
  };
  const meta = key => document.querySelector(`meta[property="${key}"]`)?.content || document.querySelector(`meta[name="${key}"]`)?.content || '';
  const header = () => SELECTORS.headers.map(selector => document.querySelector(selector)).find(Boolean) || null;

  // Author strategy chain: structured data -> page metadata -> post header links -> fail.
  const authorFromStructuredData = () => {
    for (const script of document.querySelectorAll(SELECTORS.structuredData)) {
      try {
        const items = [JSON.parse(script.textContent || 'null')].flat(3);
        for (const item of items) {
          for (const author of [item?.author].flat()) {
            const value = author?.alternateName || author?.identifier?.value || author?.url || '';
            const name = String(value).replace(/^@/, '').match(/(?:instagram\.com\/)?([a-zA-Z0-9_.]{1,30})\/?$/)?.[1];
            if (name && !reserved.test(name)) return name.toLowerCase();
          }
        }
      } catch { /* Malformed structured data is ignored. */ }
    }
    return null;
  };
  const authorFromMetadata = () => {
    const description = (meta('og:description') || meta('description')).trim();
    // "12 likes, 3 comments - artist on September 25, 2026: ..." or "artist on Instagram: ..."
    const name = description.match(/(?:^| - )([a-zA-Z0-9_.]{1,30}) (?:on [A-Z][a-z]+ \d{1,2}, \d{4}|on Instagram|[A-Z][a-z]+ \d{1,2}, \d{4})/)?.[1];
    return name && !reserved.test(name) ? name.toLowerCase() : null;
  };
  const authorFromHeader = () => {
    const block = header();
    if (!block) return null;
    for (const anchor of block.querySelectorAll('a[href]')) {
      const name = profileName(anchor);
      if (name) return name;
    }
    return null;
  };
  const strategies = [
    ['structured_data', authorFromStructuredData],
    ['metadata', authorFromMetadata],
    ['post_header', authorFromHeader],
  ];

  let author = null;
  let authorStrategy = null;
  // The header can render after the first paint; wait briefly instead of guessing.
  for (let attempt = 0; attempt < 12 && !author; attempt++) {
    const stop = gate();
    if (stop) return blocked(stop);
    if (TEXT.unavailable.test(bodyText())) return { url: url.href, ready: true, blocked: false, unavailable: true };
    for (const [name, strategy] of strategies) {
      author = strategy();
      if (author) { authorStrategy = name; break; }
    }
    if (!author) await wait(500);
  }

  // Collaborators: only profile links inside the post header (the authorship block).
  const collaborators = [];
  const block = header();
  if (block) {
    for (const anchor of [...block.querySelectorAll('a[href]')].slice(0, 20)) {
      const name = profileName(anchor);
      if (name && !collaborators.includes(name)) collaborators.push(name);
      if (collaborators.length >= 6) break;
    }
  }
  const collaboratorsTruncated = TEXT.moreAuthors.test(block?.innerText || '');
  const description = (meta('og:description') || meta('description')).trim();
  const quoted = description.match(/:\s*["“]([\s\S]*)["”]\s*\.?$/)?.[1];
  // Caption metadata identifies the publication, never nominates candidates.
  const caption = document.querySelector(SELECTORS.caption)?.innerText || quoted || '';
  const published_at = meta('article:published_time') || document.querySelector(SELECTORS.time)?.getAttribute('datetime') || null;

  const comments = new Map();
  let limited = false;
  if (args?.comments !== false && author) {
    const code = parts[parts.length - 1];
    const clicked = new WeakSet();
    let stable = 0;
    // Duplicates are filtered by the core, so read past its 30-new-candidate quota.
    const full = () => comments.size >= 200 || new Set([...comments.values()].map(c => c.profile_url)).size >= 100;
    for (let round = 0; round < 14; round++) {
      const before = comments.size;
      let lastRow = null;
      // A comment permalink distinguishes its author from caption tags and recommendations.
      for (const permalink of document.querySelectorAll(SELECTORS.commentPermalinks)) {
        let link;
        try { link = new URL(permalink.href, url); } catch { continue; }
        const match = link.pathname.match(/^\/(?:[\w.]+\/)?(?:p|reel|reels|tv)\/([\w-]+)\/c\/(\d+)\/?$/);
        if (!match || match[1] !== code || !hosts.includes(link.hostname)) continue;
        let row = permalink.parentElement;
        let authorLink = null;
        for (let depth = 0; row && depth < 7; depth++, row = row.parentElement) {
          authorLink = [...row.querySelectorAll('a[href]')].find(a => profileName(a));
          if (authorLink) break;
        }
        if (!row || !authorLink) continue;
        const commenter = profileName(authorLink);
        if (commenter === author) continue;
        // Current Instagram uses an author/time row followed by a sibling text block.
        const rowHeader = row;
        if (row.parentElement && row.parentElement.querySelectorAll(SELECTORS.commentPermalinks).length === 1) row = row.parentElement;
        const copy = row.cloneNode(true);
        for (const control of copy.querySelectorAll('button,[role="button"],time,svg')) control.remove();
        let text = (copy.innerText || copy.textContent || '').trim();
        const name = authorLink.innerText?.trim() || '';
        if (name && text.startsWith(name)) text = text.slice(name.length).trim();
        const time = rowHeader.querySelector('time[datetime]');
        comments.set(match[2], { profile_url: `https://www.instagram.com/${commenter}/`, text: text.slice(0, 1500),
          published_at: time?.getAttribute('datetime') || null });
        lastRow = row;
        if (full()) break;
      }
      if (full()) { limited = true; break; }
      const more = [...document.querySelectorAll(SELECTORS.buttons)].find(button => {
        const label = (button.innerText || button.getAttribute('aria-label') || '').trim();
        return !clicked.has(button) && TEXT.loadMore.test(label);
      });
      if (more) { clicked.add(more); more.click(); limited = true; }
      if (lastRow) {
        lastRow.scrollIntoView({ block: 'end' });
        let scroller = lastRow.parentElement;
        while (scroller && scroller !== document.body) {
          if (scroller.scrollHeight > scroller.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(scroller).overflowY)) {
            scroller.scrollTop = scroller.scrollHeight;
            break;
          }
          scroller = scroller.parentElement;
        }
      }
      stable = comments.size === before && !more ? stable + 1 : 0;
      if (stable >= 2) break;
      if (round === 13) { limited = true; break; }
      await wait(750);
      if (location.href !== url.href) return { ready: false };
      const stop = gate();
      if (stop) return blocked(stop);
    }
  }
  const result = {
    url: url.href,
    // Loaded even when the author is unknown: the core logs the failure instead of guessing.
    ready: document.readyState === 'complete',
    blocked: false,
    caption: caption.slice(0, 12000),
    author,
    author_strategy: authorStrategy,
    collaborators,
    collaborators_truncated: collaboratorsTruncated,
    published_at,
    comments: [...comments.values()],
    comments_limited: limited,
  };
  if (!author) {
    result.parse_error = 'author_not_found';
    if (args?.debug) {
      // Sanitised fragment: structure and links only, no scripts, inputs or attribute noise.
      const root = (header() || document.querySelector('article') || document.querySelector('main'))?.cloneNode(true);
      for (const node of root?.querySelectorAll?.('script,style,input,textarea,svg,img,video') || []) node.remove();
      for (const node of root?.querySelectorAll?.('*') || []) {
        for (const attribute of [...node.attributes]) {
          if (!['href', 'role', 'aria-label', 'datetime'].includes(attribute.name)) node.removeAttribute(attribute.name);
        }
      }
      result.debug = { reason: 'author_not_found', html: (root?.outerHTML || '').slice(0, 20000) };
    }
  }
  return result;
}
