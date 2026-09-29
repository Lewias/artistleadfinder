/* eslint-disable-next-line @typescript-eslint/no-unused-expressions -- evaluated as a function by Playwright */
async (args) => {
  // Outreach: one direct message from the sender's already open Instagram tab, sent the
  // way the web client sends it. The browser attaches the session cookies itself. The
  // CSRF token is read from the page only to put it into the header Instagram requires;
  // it is never returned. The result carries no headers, cookies or tokens.
  //
  // Only failures before the message request are reported as retryable ("network").
  // Once the request has left, any failure is "unconfirmed": the core never resends it.
  const CONFIG = {
    hosts: ['instagram.com', 'www.instagram.com'],
    // The public app id of instagram.com's web client, used when the page does not expose it.
    webAppId: '936619743392459',
    timeoutMs: 15000,
    maxText: 1000,
    lookupPath: '/api/v1/users/web_profile_info/?username=',
    sendPath: '/api/v1/direct_v2/threads/broadcast/text/',
  };
  const username = String(args?.username || '').toLowerCase();
  let userId = String(args?.user_id || '');
  const text = String(args?.text || '');
  const clientContext = String(args?.client_context || '');
  const fail = (error, status = null) => ({ outcome: 'error', error, status });

  if (!CONFIG.hosts.includes(location.hostname)) return fail('no_instagram_tab');
  if (
    !/^[a-z0-9_.]{1,30}$/.test(username) ||
    !text.trim() ||
    text.length > CONFIG.maxText ||
    !/^\d{1,30}$/.test(clientContext) ||
    (userId && !/^\d{1,30}$/.test(userId))
  ) {
    return fail('bad_request');
  }
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return fail('network');
  const csrf = (String(document.cookie || '').match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1] || '';
  if (!csrf) return fail('login');

  const appId = () => {
    for (const script of [...document.querySelectorAll('script:not([src])')].slice(0, 300)) {
      const match = (script.textContent || '').match(/"(?:X-IG-App-ID|appId|APP_ID)"\s*:\s*"(\d{6,20})"/);
      if (match) return match[1];
    }
    return CONFIG.webAppId;
  };
  const headers = { 'x-ig-app-id': appId(), 'x-requested-with': 'XMLHttpRequest', accept: 'application/json' };
  const request = async (path, init = {}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), CONFIG.timeoutMs);
    try {
      const response = await fetch(path, { credentials: 'include', signal: controller.signal, ...init });
      const finalPath = new URL(response.url || path, location.href).pathname;
      const redirect = /^\/accounts\/login/.test(finalPath)
        ? 'login'
        : /^\/challenge\//.test(finalPath)
          ? 'challenge'
          : null;
      let body = null;
      if ((response.headers.get('content-type') || '').includes('json')) {
        try {
          body = await response.json();
        } catch {
          body = null;
        }
      }
      return { status: response.status, ok: response.ok, redirect, body };
    } finally {
      clearTimeout(timer);
    }
  };
  // Typed reason of a refused request; message texts are matched, never returned.
  const refused = ({ status, redirect, body }) => {
    const message = String(body?.message || '');
    if (redirect === 'login' || status === 401 || message === 'login_required') return 'login';
    if (redirect === 'challenge' || message === 'checkpoint_required' || body?.checkpoint_url) return 'checkpoint';
    if (status === 429 || message === 'feedback_required' || body?.spam === true) return 'rate_limited';
    if (status === 404 || message === 'user_not_found') return 'not_found';
    return 'rejected';
  };

  // 1. Recipient id, when the lead has none yet. Nothing has been sent.
  if (!userId) {
    let lookup;
    try {
      lookup = await request(CONFIG.lookupPath + encodeURIComponent(username), { headers });
    } catch {
      return fail('network');
    }
    if (!lookup.ok || lookup.redirect) return fail(refused(lookup), lookup.status);
    userId = String(lookup.body?.data?.user?.id || '');
    if (!/^\d{1,30}$/.test(userId)) return fail('not_found', lookup.status);
  }

  // 2. The message. The stable client_context lets Instagram recognise a repeat.
  const form = new URLSearchParams({
    recipient_users: JSON.stringify([[userId]]),
    client_context: clientContext,
    mutation_token: clientContext,
    offline_threading_id: clientContext,
    action: 'send_item',
    text,
  });
  let sent;
  try {
    sent = await request(CONFIG.sendPath, {
      method: 'POST',
      headers: { ...headers, 'content-type': 'application/x-www-form-urlencoded', 'x-csrftoken': csrf },
      body: form.toString(),
    });
  } catch {
    return fail('unconfirmed');
  }
  if (sent.ok && !sent.redirect && sent.body?.status === 'ok') {
    const payload = sent.body.payload || {};
    return {
      outcome: 'sent',
      status: sent.status,
      user_id: userId,
      message_id: String(payload.item_id || '').slice(0, 80),
      thread_id: String(payload.thread_id || '').slice(0, 80),
    };
  }
  if (sent.ok && !sent.redirect && !sent.body) return fail('unconfirmed', sent.status);
  return fail(refused(sent), sent.status);
}
