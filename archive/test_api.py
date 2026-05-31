import httpx

def test():
    urls = [
        "http://127.0.0.1:4096/v1/models",
        "http://127.0.0.1:4096/api/version",
        "http://127.0.0.1:4096/session",
        "http://127.0.0.1:4096/api/session"
    ]
    for url in urls:
        try:
            r = httpx.get(url, timeout=2)
            print(f"{url} -> {r.status_code} {r.headers.get('content-type', '')} {r.text[:50]}")
        except Exception as e:
            print(f"{url} -> Error: {e}")

if __name__ == "__main__":
    test()
