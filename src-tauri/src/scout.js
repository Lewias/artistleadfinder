(async () => {
  const url = new URL(location.href);
  if (!['instagram.com', 'www.instagram.com'].includes(url.hostname)) return { ready: false };
  const body = (document.body?.innerText || '').slice(0, 30000);
  const dialog = document.querySelector('[role="dialog"]')?.innerText || '';
  // Rate limits need a break; other gates need the user (login, challenge).
  const rateLimited = /try again later|too many requests|wait a few minutes|подождите несколько минут|повторите попытку позже/i.test(body);
  const blocked = rateLimited
    || /\/(accounts|challenge)\//.test(url.pathname)
    || Boolean(document.querySelector('input[type="password"]'))
    || /log in|sign up|войти|зарегистрир/i.test(dialog)
    || /зарегистрируйтесь, чтобы|sign up to see|log in to see|смотрите фото, видео и другой контент|подтвердите.*личность/i.test(body)
    // Audience-restricted accounts render a login stub without the publication grid.
    || /ограниченный профиль|restricted profile|недоступен для определ[её]нных аудиторий|not available (to|for) certain audiences/i.test(body);
  if (blocked) return { url: url.href, ready: false, blocked: true, rate_limited: rateLimited };
  const meta = key => document.querySelector(`meta[property="${key}"]`)?.content || '';
  const parts = url.pathname.split('/').filter(Boolean);
  if (parts.length === 1 || (parts.length === 2 && parts[1] === 'tagged')) {
    const username = (meta('og:title') || document.title).match(/@([a-zA-Z0-9_.]{1,30})/)?.[1]?.toLowerCase();
    const posts = [];
    const collect = () => {
      // Instagram keeps only part of a long grid in the DOM, so links accumulate across scrolls.
      for (const anchor of [...document.querySelectorAll('main a[href]')].slice(0, 2000)) {
        try {
          const link = new URL(anchor.href);
          if (['instagram.com', 'www.instagram.com'].includes(link.hostname) && /^\/(?:[\w.]+\/)?(?:p|reel|reels|tv)\/[\w-]+\/?$/.test(link.pathname)) {
            const canonical = `https://www.instagram.com${link.pathname.replace(/\/$/, '')}/`;
            if (!posts.includes(canonical)) posts.push(canonical);
          }
        } catch { /* Ignore malformed links. */ }
        if (posts.length >= 120) break;
      }
    };
    // Scroll to older publications so account goals can be reached; the core queues them in batches.
    let stable = 0;
    for (let round = 0; round < 15 && posts.length < 120 && stable < 2; round++) {
      const before = posts.length;
      collect();
      stable = posts.length === before ? stable + 1 : 0;
      if (posts.length >= 120 || stable >= 2) break;
      globalThis.scrollTo?.(0, document.body?.scrollHeight || 0);
      await new Promise(resolve => setTimeout(resolve, 1200));
      if (location.href !== url.href) return { ready: false };
    }
    return { url: url.href, blocked: false, posts, ready: document.readyState === 'complete'
      && username === parts[0].toLowerCase() && (posts.length > 0 || /private|закрыт|no posts yet|нет публикаций|no photos|нет фото|when people tag|когда люди отмечают/i.test(body)) };
  }
  const media = ['p', 'reel', 'reels', 'tv'];
  if ((parts.length === 2 && media.includes(parts[0]))
      || (parts.length === 3 && media.includes(parts[1]))) {
    const description = (meta('og:description') || document.querySelector('meta[name="description"]')?.content || '').trim();
    // Caption metadata identifies the publication, never nominates candidates.
    const quoted = description.match(/:\s*["“]([\s\S]*)["”]\s*\.?$/)?.[1];
    const caption = document.querySelector('article h1')?.innerText || quoted || '';
    // Instagram omits the likes/comments prefix on some posts and reels.
    let author = description.match(/(?:^| - )([a-zA-Z0-9_.]{1,30}) (?:[A-Z][a-z]+ \d{1,2}, \d{4}|on )/)?.[1] || '';
    if (!author) {
      const authorLink = document.querySelector('article header a[href]');
      try {
        const link = new URL(authorLink?.href || '');
        if (['instagram.com', 'www.instagram.com'].includes(link.hostname) && /^\/[\w.]+\/?$/.test(link.pathname)) author = link.pathname.replaceAll('/', '');
      } catch { /* An unknown author is explicitly rejected by the backend. */ }
    }
    // Collaborative posts list every co-author as a profile link in the post header.
    const collaborators = [];
    for (const anchor of [...document.querySelectorAll('article header a[href]')].slice(0, 20)) {
      try {
        const link = new URL(anchor.href, url);
        const name = link.pathname.match(/^\/([a-zA-Z0-9_.]{1,30})\/?$/)?.[1];
        if (['instagram.com', 'www.instagram.com'].includes(link.hostname) && name
            && !/^(explore|reels?|p|tv|stories|accounts|direct)$/i.test(name) && !collaborators.includes(name.toLowerCase())) {
          collaborators.push(name.toLowerCase());
        }
      } catch { /* Ignore non-profile links. */ }
      if (collaborators.length >= 6) break;
    }
    const published_at = meta('article:published_time') || document.querySelector('article time[datetime]')?.getAttribute('datetime') || null;
    const comments = new Map();
    const code = parts[parts.length - 1];
    const profileLink = anchor => {
      try {
        const link = new URL(anchor.href, url);
        const name = link.pathname.match(/^\/([a-zA-Z0-9_.]{1,30})\/?$/)?.[1];
        if (['instagram.com', 'www.instagram.com'].includes(link.hostname) && name
            && !/^(accounts|explore|reels?|p|direct|stories|challenge)$/i.test(name)) {
          return `https://www.instagram.com/${name.toLowerCase()}/`;
        }
      } catch { /* Ignore non-profile links. */ }
      return null;
    };
    let stable = 0;
    let limited = false;
    const clicked = new WeakSet();
    // Duplicates are filtered by the core, so read past its 30-new-candidate quota.
    const full = () => comments.size >= 200 || new Set([...comments.values()].map(c => c.profile_url)).size >= 100;
    for (let round = 0; round < 14; round++) {
      const before = comments.size;
      let lastRow = null;
      // A comment permalink distinguishes its author from caption tags and recommendations.
      for (const permalink of document.querySelectorAll('a[href*="/c/"]')) {
        let link;
        try { link = new URL(permalink.href, url); } catch { continue; }
        const match = link.pathname.match(/^\/(?:[\w.]+\/)?(?:p|reel|reels|tv)\/([\w-]+)\/c\/(\d+)\/?$/);
        if (!match || match[1] !== code || !['instagram.com', 'www.instagram.com'].includes(link.hostname)) continue;
        let row = permalink.parentElement;
        let authorLink = null;
        for (let depth = 0; row && depth < 7; depth++, row = row.parentElement) {
          authorLink = [...row.querySelectorAll('a[href]')].find(a => profileLink(a));
          if (authorLink) break;
        }
        if (!row || !authorLink) continue;
        const candidate = profileLink(authorLink);
        if (candidate === `https://www.instagram.com/${author.toLowerCase()}/`) continue;
        // Current Instagram uses an author/time row followed by a sibling text block.
        const header = row;
        if (row.parentElement && row.parentElement.querySelectorAll('a[href*="/c/"]').length === 1) row = row.parentElement;
        const copy = row.cloneNode(true);
        for (const control of copy.querySelectorAll('button,[role="button"],time,svg')) control.remove();
        let text = (copy.innerText || copy.textContent || '').trim();
        const name = authorLink.innerText?.trim() || '';
        if (name && text.startsWith(name)) text = text.slice(name.length).trim();
        const time = header.querySelector('time[datetime]');
        comments.set(match[2], { profile_url: candidate, text: text.slice(0, 1500),
          published_at: time?.getAttribute('datetime') || null });
        lastRow = row;
        if (full()) break;
      }
      if (full()) { limited = true; break; }
      const more = [...document.querySelectorAll('button,[role="button"]')].find(button => {
        const label = (button.innerText || button.getAttribute('aria-label') || '').trim();
        return !clicked.has(button) && /^(?:(?:load|view|show) (?:all |more |previous )?(?:comments|replies)|(?:показать|смотреть|загрузить) (?:все |ещ[её] |предыдущие )?(?:комментарии|ответы))/i.test(label);
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
      await new Promise(resolve => setTimeout(resolve, 750));
      if (location.href !== url.href) return { ready: false };
      const limitedNow = /try again later|too many requests|wait a few minutes|подождите несколько минут|повторите попытку позже/i.test(document.body?.innerText || '');
      if (limitedNow || document.querySelector('input[type="password"]')) {
        return { url: url.href, ready: false, blocked: true, rate_limited: limitedNow };
      }
    }
    return { url: url.href, ready: document.readyState === 'complete' && Boolean(author),
      blocked: false, caption: caption.slice(0, 12000), author, collaborators, published_at,
      comments: [...comments.values()], comments_limited: limited };

  }
  return { url: url.href, ready: false, blocked: false };
})()
