"""
Launch a Cloudflare tunnel for the BMO webchat server (port 3456).
CLI version — runs as a standalone script.

Usage:
  python scratch/launch_tunnel.py          # tunnels webchat (port 3456)
  python scratch/launch_tunnel.py --port 8080  # custom port
"""

import subprocess
import os
import time
import re
import sys
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def run(port=3456, timeout=60):
    log_file = os.path.join(PROJECT_ROOT, "tunnel_cli.log")

    print(f"Starting cloudflared tunnel for localhost:{port}...")

    # Check if cloudflared is available
    try:
        subprocess.run(
            ["cloudflared", "--version"],
            capture_output=True, timeout=5, check=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("ERROR: cloudflared is not installed or not in PATH.")
        print("Install it: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)

    # Start tunnel — redirect output to log file
    cmd = f"cloudflared tunnel --url http://localhost:{port}"
    print(f"Running: {cmd}")

    with open(log_file, "w", encoding="utf-8") as f:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            stdout=f,
            stderr=f,
        )

    print(f"Tunnel process started (PID: {proc.pid}). Waiting for URL...")

    # Poll log file every 2 seconds until URL appears or timeout
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(2)
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                if match:
                    url = match.group(0)
                    print(f"\n✅ SUCCESS: {url}")
                    url_file = os.path.join(PROJECT_ROOT, "tunnel_url.txt")
                    with open(url_file, "w") as uf:
                        uf.write(url)
                    print(f"URL saved to: {url_file}")
                    return url

    print(f"\n⚠️ Timeout after {timeout}s. The tunnel may still be starting.")
    print(f"Check {log_file} for the URL.")
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Launch a cloudflared tunnel for BMO webchat")
    parser.add_argument("--port", type=int, default=3456, help="Local port to tunnel (default: 3456)")
    parser.add_argument("--timeout", type=int, default=60, help="Max seconds to wait for URL (default: 60)")
    args = parser.parse_args()
    run(port=args.port, timeout=args.timeout)
