/**
 * BFP Registry — Cloudflare Worker
 * Deploy: wrangler deploy
 * URL:    https://bfp-registry.aliwey.workers.dev
 *
 * KV namespace: REGISTRY (binding name)
 * Stores: DID → { endpoint, caps, ts }
 * Auto-expiry: 1 hour (refreshed every 30min by bmo relay)
 */

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const { pathname } = url;

    // CORS headers for all responses
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (req.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    const json = (data, status = 200) =>
      Response.json(data, { status, headers: corsHeaders });

    const err = (msg, status = 400) =>
      json({ error: msg }, status);

    // ── POST /register ────────────────────────────────────────────────────────
    // Body: { did: string, endpoint: string, caps: string[] }
    if (req.method === 'POST' && pathname === '/register') {
      let body;
      try { body = await req.json(); } catch { return err('Invalid JSON'); }

      const { did, endpoint, caps } = body;
      if (!did || !endpoint) return err('Missing did or endpoint');
      if (!did.startsWith('did:bfp:')) return err('Invalid DID format');
      if (!endpoint.startsWith('ws')) return err('Endpoint must be a WebSocket URL');

      await env.REGISTRY.put(
        did,
        JSON.stringify({ endpoint, caps: caps || [], ts: Date.now() }),
        { expirationTtl: 3600 }   // auto-expire in 1 hour
      );

      return json({ ok: true, did, expires_in: 3600 });
    }

    // ── POST /unregister ──────────────────────────────────────────────────────
    // Body: { did: string }
    if (req.method === 'POST' && pathname === '/unregister') {
      let body;
      try { body = await req.json(); } catch { return err('Invalid JSON'); }

      const { did } = body;
      if (!did) return err('Missing did');

      await env.REGISTRY.delete(did);
      return json({ ok: true });
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
    // Returns all online agents, optionally filtered by capability
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

      // Sort by most recently registered
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
