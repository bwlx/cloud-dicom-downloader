"""Explore the key decryption and thirdvist flow."""
import json
import base64
from Cryptodome.Cipher import DES, AES
from Cryptodome.Util.Padding import pad, unpad
import aiohttp
import asyncio

DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])
AES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1, 8, 7, 6, 9, 4, 3, 2, 1])
AES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8, 1, 2, 3, 4, 9, 6, 7, 8])

key_b64 = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="

# Function f from JS (swaps adjacent character pairs)
def swap_pairs(s):
    chars = list(s)
    result = []
    for i in range(len(chars)):
        if i % 2 != 0:
            result.append(chars[i - 1])
        else:
            result.append(chars[i + 1] if i + 1 < len(chars) else chars[i])
    return ''.join(result)


print("=== Trying different key transformations ===")
raw = base64.b64decode(key_b64)

# Try swapping pairs first
swapped_key = swap_pairs(key_b64)
print(f"Swapped key: {swapped_key[:80]}...")

# Try AES on raw bytes
for name, key_bytes, iv_bytes in [
    ("AES-16", AES_KEY, AES_IV),
]:
    try:
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv=iv_bytes)
        result = unpad(cipher.decrypt(raw), 16)
        print(f"AES decrypt raw: {result}")
    except Exception as e:
        print(f"AES decrypt raw failed: {e}")

# Try AES-ECB (no IV)
try:
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    result = unpad(cipher.decrypt(raw), 16)
    print(f"AES-ECB decrypt: {result}")
except Exception as e:
    print(f"AES-ECB failed: {e}")

# Maybe it's hex -> base64 -> AES (like d function)
try:
    # The 'd' function: hex.parse -> Base64.stringify -> AES.decrypt
    # Let's try: base64.decode -> hex -> base64.encode -> AES.decrypt
    hex_str = raw.hex()
    b64_from_hex = base64.b64encode(bytes.fromhex(hex_str)).decode()
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
    result = unpad(cipher.decrypt(base64.b64decode(b64_from_hex)), 16)
    print(f"Via hex->b64->AES: {result}")
except Exception as e:
    print(f"Via hex->b64->AES failed: {e}")


async def main():
    async with aiohttp.ClientSession() as session:
        BASE = "https://xhbi.whuh.com"
        
        # Try the thirdvistindex approach - GetCloudImageReportInfoByThirdVistParm
        # with different formats
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
        
        # Try with the raw key (not URL-encoded)
        print("\n=== GetCloudImageReportInfoByThirdVistParm with full params ===")
        async with session.post(f"{BASE}/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", json=url_params) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}")
            print(f"Response: {text[:800]}")
        
        # Try just the key
        print("\n=== GetCloudImageReportInfoByThirdVistParm with just key ===")
        async with session.post(f"{BASE}/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm", 
                                json={"key": key_b64}) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}")
            print(f"Response: {text[:500]}")
        
        # Try GetCloudImageReportInfoByID
        print("\n=== GetCloudImageReportInfoByID ===")
        async with session.post(f"{BASE}/ElectronicFilmService/GetCloudImageReportInfoByID", 
                                json={"id": "0001_100012429927"}) as resp:
            text = await resp.text()
            print(f"Status: {resp.status}")
            print(f"Response: {text[:500]}")

asyncio.run(main())
