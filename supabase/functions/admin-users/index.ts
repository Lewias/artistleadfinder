// Admin actions that need the service role: create an account and set a password.
// The service role key lives only in Supabase; the app sends the admin's own session.
import { createClient } from 'npm:@supabase/supabase-js@2';

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, apikey, content-type',
};
const EMAIL = /^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$/;

const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } });

Deno.serve(async request => {
  if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
  if (request.method !== 'POST') return reply(405, { error: 'Метод не поддерживается.' });

  const url = Deno.env.get('SUPABASE_URL')!;
  const service = createClient(url, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, {
    auth: { persistSession: false },
  });
  // Who is calling: the admin's own access token, checked by Supabase Auth.
  const token = (request.headers.get('Authorization') ?? '').replace(/^Bearer\s+/i, '');
  const { data: caller } = await service.auth.getUser(token);
  if (!caller?.user) return reply(401, { error: 'Войдите в аккаунт.' });
  const { data: profile } = await service
    .from('profiles')
    .select('role, blocked')
    .eq('id', caller.user.id)
    .single();
  if (profile?.role !== 'admin' || profile.blocked) return reply(403, { error: 'Доступно только админу.' });

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return reply(400, { error: 'Некорректный запрос.' });
  }
  const password = String(body.password ?? '');
  if (body.action === 'create') {
    const email = String(body.email ?? '').trim().toLowerCase();
    const name = String(body.display_name ?? '').trim().slice(0, 80);
    if (!EMAIL.test(email)) return reply(400, { error: 'Некорректный email.' });
    if (password.length < 8) return reply(400, { error: 'Пароль — не короче 8 символов.' });
    const { data, error } = await service.auth.admin.createUser({
      email,
      password,
      email_confirm: true,
      user_metadata: { display_name: name },
    });
    if (error) return reply(400, { error: /already/i.test(error.message) ? 'Такой email уже есть.' : 'Не удалось создать пользователя.' });
    return reply(200, { id: data.user.id, email });
  }
  if (body.action === 'set_password') {
    const id = String(body.user_id ?? '');
    if (!/^[0-9a-f-]{36}$/.test(id)) return reply(400, { error: 'Некорректный пользователь.' });
    if (password.length < 8) return reply(400, { error: 'Пароль — не короче 8 символов.' });
    const { error } = await service.auth.admin.updateUserById(id, { password });
    if (error) return reply(400, { error: 'Не удалось сменить пароль.' });
    return reply(200, { ok: true });
  }
  return reply(400, { error: 'Неизвестное действие.' });
});
