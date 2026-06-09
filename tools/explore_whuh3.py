"""Try with WeChat user agent and explore login flow."""
import json
import aiohttp
import asyncio

WECHAT_UA = "Mozilla/5.0 (Linux; Android 10; SM-G9750 Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/80.0.3987.149 Mobile Safari/537.36 MicroMessenger/8.0.32.2600(0x28002000) NetType/WIFI Language/zh_CN ABI/arm64"

key_b64 = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="

async def main():
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
        "key": key_b64,
    }
    
    headers = {
        "User-Agent": WECHAT_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://xhbi.whuh.com/index.html",
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # Try with WeChat UA
        print("=== With WeChat UA ===")
        async with session.post(f"{BASE}/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", json=url_params) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}, Response: {text[:500]}")
        
        # Try CloudImageSecurityCodeLogin with empty code
        print("\n=== CloudImageSecurityCodeLogin with empty ===")
        async with session.post(f"{BASE}/ElectronicFilmService/CloudImageSecurityCodeLogin", 
                                json={"securityCode": "", "isShare": "43CF7B83C8B9B0EB080C280E4B9D90AB",
                                      "dateTime": "ECBA4D2D727721DE13CDD5E0B710649AEB090B20C4022B2E61350092D17682DE",
                                      "id": "F43B1F2DAF390E2C36066F060A43489B"}) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}, Response: {text[:500]}")
        
        # Try fetching the main page with WeChat UA to see if it redirects differently
        print("\n=== Main page with WeChat UA ===")
        async with session.get(f"{BASE}/index.html", allow_redirects=True) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}, Final URL: {resp.url}")
            print(f"Response snippet: {text[:300]}")
        
        # Try GetCloudImageConfigInfoByHospitalCode
        print("\n=== GetCloudImageConfigInfoByHospitalCode ===")
        async with session.post(f"{BASE}/ElectronicFilmService/GetCloudImageConfigInfoByHospitalCode", 
                                json={"hospitalCode": "12420000420007938Y"}) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}, Response: {text[:500]}")

asyncio.run(main())
