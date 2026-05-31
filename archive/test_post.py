import httpx
import json

def test():
    urls = [
        "http://127.0.0.1:4096/api/chat",
        "http://127.0.0.1:4096/api/generate",
        "http://127.0.0.1:4096/v1/chat/completions"
    ]
    payload = {
        "model": "default",
        "messages": [{"role": "user", "content": "hi"}],
        "prompt": "hi"
    }
    
    for url in urls:
        try:
            print(f"Trying POST {url}...")
            r = httpx.post(url, json=payload, timeout=5)
            print(f"Status: {r.status_code}")
            print(f"Response: {r.text[:200]}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test()
