(() => {
  try {
    const url = location.href;
    const parts = location.pathname.split('/').filter(Boolean);
    const username = parts[0] || '';
    if (!/(^|\.)instagram\.com$/.test(location.hostname || new URL(url).hostname)
      || parts.length !== 1 || !/^[a-zA-Z0-9_.]{1,30}$/.test(username)
      || /^(accounts|challenge|direct|explore|reel|reels|p|stories|about|privacy|terms)$/i.test(username)) {
      return { url, ready: false, blocked: /\/(accounts|challenge)\//.test(location.pathname) };
    }
    const body = (document.body?.innerText || '').slice(0, 20000);
    const dialog = document.querySelector('[role="dialog"]')?.innerText || '';
    // Rate limits need a break; other gates need the user (login, challenge).
    const rateLimited = /try again later|too many requests|подождите несколько минут|повторите попытку позже/i.test(body);
    const blocked = rateLimited || /\/(accounts|challenge)\//.test(location.pathname)
      || !!document.querySelector('input[type="password"]')
      || /log in|sign up|войти|зарегистрир/i.test(dialog)
      || /зарегистрируйтесь, чтобы|sign up to see|log in to see|смотрите фото, видео и другой контент/i.test(body)
      || /подтвердите.*личность/i.test(body)
      // Audience-restricted accounts render a login stub without the profile header.
      || /ограниченный профиль|restricted profile|недоступен для определ[её]нных аудиторий|not available (to|for) certain audiences/i.test(body);
    const root = document.querySelector('main header') || document.querySelector('main');
    const header = (root?.innerText || '').slice(0, 12000);
    const meta = name => document.querySelector(`meta[property="${name}"]`)?.content || '';
    const title = meta('og:title') || document.title;
    const titleUsername = title.match(/@([a-zA-Z0-9_.]{1,30})/)?.[1]?.toLowerCase();
    const description = meta('og:description') || document.querySelector('meta[name="description"]')?.content || '';
    let external = '';
    for (const link of Array.from(root?.querySelectorAll('a[href]') || []).slice(0, 100)) {
      try {
        let target = new URL(link.href);
        if (target.hostname === 'l.instagram.com') target = new URL(target.searchParams.get('u') || '');
        if (target.protocol === 'https:' && !target.username && !target.password
          && !/(^|\.)(instagram\.com|facebook\.com|meta\.com|threads\.net)$/.test(target.hostname)) {
          external = target.href; break;
        }
      } catch { /* Invalid page links are ignored. */ }
    }
    return { url, title: title.slice(0, 1000), description: description.slice(0, 12000),
      header, external_url: external.slice(0, 2048), blocked, rate_limited: rateLimited,
      private: /this account is private|это закрытый аккаунт|закрытый профиль/i.test(body),
      ready: !blocked && document.readyState === 'complete'
        && titleUsername === username.toLowerCase()
        && (header.length > 0 || /followers|подписчик/i.test(description)) };
  } catch { return { blocked: false, ready: false }; }
})()
