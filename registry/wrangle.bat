@echo off
REM Load CLOUDFLARE_API_TOKEN from .env and run wrangler
for /f "usebackq delims=" %%i in ("%~dp0.env") do set %%i
npx wrangler %*
