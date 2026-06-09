"""Explore the xhbi.whuh.com API to understand the data flow."""
import json
import base64
from Cryptodome.Cipher import DES, AES
from Cryptodome.Util.Padding import pad, unpad

# Keys extracted from the JS bundle (module 77db)
DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])
AES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1, 8, 7, 6, 9, 4, 3, 2, 1])
AES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8, 1, 2, 3, 4, 9, 6, 7, 8])


def decrypt_des_base64(b64_str: str) -> str:
    cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
    raw = base64.b64decode(b64_str)
    return unpad(cipher.decrypt(raw), 8).decode('utf-8')


def decrypt_aes_base64(b64_str: str) -> str:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
    raw = base64.b64decode(b64_str)
    return unpad(cipher.decrypt(raw), 16).decode('utf-8')


def decrypt_aes_hex(hex_str: str) -> str:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
    raw = bytes.fromhex(hex_str)
    return unpad(cipher.decrypt(raw), 16).decode('utf-8')


key = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="

print("=== Trying to decrypt 'key' parameter ===")
for name, fn in [("DES", decrypt_des_base64), ("AES", decrypt_aes_base64)]:
    try:
        result = fn(key)
        print(f"{name} decrypt: {result}")
    except Exception as e:
        print(f"{name} decrypt failed: {e}")

print()
for name, val in [("isShare", "43CF7B83C8B9B0EB080C280E4B9D90AB"),
                   ("dateTime", "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE"),
                   ("id", "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E")]:
    try:
        result = decrypt_aes_hex(val)
        print(f"{name}: {result}")
    except Exception as e:
        print(f"{name} decrypt failed: {e}")

# Now try the API
import aiohttp
import asyncio


async def main():
    async with aiohttp.ClientSession() as session:
        BASE = "https://xhbi.whuh.com"
        url_params = {
            "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
            "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
            "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
            "h": "12420000420007938Y",
            "e": "UMR202602271577",
            "p": "P4901903",
            "r": "UMR2026022715773837619",
            "t": "1",
            "key": key,
        }

        print("\n=== Trying GetCloudImageReportInfoByThirdVistParm ===")
        async with session.post(f"{BASE}/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", json=url_params) as resp:
            print(f"Status: {resp.status}")
            text = await resp.text()
            print(f"Response: {text[:1000]}")
            try:
                data = json.loads(text)
                print(f"JSON: {json.dumps(data, indent=2, ensure_ascii=False)[:2000]}")
            except:
                pass

asyncio.run(main())
