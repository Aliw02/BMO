import httpx, json

r = httpx.get('http://127.0.0.1:4096/provider', timeout=5)
data = r.json()
all_providers = data.get('all', [])
connected = data.get('connected', [])

known = ['anthropic', 'openai', 'google', 'mistral', 'groq', 'xai', 'deepseek', 'openrouter', 'azure', 'cohere', 'ollama']
print('=== POPULAR PROVIDERS & ENV KEYS ===')
for p in all_providers:
    if p['id'] in known:
        env = p.get('env', [])
        is_connected = p['id'] in connected
        models = list(p.get('models', {}).keys())[:3]
        print(f"{p['id']:20s} | connected: {str(is_connected):5s} | env: {env} | sample models: {models}")
