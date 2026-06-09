"""Try to decrypt the key with different approaches and try thirdvist API."""
import json
import base64
import hashlib
import time
import random
import aiohttp
import asyncio
from datetime import datetime
from Cryptodome.Cipher import DES, AES
from Cryptodome.Util.Padding import pad, unpad

DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])
AES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1, 8, 7, 6, 9, 4, 3, 2, 1])
AES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8, 1, 2, 3, 4, 9, 6, 7, 8])

key_b64 = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="

# Try DES decrypt without unpadding
raw = base64.b64decode(key_b64)
print(f"Key raw bytes ({len(raw)}): {raw.hex()}")

cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
decrypted = cipher.decrypt(raw)
print(f"DES raw decrypt: {decrypted.hex()}")
print(f"DES raw decrypt as text: {decrypted}")

# Try AES
try:
    cipher2 = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
    decrypted2 = cipher2.decrypt(raw)
    print(f"AES raw decrypt: {decrypted2.hex()}")
except Exception as e:
    print(f"AES raw decrypt failed: {e}")

# Maybe the key is a token that should be sent as-is
# Let's try passing all the raw URL parameters to the API

# Reconstruct the URL query string
full_url = ("https://xhbi.whuh.com/index.html#/reportView?"
    "isShare=43CF7B83C8B9B0EB080C280E4B9D90AB&"
    "dateTime=ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE&"
    "id=F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E&"
    "h=12420000420007938Y&"
    "e=UMR202602271577&"
    "p=P4901903&"
    "r=UMR2026022715773837619&"
    "t=1&"
    "key=gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ==")

def make_headers(api_path):
    ts = int(time.time() * 1000)
    nonce = str(random.randint(100000, 999999))
    sig_raw = f"{nonce}{ts}{api_path}{nonce}"
    signature = hashlib.md5(sig_raw.encode()).hexdigest().upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    des_cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
    vtoken = des_cipher.encrypt(pad(now_str.encode('utf-8'), 8)).hex().upper()
    return {
        "TimeStamp": str(ts),
        "Nonce": nonce,
        "Signature": signature,
        "vToken": vtoken,
        "Content-Type": "application/json",
    }

async def call_api(session, path, data):
    headers = make_headers(path)
    async with session.post(f"https://xhbi.whuh.com{path}", json=data, headers=headers) as resp:
        text = await resp.text()
        # Try DES decrypt
        try:
            padded = text.strip('"') + "=" * (4 - len(text.strip('"')) % 4) if len(text.strip('"')) % 4 else text.strip('"')
            raw_resp = base64.b64decode(padded)
            cipher_resp = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
            decrypted = unpad(cipher_resp.decrypt(raw_resp), 8)
            print(f"Decrypted: {decrypted.decode('utf-8')}")
        except:
            print(f"Raw: {text[:500]}")
        return text

async def main():
    async with aiohttp.ClientSession() as session:
        # Try GetCloudImageReportInfoByThirdVistParm with full URL-like params
        print("=== Test 1: Full params ===")
        await call_api(session, "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", {
            "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
            "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
            "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
            "h": "12420000420007938Y",
            "e": "UMR202602271577",
            "p": "P4901903",
            "r": "UMR2026022715773837619",
            "t": "1",
            "key": key_b64,
        })
        
        # Try with hospitalCode instead of h
        print("\n=== Test 2: hospitalCode ===")
        await call_api(session, "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", {
            "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
            "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
            "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
            "hospitalCode": "12420000420007938Y",
            "key": key_b64,
        })
        
        # Try GetCloudImageReportInfoByID with the decrypted id
        print("\n=== Test 3: GetCloudImageReportInfoByID ===")
        await call_api(session, "/ElectronicFilmService/GetCloudImageReportInfoByID", {
            "id": "0001_100012429927",
        })
        
        # Try CloudImageSecurityCodeLogin with key as securityCode
        print("\n=== Test 4: CloudImageSecurityCodeLogin ===")
        await call_api(session, "/ElectronicFilmService/CloudImageSecurityCodeLogin", {
            "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
            "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
            "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
            "securityCode": "",
            "key": key_b64,
        })
        
        # Try Login (not security code, the IMService/login)
        print("\n=== Test 5: IMService/login ===")
        await call_api(session, "/IMService/login", {
            "key": key_b64,
        })

asyncio.run(main())
