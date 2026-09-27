(() => {
  const page = new URL(location.href);
  if (page.origin !== 'https://search.brave.com' || page.pathname !== '/search') return { ready: false };
  const text = (document.body?.innerText || '').slice(0, 50000);
  const blocked = /verify (you are|that you)|unusual traffic|captcha|провер.*человек|too many requests/i.test(text)
    || Boolean(document.querySelector('iframe[src*="captcha"], input[name="captcha"]'));
  const excluded = new Set(['accounts', 'explore', 'reels', 'reel', 'p', 'direct', 'stories', 'challenge', 'web', 'about', 'developer', 'legal', 'privacy', 'terms']);
  const results = new Map();
  for (const anchor of [...document.querySelectorAll('a[href]')].slice(0, 1000)) {
    try {
      const url = new URL(anchor.href);
      const name = url.pathname.replace(/^\/+|\/+$/g, '').toLowerCase();
      if (url.protocol !== 'https:' || !['instagram.com', 'www.instagram.com'].includes(url.hostname)
        || url.username || url.password || url.port || !/^[a-z0-9_.]{1,30}$/.test(name) || excluded.has(name)) continue;
      const canonical = `https://www.instagram.com/${name}/`;
      if (!results.has(canonical)) results.set(canonical, { url: canonical, username: name, title: (anchor.innerText || name).trim().slice(0, 200) });
      if (results.size >= 20) break;
    } catch { /* Ignore non-profile and malformed links. */ }
  }
  const empty = /no results found|couldn't find any results|no matching results|ничего не найдено/i.test(text);
  return { url: page.href, query: page.searchParams.get('q'), blocked, ready: !blocked && document.readyState === 'complete' && (results.size > 0 || empty), results: [...results.values()] };
})()
