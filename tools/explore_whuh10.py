"""Try API calls with hospital context."""
import json, base64, hashlib, time, random, aiohttp, asyncio
from datetime import datetime
from Cryptodome.Cipher import DES, AES
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
    BASE = "https://xhbi.whuh.com"
    hospital_code = "12420000420007938Y"
    
    async with aiohttp.ClientSession() as session:
        # Get config first
        print("=== Config ===")
        config = await call_api(session, "/ElectronicFilmService/GetCloudImageConfigInfoByHospitalCode", 
                                {"HospitalCode": hospital_code})
        config_data = json.loads(config)
        print(f"CloudImagePACSType: {config_data['Result']['CloudImagePACSType']}")
        print(f"WPACSPrePath: {config_data['Result']['WPACSPrePath']}")
        
        # Now try GetCloudImageReportInfoByID with various parameters
        report_id = "0001_100012429927"  # Decrypted from 'id' param
        
        for label, data in [
            ("just id", {"id": report_id}),
            ("id + HospitalCode", {"id": report_id, "HospitalCode": hospital_code}),
            ("id + hospitalCode", {"id": report_id, "hospitalCode": hospital_code}),
            ("id + CloudImageReportID", {"CloudImageReportID": report_id}),
            ("id + CloudImageReportID + HospitalCode", {"CloudImageReportID": report_id, "HospitalCode": hospital_code}),
        ]:
            print(f"\n=== GetCloudImageReportInfoByID ({label}) ===")
            r = await call_api(session, "/ElectronicFilmService/GetCloudImageReportInfoByID", data)
            print(f"Response: {r}")
        
        # Try GetPatientCloudReportInfos
        print("\n=== GetPatientCloudReportInfos ===")
        r = await call_api(session, "/ElectronicFilmService/GetPatientCloudReportInfos", {
            "HospitalCode": hospital_code,
            "PatientID": "P4901903",
        })
        print(f"Response: {r[:500]}")
        
        # Try GetExaminationInfoByCloudImageReportID
        print("\n=== GetExaminationInfoByCloudImageReportID ===")
        r = await call_api(session, "/ElectronicFilmService/GetExaminationInfoByCloudImageReportID", {
            "CloudImageReportID": report_id,
            "HospitalCode": hospital_code,
        })
        print(f"Response: {r}")

asyncio.run(main())
