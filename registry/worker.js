/**
 * BFP Registry — Cloudflare Worker
 * Deploy: wrangler deploy
 * URL:    https://bfp-registry.aliwey.workers.dev
 *
 * KV namespace: REGISTRY (binding name)
 * Stores: DID → { endpoint, caps, name, description, icon, skills, ts }
 * Auto-expiry: 1 hour (refreshed every 30min by bmo relay)
 */

function authenticate(req, env) {
  const key = env.REGISTRY_API_KEY;
  if (!key) return null;
  const header = req.headers.get('X-Api-Key');
  if (header !== key) return 'Invalid or missing API key';
  return null;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const { pathname } = url;

    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, X-Api-Key',
    };

    if (req.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    const json = (data, status = 200) =>
      Response.json(data, { status, headers: corsHeaders });

    const err = (msg, status = 400) =>
      json({ error: msg }, status);

    const unauth = () => err('Invalid or missing API key', 401);

    // ── POST /register ────────────────────────────────────────────────────────
    // Body: { did, endpoint, caps?, name?, description?, icon?, skills? }
    if (req.method === 'POST' && pathname === '/register') {
      const authErr = authenticate(req, env);
      if (authErr) return unauth();

      let body;
      try { body = await req.json(); } catch { return err('Invalid JSON'); }

      const { did, endpoint, caps, name, description, icon, skills } = body;
      if (!did || !endpoint) return err('Missing did or endpoint');
      if (!did.startsWith('did:bfp:')) return err('Invalid DID format');
      if (!endpoint.startsWith('ws')) return err('Endpoint must be a WebSocket URL');

      const record = {
        endpoint,
        caps: caps || [],
        ts: Date.now(),
      };
      if (name) record.name = name;
      if (description) record.description = description;
      if (icon) record.icon = icon;
      if (skills) record.skills = skills;

      await env.REGISTRY.put(did, JSON.stringify(record), { expirationTtl: 3600 });

      return json({ ok: true, did, expires_in: 3600 });
    }

    // ── POST /unregister ──────────────────────────────────────────────────────
    if (req.method === 'POST' && pathname === '/unregister') {
      const authErr = authenticate(req, env);
      if (authErr) return unauth();

      let body;
      try { body = await req.json(); } catch { return err('Invalid JSON'); }

      const { did } = body;
      if (!did) return err('Missing did');

      await env.REGISTRY.delete(did);
      return json({ ok: true });
    }

    // ── POST /heartbeat ────────────────────────────────────────────────────────
    // Body: { did: string }
    // Resets TTL to 3600s for the registered agent
    if (req.method === 'POST' && pathname === '/heartbeat') {
      const authErr = authenticate(req, env);
      if (authErr) return unauth();

      let body;
      try { body = await req.json(); } catch { return err('Invalid JSON'); }

      const { did } = body;
      if (!did) return err('Missing did');

      const val = await env.REGISTRY.get(did);
      if (!val) return json({ error: 'Agent not found or offline' }, 404);

      // Re-put with updated timestamp to reset TTL
      const record = JSON.parse(val);
      record.ts = Date.now();
      await env.REGISTRY.put(did, JSON.stringify(record), { expirationTtl: 3600 });

      return json({ ok: true, did, expires_in: 3600 });
    }

    // ── GET /lookup?did=xxx ───────────────────────────────────────────────────
    if (req.method === 'GET' && pathname === '/lookup') {
      const did = url.searchParams.get('did');
      if (!did) return err('Missing did parameter');

      const val = await env.REGISTRY.get(did);
      if (!val) return json({ error: 'Agent not found or offline' }, 404);

      return json({ did, ...JSON.parse(val) });
    }

    // ── GET /list?capability=code ─────────────────────────────────────────────
    if (req.method === 'GET' && pathname === '/list') {
      const cap = url.searchParams.get('capability');

      const { keys } = await env.REGISTRY.list({ limit: 500 });
      const agents = [];

      await Promise.all(keys.map(async key => {
        const val = await env.REGISTRY.get(key.name);
        if (!val) return;
        const data = JSON.parse(val);
        if (!cap || (data.caps && data.caps.includes(cap))) {
          agents.push({ did: key.name, ...data });
        }
      }));

      agents.sort((a, b) => (b.ts || 0) - (a.ts || 0));

      return json(agents);
    }

    // ── GET /search?q=text ────────────────────────────────────────────────────
    // Searches did, name, description, caps[], skills[] (case-insensitive)
    if (req.method === 'GET' && pathname === '/search') {
      const q = url.searchParams.get('q');
      if (!q) return err('Missing q parameter');

      const query = q.toLowerCase();

      const { keys } = await env.REGISTRY.list({ limit: 500 });
      const agents = [];

      await Promise.all(keys.map(async key => {
        const val = await env.REGISTRY.get(key.name);
        if (!val) return;
        const data = JSON.parse(val);

        const did = key.name.toLowerCase();
        const name = (data.name || '').toLowerCase();
        const desc = (data.description || '').toLowerCase();
        const caps = (data.caps || []).map(c => c.toLowerCase());
        const skills = (data.skills || []).map(s => s.toLowerCase());

        const match =
          did.includes(query) ||
          name.includes(query) ||
          desc.includes(query) ||
          caps.some(c => c.includes(query)) ||
          skills.some(s => s.includes(query));

        if (match) {
          agents.push({ did: key.name, ...data });
        }
      }));

      agents.sort((a, b) => (b.ts || 0) - (a.ts || 0));

      return json(agents);
    }

    // ── GET /health ───────────────────────────────────────────────────────────
    if (req.method === 'GET' && pathname === '/health') {
      const { keys } = await env.REGISTRY.list({ limit: 1 });
      return json({ status: 'ok', online: keys.length });
    }

    return json({ error: 'Not found' }, 404);
  }
};
