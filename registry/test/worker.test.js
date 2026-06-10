import { describe, it, expect, beforeEach } from 'vitest'
import worker from '../worker.js'

function createMockKV() {
  const store = new Map()
  return {
    get: async (key) => store.get(key) ?? null,
    put: async (key, value, opts) => store.set(key, value),
    delete: async (key) => store.delete(key),
    list: async ({ limit } = {}) => ({
      keys: Array.from(store.keys()).slice(0, limit ?? 500).map(k => ({ name: k }))
    })
  }
}

function createFetch(path, opts = {}) {
  const url = `https://bfp-registry.bmo-relay.workers.dev${path}`
  const req = new Request(url, opts)
  return worker.fetch(req, { REGISTRY: createMockKV() })
}

describe('BFP Registry Worker', () => {
  describe('POST /register', () => {
    it('registers a valid agent', async () => {
      const env = { REGISTRY: createMockKV() }
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          did: 'did:bfp:abc123',
          endpoint: 'ws://relay.local:9753',
          caps: ['code', 'chat']
        })
      })
      const res = await worker.fetch(req, env)

      expect(res.status).toBe(200)
      const data = await res.json()
      expect(data.ok).toBe(true)
      expect(data.did).toBe('did:bfp:abc123')
      expect(data.expires_in).toBe(3600)
    })

    it('rejects registration without did', async () => {
      const env = { REGISTRY: createMockKV() }
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: 'ws://relay.local:9753' })
      })
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(400)
      const data = await res.json()
      expect(data.error).toContain('Missing')
    })

    it('rejects registration without endpoint', async () => {
      const env = { REGISTRY: createMockKV() }
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:abc123' })
      })
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(400)
      const data = await res.json()
      expect(data.error).toContain('Missing')
    })

    it('rejects invalid DID format', async () => {
      const env = { REGISTRY: createMockKV() }
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          did: 'invalid-did',
          endpoint: 'ws://relay.local:9753'
        })
      })
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(400)
      const data = await res.json()
      expect(data.error).toContain('DID')
    })

    it('rejects non-WebSocket endpoint', async () => {
      const env = { REGISTRY: createMockKV() }
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          did: 'did:bfp:abc123',
          endpoint: 'http://relay.local:9753'
        })
      })
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(400)
      const data = await res.json()
      expect(data.error).toContain('WebSocket')
    })

    it('rejects invalid JSON body', async () => {
      const env = { REGISTRY: createMockKV() }
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: 'not-json'
      })
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(400)
    })
  })

  describe('POST /unregister', () => {
    it('removes a registered agent', async () => {
      const env = { REGISTRY: createMockKV() }
      // Register first
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:abc123', endpoint: 'ws://relay:9753' })
      }), env)
      // Unregister
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/unregister', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:abc123' })
      }), env)
      expect(res.status).toBe(200)
      const data = await res.json()
      expect(data.ok).toBe(true)
      // Verify it's gone
      const lookupRes = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/lookup?did=did:bfp:abc123'),
        env
      )
      expect(lookupRes.status).toBe(404)
    })

    it('rejects unregister without did', async () => {
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/unregister', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      }), env)
      expect(res.status).toBe(400)
    })
  })

  describe('GET /lookup', () => {
    it('returns agent details by DID', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:abc123', endpoint: 'ws://relay:9753', caps: ['code'] })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/lookup?did=did:bfp:abc123'),
        env
      )
      expect(res.status).toBe(200)
      const data = await res.json()
      expect(data.did).toBe('did:bfp:abc123')
      expect(data.endpoint).toBe('ws://relay:9753')
      expect(data.caps).toEqual(['code'])
    })

    it('returns 404 for unknown DID', async () => {
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/lookup?did=did:bfp:unknown')
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(404)
    })

    it('rejects missing did parameter', async () => {
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/lookup')
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(400)
    })
  })

  describe('GET /list', () => {
    it('returns all registered agents', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:a', endpoint: 'ws://a:9753', caps: ['code'] })
      }), env)
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:b', endpoint: 'ws://b:9753', caps: ['chat'] })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/list'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents.length).toBe(2)
      expect(agents.find(a => a.did === 'did:bfp:a')).toBeTruthy()
      expect(agents.find(a => a.did === 'did:bfp:b')).toBeTruthy()
    })

    it('filters by capability', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:a', endpoint: 'ws://a:9753', caps: ['code'] })
      }), env)
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:b', endpoint: 'ws://b:9753', caps: ['chat'] })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/list?capability=code'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents.length).toBe(1)
      expect(agents[0].did).toBe('did:bfp:a')
    })

    it('returns empty array when no agents', async () => {
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/list'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents).toEqual([])
    })
  })

  describe('GET /health', () => {
    it('returns ok status with online count', async () => {
      const env = { REGISTRY: createMockKV() }
      // Register one agent so online count shows 1
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:a', endpoint: 'ws://a:9753' })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/health'),
        env
      )
      expect(res.status).toBe(200)
      const data = await res.json()
      expect(data.status).toBe('ok')
      expect(data.online).toBe(1)
    })
  })

  describe('CORS headers', () => {
    it('handles OPTIONS preflight', async () => {
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'OPTIONS'
      })
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(200)
      expect(res.headers.get('Access-Control-Allow-Origin')).toBe('*')
    })

    it('includes CORS headers on all responses', async () => {
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/health')
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(req, env)
      expect(res.headers.get('Access-Control-Allow-Origin')).toBe('*')
    })
  })

  // ── Phase 2: Agent Cards ─────────────────────────────────────────────────────
  describe('Agent Cards', () => {
    it('registers with optional agent card fields', async () => {
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          did: 'did:bfp:bmo',
          endpoint: 'ws://relay:9753',
          caps: ['code', 'chat'],
          name: 'BMO',
          description: 'A friendly robot',
          icon: 'https://example.com/bmo.png',
          skills: ['coding', 'chatting']
        })
      }), env)
      expect(res.status).toBe(200)

      const lookup = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/lookup?did=did:bfp:bmo'),
        env
      )
      const data = await lookup.json()
      expect(data.name).toBe('BMO')
      expect(data.description).toBe('A friendly robot')
      expect(data.icon).toBe('https://example.com/bmo.png')
      expect(data.skills).toEqual(['coding', 'chatting'])
    })
  })

  // ── Phase 2: Heartbeat ──────────────────────────────────────────────────────
  describe('POST /heartbeat', () => {
    it('refreshes TTL for a registered agent', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:alive', endpoint: 'ws://relay:9753' })
      }), env)

      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/heartbeat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:alive' })
      }), env)
      expect(res.status).toBe(200)
      const data = await res.json()
      expect(data.ok).toBe(true)
    })

    it('returns 404 for unknown DID', async () => {
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/heartbeat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:ghost' })
      }), env)
      expect(res.status).toBe(404)
    })
  })

  // ── Phase 2: Text Search ────────────────────────────────────────────────────
  describe('GET /search', () => {
    it('finds agents by DID substring', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:bmo', endpoint: 'ws://relay:9753', name: 'BMO' })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search?q=bmo'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents.length).toBe(1)
      expect(agents[0].did).toBe('did:bfp:bmo')
    })

    it('finds agents by name', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:helper', endpoint: 'ws://relay:9753', name: 'Helper Bot' })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search?q=helper'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents.length).toBe(1)
    })

    it('finds agents by capability', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:codey', endpoint: 'ws://relay:9753', caps: ['code'] })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search?q=code'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents.length).toBe(1)
    })

    it('finds agents by skills', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:skilly', endpoint: 'ws://relay:9753', skills: ['python', 'js'] })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search?q=python'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents.length).toBe(1)
    })

    it('finds agents by description', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:descbot', endpoint: 'ws://relay:9753', description: 'A helpful coding assistant' })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search?q=helpful'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents.length).toBe(1)
    })

    it('returns empty array when no match', async () => {
      const env = { REGISTRY: createMockKV() }
      await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:bmo', endpoint: 'ws://relay:9753' })
      }), env)

      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search?q=zzzzz'),
        env
      )
      expect(res.status).toBe(200)
      const agents = await res.json()
      expect(agents).toEqual([])
    })

    it('rejects missing q parameter', async () => {
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search'),
        env
      )
      expect(res.status).toBe(400)
    })
  })

  // ── Phase 2: Auth ───────────────────────────────────────────────────────────
  describe('Authentication', () => {
    const withKey = (headers = {}) => ({ ...headers, 'X-Api-Key': 'test-key-123' })

    it('rejects /register without API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:a', endpoint: 'ws://relay:9753' })
      }), env)
      expect(res.status).toBe(401)
    })

    it('rejects /register with wrong API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Key': 'wrong-key' },
        body: JSON.stringify({ did: 'did:bfp:a', endpoint: 'ws://relay:9753' })
      }), env)
      expect(res.status).toBe(401)
    })

    it('accepts /register with valid API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Key': 'test-key-123' },
        body: JSON.stringify({ did: 'did:bfp:a', endpoint: 'ws://relay:9753' })
      }), env)
      expect(res.status).toBe(200)
    })

    it('accepts /register when no API key configured', async () => {
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:a', endpoint: 'ws://relay:9753' })
      }), env)
      expect(res.status).toBe(200)
    })

    it('rejects /unregister without API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/unregister', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:a' })
      }), env)
      expect(res.status).toBe(401)
    })

    it('rejects /heartbeat without API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(new Request('https://bfp-registry.bmo-relay.workers.dev/heartbeat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ did: 'did:bfp:a' })
      }), env)
      expect(res.status).toBe(401)
    })

    it('allows GET /lookup without API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/lookup?did=did:bfp:a'),
        env
      )
      expect(res.status).toBe(404) // not found but NOT 401
    })

    it('allows GET /list without API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/list'),
        env
      )
      expect(res.status).toBe(200)
    })

    it('allows GET /health without API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/health'),
        env
      )
      expect(res.status).toBe(200)
    })

    it('allows GET /search without API key', async () => {
      const env = { REGISTRY: createMockKV(), REGISTRY_API_KEY: 'test-key-123' }
      const res = await worker.fetch(
        new Request('https://bfp-registry.bmo-relay.workers.dev/search?q=test'),
        env
      )
      expect(res.status).toBe(200) // public endpoint, not 401
    })
  })

  describe('404 handling', () => {
    it('returns 404 for unknown routes', async () => {
      const req = new Request('https://bfp-registry.bmo-relay.workers.dev/unknown')
      const env = { REGISTRY: createMockKV() }
      const res = await worker.fetch(req, env)
      expect(res.status).toBe(404)
    })
  })
})
