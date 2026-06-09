"""Test SM4 decryption - key might be raw string."""
import json, base64
from sm4 import SM4Key

sm4_key_str = "jEeT04X1yLKK4NBVsqSwgvxkgURV645U"
key_raw = sm4_key_str.encode('utf-8')
print(f"Key UTF-8 ({len(key_raw)} bytes)")

# Truncate to 16 bytes
key16 = key_raw[:16]
print(f"Key 16 bytes: {key16.hex()}")

sm4 = SM4Key(key16)

# Test hex params
for name, val in [
    ("isShare", "43CF7B83C8B9B0EB080C280E4B9D90AB"),
    ("dateTime", "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE"),
    ("id", "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E"),
]:
    raw = bytes.fromhex(val)
    result = b""
    for i in range(0, len(raw), 16):
        result += sm4.decrypt(raw[i:i+16])
    print(f"\n{name}: {result}")
    for encoding in ['utf-8', 'gbk', 'ascii']:
        try:
            print(f"  {encoding}: {result.decode(encoding)}")
            break
        except:
            pass

# Test key parameter
key_b64 = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="
raw_key = base64.b64decode(key_b64)
print(f"\nkey parameter ({len(raw_key)} bytes)")
result = b""
for i in range(0, len(raw_key), 16):
    result += sm4.decrypt(raw_key[i:i+16])
print(f"key decrypted: {result}")
for encoding in ['utf-8', 'gbk', 'ascii']:
    try:
        print(f"  {encoding}: {result.decode(encoding)}")
        break
    except:
        pass
