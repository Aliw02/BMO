import httpx
import json

def fetch_openapi():
    urls = [
        "http://127.0.0.1:4096/openapi.json",
        "http://127.0.0.1:4096/api/openapi.json",
        "http://127.0.0.1:4096/docs/openapi.json",
    ]
    for url in urls:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code == 200:
                try:
                    data = r.json()
                    with open("openapi_dump.json", "w") as f:
                        json.dump(data, f, indent=2)
                    print(f"Saved OpenAPI spec from {url}")
                    return
                except ValueError:
                    pass
        except Exception:
            pass
    print("Could not find OpenAPI spec.")

if __name__ == "__main__":
    fetch_openapi()
