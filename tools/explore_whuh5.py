"""Decrypt the API response."""
import json
import base64
import aiohttp
import asyncio
import hashlib
import time
import random
from datetime import datetime
from Cryptodome.Cipher import DES, AES
from Cryptodome.Util.Padding import pad, unpad

DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])
AES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1, 8, 7, 6, 9, 4, 3, 2, 1])
AES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8, 1, 2, 3, 4, 9, 6, 7, 8])

response_str = "TUyV9BYW29ON4xF3+2dSqa0dNCuiKT4jhsslGSaDqqFC+Dta4TpsGJZ1I4AE5UsGFZ05w2v6GSq1StBLSO+6aePwA1XZ3aju"

print("=== Decrypting response ===")
# Add padding if needed
padded = response_str + "=" * (4 - len(response_str) % 4) if len(response_str) % 4 else response_str
print(f"Padded: {padded}")
raw = base64.b64decode(padded)
print(f"Raw bytes length: {len(raw)}")

# Try DES
try:
    cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
    result = unpad(cipher.decrypt(raw), 8)
    print(f"DES decrypt: {result}")
except Exception as e:
    print(f"DES failed: {e}")

# Try AES
try:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
    result = unpad(cipher.decrypt(raw), 16)
    print(f"AES decrypt: {result}")
except Exception as e:
    print(f"AES failed: {e}")

# Try the 'd' function approach: hex string -> base64 -> AES
try:
    # The response might be hex
    hex_parsed = bytes.fromhex(response_str)
    b64_from_hex = base64.b64encode(hex_parsed).decode()
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
    result = unpad(cipher.decrypt(base64.b64decode(b64_from_hex)), 16)
    print(f"Via d-function (hex->b64->AES): {result}")
except Exception as e:
    print(f"Via d-function failed: {e}")

# Try 'b' function: base64 -> DES
# The b function takes a base64 string and decrypts with DES
# But waits, the b function in the JS (named l) takes a base64 string directly
# Let me make sure I'm using the right key/IV
# From the JS:
# o = Utf8.parse("\b\x07\x06\t\x04\x03\x02\x01") = DES key
# i = Utf8.parse("\x01\x02\x03\x04\t\x06\x07\b...") = DES IV (32 bytes)
# In CryptoJS, DES uses 8-byte IV, so it takes first 8 bytes
print("\n=== Trying more combinations ===")

# Maybe the encrypted data is preceded by some header bytes?
# Skip first 8 bytes?
for skip in [0, 8, 16]:
    data = raw[skip:]
    print(f"\nSkip {skip} bytes, data len: {len(data)}")
    for cipher_class, key, iv, block_size, name in [
        (DES, DES_KEY, DES_IV, 8, "DES"),
        (AES, AES_KEY, AES_IV, 16, "AES"),
    ]:
        if len(data) % block_size == 0:
            try:
                cipher = cipher_class.new(key, cipher_class.MODE_CBC, iv=iv)
                result = unpad(cipher.decrypt(data), block_size)
                print(f"  {name} decrypt: {result}")
            except Exception as e:
                pass
