"""Try the thirdvistindex approach with HospitalCode."""
import json, base64, hashlib, time, random, aiohttp, asyncio
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
        "TimeStamp": str(ts), "Nonce": nonce, "Signature": signature,
        "vToken": vtoken, "Content-Type": "application/json",
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
        return decrypt_response(await resp.text())

async def main():
    async with aiohttp.ClientSession() as session:
        # First, get the cloud image config
        print("=== GetCloudImageConfigInfoByHospitalCode ===")
        r = await call_api(session, "/ElectronicFilmService/GetCloudImageConfigInfoByHospitalCode", {
            "HospitalCode": "12420000420007938Y"
        })
        print(f"Config: {r}")
        
        # Try GetCloudImageReportInfoByThirdVistParm with various param names
        for label, data in [
            ("HospitalCode", {
                "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
                "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
                "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
                "HospitalCode": "12420000420007938Y",
                "e": "UMR202602271577",
                "p": "P4901903",
                "r": "UMR2026022715773837619",
                "t": "1",
                "key": key_b64,
            }),
            ("hospitalID", {
                "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
                "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
                "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
                "hospitalID": "12420000420007938Y",
                "key": key_b64,
            }),
            ("h as hospitalCode", {
                "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
                "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
                "id": "F43B1F2DAF390E2C36066F060A43489B4CB3E5E2C19C3652E2444D34CF47D38E",
                "hospitalCode": "12420000420007938Y",
                "key": key_b64,
            }),
        ]:
            print(f"\n=== ThirdVistParm with {label} ===")
            r = await call_api(session, "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", data)
            print(f"Result: {r}")

asyncio.run(main())
