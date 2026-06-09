"""Try with the required auth headers."""
import json
import hashlib
import time
import random
import aiohttp
import asyncio
from Cryptodome.Cipher import DES
from Cryptodome.Util.Padding import pad

# From JS module 77db: encrypt current datetime for vToken
DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])

def encrypt_des(text: str) -> str:
    cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
    return cipher.encrypt(pad(text.encode('utf-8'), 8)).hex().upper()

async def main():
    BASE = "https://xhbi.whuh.com"
    key_b64 = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="
    
    url_params = {
        "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
        "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
        "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
        "h": "12420000420007938Y",
        "e": "UMR202602271577",
        "p": "P4901903",
        "r": "UMR2026022715773837619",
        "t": "1",
        "key": key_b64,
    }
    
    # Generate headers like the JS interceptor
    ts = int(time.time() * 1000)
    nonce = str(random.randint(100000, 999999))
    api_path = "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm"
    # Signature = MD5(nonce + timestamp + path_without_api + nonce).upper()
    # The path is already without /api prefix
    sig_raw = f"{nonce}{ts}{api_path}{nonce}"
    signature = hashlib.md5(sig_raw.encode()).hexdigest().upper()
    
    # vToken = encrypt current datetime
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    vtoken = encrypt_des(now_str)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/143.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh,zh-CN;q=0.7,en;q=0.3",
        "TimeStamp": str(ts),
        "Nonce": nonce,
        "Signature": signature,
        "vToken": vtoken,
        "Content-Type": "application/json",
    }
    
    print(f"Headers: {json.dumps(headers, indent=2)}")
    
    async with aiohttp.ClientSession(headers=headers) as session:
        print("\n=== GetCloudImageReportInfoByThirdVistParm with auth headers ===")
        async with session.post(f"{BASE}{api_path}", json=url_params) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}")
            print(f"Response: {text[:1000]}")
        
        # Also try with 'key' only
        print("\n=== GetCloudImageReportInfoByThirdVistParm with key only ===")
        ts2 = int(time.time() * 1000)
        nonce2 = str(random.randint(100000, 999999))
        sig2 = hashlib.md5(f"{nonce2}{ts2}{api_path}{nonce2}".encode()).hexdigest().upper()
        now_str2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        vtoken2 = encrypt_des(now_str2)
        
        headers2 = {**headers, "TimeStamp": str(ts2), "Nonce": nonce2, "Signature": sig2, "vToken": vtoken2}
        async with session.post(f"{BASE}{api_path}", json={"key": key_b64}, headers=headers2) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}")
            print(f"Response: {text[:1000]}")

asyncio.run(main())
