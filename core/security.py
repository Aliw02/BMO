import os
import base64
from cryptography.fernet import Fernet
from config.settings import DATA_DIR

KEY_FILE = DATA_DIR / ".master.key"

def _get_or_create_master_key():
    """Retrieves existing master key or creates a new one."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read()
    else:
        # Generate a new random key
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        
        # Try to make the file hidden on Windows
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(KEY_FILE), 0x02) # FILE_ATTRIBUTE_HIDDEN
        except:
            pass
            
        return key

_MASTER_KEY = _get_or_create_master_key()
_FERNET = Fernet(_MASTER_KEY)

def encrypt_value(value: str) -> str:
    """Encrypts a string value."""
    if not value: return ""
    return _FERNET.encrypt(value.encode()).decode()

def decrypt_value(encrypted_value: str) -> str:
    """Decrypts an encrypted string value."""
    if not encrypted_value: return ""
    try:
        return _FERNET.decrypt(encrypted_value.encode()).decode()
    except Exception:
        return "" # Return empty if decryption fails (e.g. wrong key)
