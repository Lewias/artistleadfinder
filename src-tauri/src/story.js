/* eslint-disable-next-line @typescript-eslint/no-unused-expressions -- evaluated as a function by Playwright */
async (args) => {
  // Active stories of one source (/stories/<source>/): walk frame by frame and report
  // conservative candidates per story. Only elements inside the story media area count;
  // the viewer header (the source itself), reply box and navigation are excluded.
  const SELECTORS = {
    viewer: ['section', '[role="dialog"]', 'main'],
    header: 'header',
    media: 'img, video',
    anchors: 'a[href]',
    tapTargets: 'a, [role="button"], [role="link"]',
    next: ['button[aria-label="Next"]', 'button[aria-label="Далее"]', '[role="button"][aria-label="Next"]', '[role="button"][aria-label="Далее"]'],
    viewStory: /^(?:view story|посмотреть историю)$/i,
    password: 'input[type="password"]',
    replyBox: 'textarea, input, form',
  };
  const TEXT = {
    rateLimited: /try again later|too many requests|wait a few minutes|подождите несколько минут|повторите попытку позже/i,
    checkpoint: /confirm (?:it[’']?s|that it[’']?s) you|подтвердите.*личность|suspicious (?:login|activity)/i,
    loginWall: /log in to see|sign up to see|войдите, чтобы|зарегистрируйтесь, чтобы/i,
  };
  const hosts = ['instagram.com', 'www.instagram.com'];
  const reserved = /^(accounts|explore|reels?|p|tv|direct|stories|challenge|about|developer|legal|web)$/i;
  const num = (value, fallback, low, high) => {
    const parsed = Number(value);
    return Math.max(low, Math.min(high, Number.isFinite(parsed) ? parsed : fallback));
  };
  const maxStories = num(args?.maxStories, 20, 1, 100);
  const delayMs = num(args?.delayMs, 1500, 500, 5000);
  const skip = new Set((args?.skip || []).map(String));
  const deadline = Date.now() + 25000;
  const start = new URL(location.href);
  const source = String(args?.source || start.pathname.split('/').filter(Boolean)[1] || '').toLowerCase();
  // Report the requested page: Instagram rewrites it to /stories/<source>/<id>/ while playing.
  const requested = `https://www.instagram.com/stories/${source}/`;
  if (!hosts.includes(start.hostname) || !/^\/stories\/[\w.]+(?:\/\d+)?\/?$/.test(start.pathname)) {
    // Instagram redirects to the profile when there are no active stories.
    const onProfile = hosts.includes(start.hostname) && start.pathname.replace(/\//g, '').toLowerCase() === source;
    return { url: requested, ready: onProfile, blocked: false, stories: [], seen: 0, end_reason: onProfile ? 'no_active_stories' : 'not_story_page' };
  }
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const gate = () => {
    const body = (document.body?.innerText || '').slice(0, 20000);
    if (TEXT.rateLimited.test(body)) return 'rate_limited';
    if (/\/challenge\//.test(location.pathname) || TEXT.checkpoint.test(body)) return 'checkpoint';
    if (/\/accounts\/login/.test(location.pathname) || document.querySelector(SELECTORS.password) || TEXT.loginWall.test(body)) return 'login';
    return null;
  };
  const blocked = reason => ({ url: requested, ready: false, blocked: true, block_reason: reason, rate_limited: reason === 'rate_limited' });
  const profileName = href => {
    try {
      const link = new URL(href, location.href);
      const name = link.pathname.match(/^\/([a-zA-Z0-9_.]{1,30})\/?$/)?.[1];
      return hosts.includes(link.hostname) && name && !reserved.test(name) ? name.toLowerCase() : null;
    } catch { return null; }
  };
  const mediaLink = href => {
    try {
      const link = new URL(href, location.href);
      const match = link.pathname.match(/^\/(?:([\w.]+)\/)?(p|reel|reels|tv)\/([\w-]+)\/?$/);
      if (!hosts.includes(link.hostname) || !match) return null;
      const kind = match[2] === 'p' ? 'post' : 'reel';
      return { kind, author: match[1]?.toLowerCase() || null, url: `https://www.instagram.com/${match[1] ? `${match[1]}/` : ''}${match[2] === 'reels' ? 'reel' : match[2]}/${match[3]}/` };
    } catch { return null; }
  };
  // The story viewer is the smallest landmark that contains the media and a link to the source.
  const viewer = () => {
    for (const selector of SELECTORS.viewer) {
      for (const node of document.querySelectorAll(selector)) {
        const ownsSource = [...node.querySelectorAll(SELECTORS.anchors)].some(a => profileName(a.getAttribute('href')) === source);
        if (ownsSource && node.querySelector(SELECTORS.media)) return node;
      }
    }
    return null;
  };
  // Elements that belong to the story media: inside the viewer, outside its header and reply box.
  const inMedia = (root, node) => {
    const head = node.closest(SELECTORS.header);
    return root.contains(node) && !(head && root.contains(head)) && !node.closest(SELECTORS.replyBox);
  };

  // Extractors: each returns [{ username, evidenceType, confidence }].
  const extractStoryMentions = root => {
    const found = [];
    for (const node of root.querySelectorAll(SELECTORS.tapTargets)) {
      if (!inMedia(root, node)) continue;
      const label = (node.getAttribute('aria-label') || node.innerText || '').trim();
      const handle = label.match(/^@([a-zA-Z0-9_.]{1,30})$/)?.[1];
      const linked = node.getAttribute('href') ? profileName(node.getAttribute('href')) : null;
      if (handle && (!linked || linked === handle.toLowerCase())) {
        found.push({ username: handle.toLowerCase(), evidenceType: 'mention_sticker', confidence: 1 });
      }
    }
    return found;
  };
  const extractStoryProfileLinks = (root, cards = []) => {
    const found = [];
    for (const anchor of root.querySelectorAll(SELECTORS.anchors)) {
      // Links inside a shared post/reel card name that media's author, handled separately.
      if (!inMedia(root, anchor) || cards.some(card => card.contains(anchor))) continue;
      const name = profileName(anchor.getAttribute('href'));
      if (name && !(anchor.innerText || '').trim().startsWith('@')) {
        found.push({ username: name, evidenceType: 'profile_link', confidence: 0.9 });
      }
    }
    return found;
  };
  const extractStorySharedMedia = root => {
    const shared = [];
    for (const anchor of root.querySelectorAll(SELECTORS.anchors)) {
      if (!inMedia(root, anchor)) continue;
      const media = mediaLink(anchor.getAttribute('href'));
      if (media && !shared.some(item => item.url === media.url)) shared.push({ ...media, anchor });
    }
    return shared;
  };
  // The shared card: the largest ancestor of the media link that holds no other shared media.
  const cardOf = (item, shared, root) => {
    let node = item.anchor;
    while (node.parentElement && node.parentElement !== root
        && !shared.some(other => other !== item && node.parentElement.contains(other.anchor))) {
      node = node.parentElement;
    }
    return node;
  };
  const extractStoryAuthorFromSharedMedia = (item, card) => {
    if (item.author) {
      return { username: item.author, evidenceType: `shared_${item.kind}_author`, confidence: 0.95 };
    }
    // The card names its original author next to the media link; ambiguity means no answer.
    const names = [...new Set([...card.querySelectorAll(SELECTORS.anchors)]
      .map(anchor => profileName(anchor.getAttribute('href'))).filter(Boolean))];
    return names.length === 1
      ? { username: names[0], evidenceType: `shared_${item.kind}_author`, confidence: 0.9 }
      : null;
  };
  const extractStoryCandidates = root => {
    const shared = extractStorySharedMedia(root);
    const cards = new Map(shared.map(item => [item, cardOf(item, shared, root)]));
    const candidates = [...extractStoryMentions(root), ...extractStoryProfileLinks(root, [...cards.values()])];
    const unresolved = [];
    for (const item of shared) {
      const author = extractStoryAuthorFromSharedMedia(item, cards.get(item));
      if (author) candidates.push(author);
      // The original author is read from the publication page by the core.
      else unresolved.push({ url: item.url, kind: item.kind });
    }
    const best = new Map();
    for (const candidate of candidates) {
      if (candidate.username === source) continue;
      const known = best.get(candidate.username);
      if (!known || known.confidence < candidate.confidence) best.set(candidate.username, candidate);
    }
    return { candidates: [...best.values()], shared_media: unresolved };
  };

  // Instagram may ask to confirm viewing before the first frame.
  const confirm = [...document.querySelectorAll('button,[role="button"]')].find(button => SELECTORS.viewStory.test((button.innerText || '').trim()));
  if (confirm) { confirm.click(); await wait(delayMs); }
  const stories = [];
  let previous = null;
  let repeats = 0;
  let endReason = 'limit';
  for (let index = 0; index < maxStories; index++) {
    const stop = gate();
    if (stop) return { ...blocked(stop), stories };
    if (Date.now() > deadline) { endReason = 'time_budget'; break; }
    const path = location.pathname.match(/^\/stories\/([\w.]+)\/(\d+)\/?$/);
    if (!path) { endReason = stories.length ? 'end_of_stories' : 'no_active_stories'; break; }
    if (path[1].toLowerCase() !== source) { endReason = 'other_account'; break; }
    const storyId = path[2];
    if (storyId === previous) {
      repeats += 1;
      if (repeats >= 3) { endReason = 'stuck'; break; }
    } else {
      repeats = 0;
      previous = storyId;
      const root = viewer();
      if (skip.has(storyId)) {
        stories.push({ id: storyId, url: location.href, skipped: true, candidates: [], shared_media: [] });
      } else if (!root) {
        stories.push({ id: storyId, url: location.href, error: 'viewer_not_found', candidates: [], shared_media: [] });
      } else {
        stories.push({ id: storyId, url: location.href, ...extractStoryCandidates(root) });
      }
    }
    const next = SELECTORS.next.map(selector => document.querySelector(selector)).find(Boolean);
    if (!next) { endReason = 'no_next_button'; break; }
    next.click();
    await wait(delayMs);
  }
  // Leave the viewer so the window is ready for the next page.
  document.dispatchEvent?.(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  return { url: requested, ready: true, blocked: false, source, stories, seen: stories.length, end_reason: endReason };
}
