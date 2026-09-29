/* eslint-disable-next-line @typescript-eslint/no-unused-expressions -- evaluated as a function by Playwright */
async (args) => {
  // Profile resolver, API step: one web API request from the already open Instagram tab.
  // The browser attaches the session cookies itself; this script never reads
  // document.cookie and never returns request headers, cookies or tokens.
  const CONFIG = {
    hosts: ['instagram.com', 'www.instagram.com'],
    // Only Instagram's own API paths, given by the core's endpoint adapter.
    allowedPath: /^\/api\/v1\/users\/[\w/]+\/?\?username=[a-z0-9_.]{1,30}$/,
    // The public app id of instagram.com's web client, used when the page does not expose it.
    webAppId: '936619743392459',
    timeoutMs: 15000,
    maxArray: 12,
    maxString: 4000,
    // data.user.edge_owner_to_timeline_media.edges[].node.edge_media_to_caption.edges[].node.text
    maxDepth: 12,
    // Heavy media fields are dropped before the answer leaves the page.
    dropKeys: /^(?:display_resources|display_url|thumbnail_src|thumbnail_resources|video_url|dash_info|image_versions2|video_versions|profile_pic_url(?:_hd)?|hd_profile_pic_url_info|edge_felix_video_timeline|edge_saved_media|edge_media_collections|edge_mutual_followed_by|edge_related_profiles)$/,
  };
  const url = String(args?.url || '');
  const username = String(args?.username || '').toLowerCase();
  const path = String(args?.path || '');
  const done = api => ({ url, ready: true, blocked: false, api });
  if (!CONFIG.hosts.includes(location.hostname)) return done({ error: 'no_instagram_tab' });
  if (!/^[a-z0-9_.]{1,30}$/.test(username) || !CONFIG.allowedPath.test(path)) {
    return done({ error: 'bad_request' });
  }

  // Session headers: the same fixed headers the web client sends; no secrets involved.
  const sessionHeaders = () => {
    let appId = CONFIG.webAppId;
    for (const script of [...document.querySelectorAll('script:not([src])')].slice(0, 300)) {
      const match = (script.textContent || '').match(/"(?:X-IG-App-ID|appId|APP_ID)"\s*:\s*"(\d{6,20})"/);
      if (match) { appId = match[1]; break; }
    }
    return { 'x-ig-app-id': appId, 'x-requested-with': 'XMLHttpRequest', accept: 'application/json' };
  };
  const prune = (value, depth = 0) => {
    if (depth > CONFIG.maxDepth) return null;
    if (typeof value === 'string') return value.slice(0, CONFIG.maxString);
    if (Array.isArray(value)) return value.slice(0, CONFIG.maxArray).map(item => prune(item, depth + 1));
    if (value && typeof value === 'object') {
      const out = {};
      for (const [key, item] of Object.entries(value)) {
        if (!CONFIG.dropKeys.test(key)) out[key] = prune(item, depth + 1);
      }
      return out;
    }
    return value;
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONFIG.timeoutMs);
  try {
    const response = await fetch(path, {
      headers: sessionHeaders(),
      credentials: 'include',
      signal: controller.signal,
    });
    const finalPath = new URL(response.url, location.href).pathname;
    const redirect = /^\/accounts\/login/.test(finalPath) ? 'login' : /^\/challenge\//.test(finalPath) ? 'challenge' : null;
    let body = null;
    if ((response.headers.get('content-type') || '').includes('json')) {
      try { body = prune(await response.json()); } catch { body = null; }
    }
    return done({ endpoint: String(args?.endpoint || ''), status: response.status, redirect, body });
  } catch (error) {
    return done({ error: error?.name === 'AbortError' ? 'timeout' : 'network' });
  } finally {
    clearTimeout(timer);
  }
}
