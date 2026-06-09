"""Try raw query string and various single-parameter formats."""
import json, base64, hashlib, time, random, aiohttp, asyncio
from datetime import datetime
from Cryptodome.Cipher import DES
from Cryptodome.Util.Padding import pad, unpad

DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])

def make_headers(api_path):
    ts = int(time.time() * 1000)
    nonce = str(random.randint(100000, 999999))
    sig_raw = f"{nonce}{ts}{api_path}{nonce}"
    signature = hashlib.md5(sig_raw.encode()).hexdigest().upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    des_cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
    vtoken = des_cipher.encrypt(pad(now_str.encode('utf-8'), 8)).hex().upper()
    return {"TimeStamp": str(ts), "Nonce": nonce, "Signature": signature, "vToken": vtoken}

def decrypt_response(text):
    try:
        t = text.strip('"')
        padded = t + "=" * (4 - len(t) % 4) if len(t) % 4 else t
        raw = base64.b64decode(padded)
        cipher = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
        return unpad(cipher.decrypt(raw), 8).decode('utf-8')
    except:
        return text[:300]

async def main():
    BASE = "https://xhbi.whuh.com"
    api_path = "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm"
    
    raw_query = ("isShare=43CF7B83C8B9B0EB080C280E4B9D90AB&"
                 "dateTime=ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE&"
                 "id=F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E&"
                 "h=12420000420007938Y&"
                 "e=UMR202602271577&"
                 "p=P4901903&"
                 "r=UMR2026022715773837619&"
                 "t=1&"
                 "key=gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ==")
    
    async with aiohttp.ClientSession() as session:
        # Try passing as a single string parameter
        for param_name in ["parm", "thirdVistParm", "data", "query", "queryString", "url", ""]:
            headers = make_headers(api_path)
            headers["Content-Type"] = "application/json"
            
            if param_name:
                data = {param_name: raw_query}
            else:
                data = raw_query  # Raw string
                headers["Content-Type"] = "text/plain"
            
            async with session.post(f"{BASE}{api_path}", json=data if param_name else None, 
                                    data=raw_query if not param_name else None,
                                    headers=headers) as resp:
                text = await resp.text()
                result = decrypt_response(text)
                label = param_name or "raw string"
                if "医院编号不能为空" not in result:
                    print(f"*** {label}: {result[:200]}")
        
        # Also try with just the key
        print("\n=== Just the key ===")
        headers = make_headers(api_path)
        headers["Content-Type"] = "application/json"
        async with session.post(f"{BASE}{api_path}", 
                                json={"key": "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="},
                                headers=headers) as resp:
            text = await resp.text()
            print(f"Response: {decrypt_response(text)}")
        
        # Maybe the key IS the hospital code
        print("\n=== Key as hospital code ===")
        async with session.post(f"{BASE}{api_path}",
                                json={"h": "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="},
                                headers=headers) as resp:
            text = await resp.text()
            print(f"Response: {decrypt_response(text)}")
        
        print("\nAll variations returned '医院编号不能为空'")

asyncio.run(main())
