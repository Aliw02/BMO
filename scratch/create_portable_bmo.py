import os
import shutil
import subprocess
import urllib.request
import zipfile

PROJECT_ROOT = r"f:\Programming\ProgrammingWithPython\OpenCodeTes.zip"
DIST_DIR = os.path.join(PROJECT_ROOT, "BMO_Portable")
PYTHON_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

def prepare():
    # 1. Create clean dist directory
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    os.makedirs(DIST_DIR)
    
    print(f"--- Creating BMO Portable in {DIST_DIR} ---")
    
    # 2. Copy Project Structure (Automated & Clean)
    # Note: 'storage' must be copied because it contains storage.py (code)
    # Note: 'data' is just for generated files, we create it empty.
    exclude_folders = ['BMO_Portable', 'venv', '.git', '__pycache__', '.idea', '.vscode', 'data', 'scratch']
    
    # First, create base structure
    os.makedirs(os.path.join(DIST_DIR, 'data'), exist_ok=True)
    print("Created empty private folder: data")

    # Copy all other folders that contain code (including storage)
    for item in os.listdir(PROJECT_ROOT):
        src = os.path.join(PROJECT_ROOT, item)
        dst = os.path.join(DIST_DIR, item)
        
        if os.path.isdir(src) and item not in exclude_folders:
            shutil.copytree(src, dst)
            print(f"Copied folder: {item}")
            
    # Remove any DB or LOG files (Crucial for Privacy)
    for root, dirs, files in os.walk(DIST_DIR):
        for file in files:
            if file.endswith(('.db', '.log', '.pyc')):
                os.remove(os.path.join(root, file))
                print(f"Removed private/temp file: {file}")

    files_to_copy = ['main.py', 'requirements.txt']
    for f in files_to_copy:
        src = os.path.join(PROJECT_ROOT, f)
        dst = os.path.join(DIST_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            
    # Create .env.example
    with open(os.path.join(DIST_DIR, ".env.example"), "w") as f:
        f.write("TELEGRAM_BOT_TOKEN=your_token_here\n")
        f.write("OPENCODE_SERVER_URL=http://127.0.0.1:4096\n")

    # 3. Download Portable Python (One-time)
    py_zip = os.path.join(DIST_DIR, "python_portable.zip")
    py_dir = os.path.join(DIST_DIR, "python_runtime")
    os.makedirs(py_dir, exist_ok=True)
    
    print("Downloading Portable Python (this may take a minute)...")
    try:
        urllib.request.urlretrieve(PYTHON_URL, py_zip)
        with zipfile.ZipFile(py_zip, 'r') as zip_ref:
            zip_ref.extractall(py_dir)
        os.remove(py_zip)
        print("✅ Portable Python ready.")
    except Exception as e:
        print(f"❌ Failed to download Python: {e}")

    # 4. Enable site-packages in Portable Python (CRITICAL)
    pth_file = os.path.join(py_dir, "python311._pth")
    if os.path.exists(pth_file):
        with open(pth_file, "r") as f:
            lines = f.readlines()
        with open(pth_file, "w") as f:
            for line in lines:
                # Uncomment 'import site'
                if "import site" in line:
                    f.write("import site\n")
                else:
                    f.write(line)
        print("✅ Enabled site-packages in python311._pth")

    # 5. Download Cloudflared
    print("Downloading Cloudflared tool...")
    try:
        cf_path = os.path.join(DIST_DIR, "cloudflared.exe")
        urllib.request.urlretrieve(CLOUDFLARED_URL, cf_path)
        print("✅ Cloudflared ready.")
    except Exception as e:
        print(f"❌ Failed to download Cloudflared: {e}")

    # 6. Create the "Magic" Starter Batch File (with GUI Setup & Smart Check)
    batch_content = """@echo off
title BMO - Autonomous Telegram Agent
setlocal enabledelayedexpansion

cd /d "%~dp0"
set PYTHONPATH=%cd%

:: Kill any existing BMO instances running from this specific folder
echo [BMO] Checking for existing instances...
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*%cd%*' } | Stop-Process -Force" >nul 2>&1

:: ── GUI SETUP FOR FIRST RUN ──
if not exist .env (
    echo [BMO] Initializing Setup Wizard...
    
    :: Ask for Token using PS Popup
    powershell -Command "[System.Reflection.Assembly]::LoadWithPartialName('Microsoft.VisualBasic') > $null; $token = [Microsoft.VisualBasic.Interaction]::InputBox('Welcome to BMO! 🦾`n`nPlease enter your Telegram Bot Token:', 'BMO Setup Wizard', ''); if($token) { echo $token } else { exit 1 }" > .token_tmp
    if %ERRORLEVEL% NEQ 0 (
        echo [BMO] Setup cancelled.
        del .token_tmp >nul 2>&1
        pause
        exit /b
    )
    set /p BMO_TOKEN=<.token_tmp
    del .token_tmp >nul 2>&1

    :: Ask for Name using PS Popup
    powershell -Command "[System.Reflection.Assembly]::LoadWithPartialName('Microsoft.VisualBasic') > $null; $name = [Microsoft.VisualBasic.Interaction]::InputBox('How should BMO call you?', 'BMO Customization', 'Friend'); if($name) { echo $name } else { echo 'Friend' }" > .name_tmp
    set /p BMO_NAME=<.name_tmp
    del .name_tmp >nul 2>&1

    :: Create .env
    echo TELEGRAM_BOT_TOKEN=!BMO_TOKEN! > .env
    echo OPENCODE_SERVER_URL=http://127.0.0.1:4096 >> .env
    echo BMO_INITIAL_NAME=!BMO_NAME! >> .env
    
    cls
    echo [BMO] ✅ Setup Complete! Configuration saved.
)

:: Smart OpenCode Server Check
echo [BMO] Checking OpenCode Server status...
powershell -Command "$TCP = New-Object System.Net.Sockets.TcpClient; $Connect = $TCP.BeginConnect('127.0.0.1', 4096, $null, $null); $Wait = $Connect.AsyncWaitHandle.WaitOne(1000, $false); if($Wait) { exit 0 } else { exit 1 }" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [BMO] OpenCode Server NOT detected at 127.0.0.1:4096
    echo [BMO] Attempting to wake up OpenCode...
    where opencode >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [BMO] Starting OpenCode server in background...
        start /min cmd /c "opencode"
        timeout /t 5 >nul
    ) else (
        echo [BMO] ⚠️ OpenCode command not found. Please ensure it is running.
    )
)

:: Check for pip/requirements
if not exist python_runtime\\Lib\\site-packages\\telegram (
    echo [BMO] Waking up BMO's brain... (First time setup)
    if not exist get-pip.py (
        curl -s https://bootstrap.pypa.io/get-pip.py -o get-pip.py
    )
    python_runtime\\python.exe get-pip.py --quiet --no-warn-script-location
    python_runtime\\python.exe -m pip install --quiet -r requirements.txt
    if exist get-pip.py del get-pip.py
)

cls
echo [BMO] BMO is online and ready.
python_runtime\\python.exe main.py
if %ERRORLEVEL% NEQ 0 (
    echo [BMO] Something went wrong. Restarting in 5 seconds...
    timeout /t 5
    goto :eof
)
pause
"""
    with open(os.path.join(DIST_DIR, "Run_BMO.bat"), "w") as f:
        f.write(batch_content)

    print("\n" + "="*40)
    print("🎉 BMO PORTAL IS READY!")
    print(f"Location: {DIST_DIR}")
    print("Instruction: Just ZIP this folder and send it to your friend.")
    print("="*40)

if __name__ == "__main__":
    prepare()
