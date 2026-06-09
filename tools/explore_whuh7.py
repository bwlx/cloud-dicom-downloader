"""Try different parameter formats for ThirdVistParm API."""
import json
import base64
import hashlib
import time
import random
import aiohttp
import asyncio
from datetime import datetime
from Cryptodome.Cipher import DES
from Cryptodome.Util.Padding import pad, unpad

DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])

key_b64 = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="

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

def decrypt_response(text):
    try:
        t = text.strip('"')
        padded = t + "=" * (4 - len(t) % 4) if len(t) % 4 else t
        raw = base64.b64decode(padded)
        cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
        return unpad(cipher.decrypt(raw), 8).decode('utf-8')
    except:
        return text[:500]

async def call_api(session, path, data):
    headers = make_headers(path)
    async with session.post(f"https://xhbi.whuh.com{path}", json=data, headers=headers) as resp:
        text = await resp.text()
        return decrypt_response(text)

async def main():
    BASE = "https://xhbi.whuh.com"
    url_query = ("isShare=43CF7B83C8B9B0EB080C280E4B9D90AB&"
                 "dateTime=ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE&"
                 "id=F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E&"
                 "h=12420000420007938Y&"
                 "e=UMR202602271577&"
                 "p=P4901903&"
                 "r=UMR2026022715773837619&"
                 "t=1&"
                 "key=gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ==")
    
    async with aiohttp.ClientSession() as session:
        # Try passing the full query as a single "parm" 
        print("=== Test: thirdVistParm as single string ===")
        r = await call_api(session, "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", {
            "thirdVistParm": url_query
        })
        print(f"Result: {r}")
        
        # Try passing as a dictionary with the URL params decoded
        print("\n=== Test: with thirdVistParm dict ===")
        r = await call_api(session, "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", {
            "thirdVistParm": {
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
        })
        print(f"Result: {r}")
        
        # Try the URL parameters as form data instead of JSON
        print("\n=== Test: as form data ===")
        headers = make_headers("/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        async with session.post(f"{BASE}/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", 
                                data=url_query, headers=headers) as resp:
            text = await resp.text()
            print(f"Result: {decrypt_response(text)}")
        
        # Let's also try the chunk for thirdvistindex
        print("\n=== Fetching thirdvistindex chunk ===")
        async with session.get(f"{BASE}/js/chunk-ed6f84a.js") as resp:
            text = await resp.text()
            # Look for API calls
            import re
            apis = re.findall(r'url:"([^"]+)"', text)
            params = re.findall(r'(\w+):\w+\.(\w+)', text)
            print(f"API calls: {apis}")
            # Also search for the function body
            post_calls = re.findall(r'\.post\([^)]+\)', text)
            print(f"Post calls: {post_calls[:5]}")

asyncio.run(main())
