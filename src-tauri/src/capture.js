(() => {
  // Profile page (/<username>/). Each field walks a strategy chain (structured data and
  // meta tags, then semantic header elements, then visible text) and reports which one
  // answered; nothing is guessed. All Instagram DOM assumptions live in SELECTORS and TEXT.
  const SELECTORS = {
    headers: ['main header', 'header'],
    main: 'main',
    title: 'meta[property="og:title"]',
    description: 'meta[property="og:description"]',
    metaDescription: 'meta[name="description"]',
    ownerId: 'meta[property="instapp:owner_user_id"]',
    structuredData: 'script[type="application/ld+json"]',
    inlineScripts: 'script:not([src])',
    statLink: kind => `a[href$="/${kind}/"]`,
    bioHeading: 'h1',
    contactLinks: 'a[href^="mailto:"], a[href^="tel:"]',
    gridCaptions: 'main a[href*="/p/"] img[alt], main a[href*="/reel/"] img[alt]',
    password: 'input[type="password"]',
    dialog: '[role="dialog"]',
  };
  const TEXT = {
    rateLimited: /try again later|too many requests|wait a few minutes|подождите несколько минут|повторите попытку позже/i,
    checkpoint: /подтвердите.*личность|confirm (?:it[’']?s|that it[’']?s) you/i,
    loginDialog: /log in|sign up|войти|зарегистрир/i,
    loginWall: /зарегистрируйтесь, чтобы|sign up to see|log in to see|смотрите фото, видео и другой контент/i,
    // Audience-restricted accounts render a login stub without the profile header.
    restricted: /ограниченный профиль|restricted profile|недоступен для определ[её]нных аудиторий|not available (to|for) certain audiences/i,
    private: /this account is private|это закрытый аккаунт|закрытый профиль/i,
    unavailable: /sorry, this page isn[’']?t available|эта страница недоступна|page not found|страница не найдена/i,
    stats: {
      followers: /followers|подписчик/i,
      following: /following|подписк|подписок/i,
      posts: /posts?|публикаци/i,
    },
    // Meta description: "12K Followers, 300 Following, 40 Posts - See Instagram photos…" or,
    // in Russian, "Подписчики: 12K, подписки: 300, публикации: 40 — …" (label first). A number
    // ends with a digit, so "12K, подписки" never lends the followers count to "following".
    metaCount: label => {
      const number = String.raw`(\d(?:[\d\s.,\u00a0\u202f]*\d)?\s*(?:[KkMmBb]|тыс\.?|млн\.?)?)`;
      return [
        new RegExp(String.raw`${number}\s*(?:${label.source})`, 'i'),
        new RegExp(String.raw`(?:${label.source})[^\d:,]{0,4}:\s*${number}`, 'i'),
      ];
    },
    contactLine: /@|\+\d|e-?mail|mail|phone|tel\b|whats\s?app|viber|telegram|booking|mgmt|management|телефон|почта|букинг|менеджмент/i,
    // Auto-generated image descriptions are not captions.
    autoAlt: /^(?:photo|video|image)s? (?:shared )?by .{1,80}? on \w|may be an? (?:image|graphic)|может быть изображение/i,
  };
  const LIMITS = { header: 12000, description: 12000, links: 10, captions: 8, caption: 500, bio: 10000 };
  const hosts = ['instagram.com', 'www.instagram.com'];
  const internalHosts = /(^|\.)(instagram\.com|facebook\.com|meta\.com|threads\.net)$/;
  const reserved = /^(accounts|challenge|direct|explore|reel|reels|p|stories|about|privacy|terms)$/i;

  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const meta = selector => document.querySelector(selector)?.content || '';
  const structured = () => {
    const items = [];
    for (const script of [...document.querySelectorAll(SELECTORS.structuredData)].slice(0, 10)) {
      try {
        const data = JSON.parse(script.textContent || 'null');
        items.push(...(Array.isArray(data) ? data : [data]).filter(Boolean));
      } catch { /* Ignore malformed JSON-LD. */ }
    }
    return items.flatMap(item => [item, item.mainEntity, item.author].filter(Boolean));
  };
  const found = (value, strategy) => (value ? { value: String(value), strategy } : null);

  function extractProfileIdentity(username, title) {
    const entities = structured();
    const nameFromData = entities.find(item => clean(item.alternateName).replace(/^@/, '').toLowerCase() === username)?.name;
    const nameFromTitle = title.match(new RegExp(`^(.*?)\\s*\\(@${username.replace(/\./g, '\\.')}\\)`, 'i'))?.[1];
    const ownerId = meta(SELECTORS.ownerId);
    const idFromData = entities.map(item => item.identifier?.value || item.identifier).find(value => /^\d{1,30}$/.test(String(value || '')));
    let idFromPage = '';
    for (const script of [...document.querySelectorAll(SELECTORS.inlineScripts)].slice(0, 300)) {
      const match = (script.textContent || '').match(/"profilePage_(\d{1,30})"/);
      if (match) { idFromPage = match[1]; break; }
    }
    return {
      full_name: found(clean(nameFromData), 'structured_data') || found(clean(nameFromTitle), 'meta_title'),
      user_id: found(/^\d{1,30}$/.test(ownerId) ? ownerId : '', 'meta_owner_id')
        || found(idFromData, 'structured_data') || found(idFromPage, 'page_data'),
    };
  }

  function extractProfileStats(root, description) {
    const stats = {};
    for (const [kind, label] of Object.entries(TEXT.stats)) {
      const link = root?.querySelector?.(SELECTORS.statLink(kind));
      // Exact counts sit in a title attribute; the visible text is often rounded.
      const exact = link?.querySelector('[title]')?.getAttribute('title') || link?.getAttribute('title');
      const fromMeta = TEXT.metaCount(label).map(pattern => description.match(pattern)?.[1]).find(Boolean);
      const fromLink = link ? clean(link.innerText) : '';
      const line = (root?.innerText || '').split('\n').map(clean).find(text => label.test(text) && /\d/.test(text));
      const chain = [[exact, 'title_attribute'], [fromMeta, 'meta_description'], [fromLink, 'header_link'], [line, 'visible_text']];
      const [raw, strategy] = chain.find(([value]) => value && /\d/.test(value)) || [];
      if (raw) stats[kind] = { raw: clean(raw).slice(0, 80), strategy };
    }
    return stats;
  }

  function extractProfileBio(root, identity, username) {
    const entities = structured();
    const fromData = entities.map(item => item.description).find(text => typeof text === 'string' && text.trim());
    // The bio heading of the profile header; skipped when it only repeats the name.
    const heading = clean(root?.querySelector?.(SELECTORS.bioHeading)?.innerText);
    const name = clean(identity.full_name?.value).toLowerCase();
    const fromHeading = heading && ![username, name].includes(heading.toLowerCase()) ? heading : '';
    const bio = found(fromData, 'structured_data') || found(fromHeading, 'header_heading');
    return bio ? { ...bio, value: bio.value.slice(0, LIMITS.bio) } : null;
  }

  function extractProfileLinks(root) {
    const links = [];
    for (const link of [...(root?.querySelectorAll('a[href]') || [])].slice(0, 100)) {
      try {
        let target = new URL(link.href);
        // Redirect wrappers are unwrapped locally; the destination is never opened.
        if (target.hostname === 'l.instagram.com') target = new URL(target.searchParams.get('u') || '');
        if (target.protocol === 'https:' && !target.username && !target.password
          && !internalHosts.test(target.hostname) && !links.includes(target.href)) {
          links.push(target.href.slice(0, 2048));
        }
      } catch { /* Invalid page links are ignored. */ }
      if (links.length >= LIMITS.links) break;
    }
    return links;
  }

  function extractProfileCategory(root, identity, username) {
    // Short text lines of the header near the name; the core keeps only known categories.
    const skip = new Set([username, clean(identity.full_name?.value).toLowerCase()]);
    const lines = (root?.innerText || '').split('\n').map(clean)
      .filter(text => text && text.length <= 40 && !/\d|https?:|www\.|@/.test(text) && !skip.has(text.toLowerCase()));
    return { candidates: lines.slice(0, 12) };
  }

  function extractProfileContacts(root) {
    const emails = [];
    const phones = [];
    for (const link of [...(root?.querySelectorAll?.(SELECTORS.contactLinks) || [])].slice(0, 20)) {
      const href = decodeURIComponent(String(link.href || ''));
      if (href.startsWith('mailto:')) emails.push(href.slice(7).split('?')[0].slice(0, 200));
      if (href.startsWith('tel:')) phones.push(href.slice(4).slice(0, 40));
    }
    const text = (root?.innerText || '').split('\n').map(clean).filter(line => TEXT.contactLine.test(line));
    return { emails, phones, text: text.join('\n').slice(0, 2000) };
  }

  function extractRecentCaptions() {
    return [...document.querySelectorAll(SELECTORS.gridCaptions)]
      .map(image => clean(image.getAttribute('alt')))
      .filter(text => text && !TEXT.autoAlt.test(text))
      .slice(0, LIMITS.captions)
      .map(text => text.slice(0, LIMITS.caption));
  }

  try {
    const url = location.href;
    const hostname = location.hostname || new URL(url).hostname;
    const parts = location.pathname.split('/').filter(Boolean);
    const username = (parts[0] || '').toLowerCase();
    if (!hosts.includes(hostname) || parts.length !== 1
      || !/^[a-z0-9_.]{1,30}$/.test(username) || reserved.test(username)) {
      return { url, ready: false, blocked: /\/(accounts|challenge)\//.test(location.pathname) };
    }
    const body = (document.body?.innerText || '').slice(0, 20000);
    const dialog = document.querySelector(SELECTORS.dialog)?.innerText || '';
    // Rate limits need a break; other gates need the user (login, challenge).
    const rateLimited = TEXT.rateLimited.test(body);
    const checkpoint = /\/challenge\//.test(location.pathname) || TEXT.checkpoint.test(body);
    const login = /\/accounts\//.test(location.pathname) || !!document.querySelector(SELECTORS.password)
      || TEXT.loginDialog.test(dialog) || TEXT.loginWall.test(body) || TEXT.restricted.test(body);
    const blockReason = rateLimited ? 'rate_limited' : checkpoint ? 'checkpoint' : login ? 'login' : null;
    if (!blockReason && TEXT.unavailable.test(body)) {
      return { url, ready: true, blocked: false, unavailable: true };
    }
    const root = SELECTORS.headers.map(selector => document.querySelector(selector)).find(Boolean)
      || document.querySelector(SELECTORS.main);
    const header = (root?.innerText || '').slice(0, LIMITS.header);
    const title = meta(SELECTORS.title) || document.title;
    const titleUsername = title.match(/@([a-zA-Z0-9_.]{1,30})/)?.[1]?.toLowerCase();
    const description = (meta(SELECTORS.description) || meta(SELECTORS.metaDescription)).slice(0, LIMITS.description);
    const identity = extractProfileIdentity(username, title);
    const links = extractProfileLinks(root);
    return {
      url, title: title.slice(0, 1000), description, header,
      external_url: links[0] || '', links, captions: extractRecentCaptions(),
      user_id: identity.user_id?.value || '',
      profile: {
        ...identity,
        stats: extractProfileStats(root, description),
        bio: extractProfileBio(root, identity, username),
        category: extractProfileCategory(root, identity, username),
        contacts: extractProfileContacts(root),
      },
      blocked: !!blockReason, block_reason: blockReason, rate_limited: rateLimited,
      private: TEXT.private.test(body),
      ready: !blockReason && document.readyState === 'complete'
        && titleUsername === username
        && (header.length > 0 || /followers|подписчик/i.test(description)) };
  } catch { return { blocked: false, ready: false }; }
})()
