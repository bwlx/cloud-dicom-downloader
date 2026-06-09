import asyncio
import base64
import hashlib
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import aiohttp
from Cryptodome.Cipher import AES, DES
from Cryptodome.Util.Padding import pad, unpad
from yarl import URL

from crawlers._utils import download_to_path, make_unique_dir, new_http_client, pathify, suggest_save_dir

_BASE_URL = URL("https://xhbi.whuh.com")
_HOST = "xhbi.whuh.com"
_DES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1])
_DES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8])
_AES_KEY = bytes([8, 7, 6, 9, 4, 3, 2, 1, 8, 7, 6, 9, 4, 3, 2, 1])
_AES_IV = bytes([1, 2, 3, 4, 9, 6, 7, 8, 1, 2, 3, 4, 9, 6, 7, 8])

_CONFIG_PATH = "/ElectronicFilmService/GetCloudImageConfigInfoByHospitalCode"
_THIRD_VISIT_PATH = "/ElectronicFilmService/GetCloudImageReportInfoByThirdVistParm"
_EXIST_IMAGE_PATH = "/ElectronicFilmService/GetExistImageByCloudImageReportID"
_DOWNLOAD_CACHE_PATH = "/ElectronicFilmService/GetDownloadCacheInfoByCloudImage"
_DOWNLOAD_STATUS_PATH = "/ElectronicFilmService/GetDownloadStatus"
_ADD_DOWNLOAD_RECORD_PATH = "/ElectronicFilmService/AddDownloadImageRecordInfo"

_DICOM_EXTENSION = 0
_PATIENT_DOWNLOAD_SOURCE = 5
_COMPRESS_DONE = 4
_COMPRESS_FAILED = 5
_POLL_INTERVAL_SECONDS = 3
_POLL_TIMEOUT_SECONDS = 30 * 60
_PARAMETER_NAMES = {
	"e": "ExaminationID",
	"p": "PatientID",
	"r": "ReportID",
	"t": "VistType",
	"s": "TelePhone",
	"c": "CertificateCode",
	"m": "OutpatientNO",
	"z": "HospitalizationNO",
}
_EXCLUDED_THIRD_VISIT_KEYS = {"isEncryption", "dateTime", "isShare", "id", "h"}
_COMPRESS_STATUS_NAMES = {
	0: "未开始",
	1: "拉取中",
	2: "处理中",
	3: "压缩中",
	4: "已完成",
	5: "已失败",
}


@dataclass(slots=True)
class ShareLink:
	query: dict[str, str]
	redacted_url: str


def _fragment_query(address: URL) -> dict[str, str]:
	fragment = address.fragment.strip()
	if not fragment:
		return {}
	if not fragment.startswith("/"):
		fragment = "/" + fragment
	return dict(parse_qsl(urlsplit(fragment).query, keep_blank_values=True))


def _parse_share_link(address: URL) -> ShareLink:
	if address.host != _HOST:
		raise ValueError("当前链接不是受支持的武汉大学中南医院云影像分享链接。")

	query = _fragment_query(address)
	if not query:
		query = {key: str(value) for key, value in address.query.items()}

	required = {"h", "t", "key"}
	if not required.issubset(query):
		raise ValueError("武汉大学中南医院云影像链接缺少必要参数。")

	if not any(query.get(key) for key in ("e", "p", "r", "s", "c", "m", "z")):
		raise ValueError("武汉大学中南医院云影像链接缺少检查号、患者号或报告号。")

	return ShareLink(query=query, redacted_url=_redact_link(address))


def _redact_link(address: URL) -> str:
	query = _fragment_query(address)
	if not query:
		return f"{address.origin()}{address.path}"

	route = address.fragment.split("?", 1)[0] if address.fragment else "/reportView"
	keys = "&".join(f"{key}=<redacted>" for key in sorted(query))
	return f"{address.origin()}{address.path}#{route}?{keys}"


def _decrypt_aes_hex(text: str) -> str:
	if not text:
		return ""
	try:
		raw = bytes.fromhex(text)
		plain = AES.new(_AES_KEY, AES.MODE_CBC, iv=_AES_IV).decrypt(raw)
		return unpad(plain, AES.block_size).decode("utf-8")
	except Exception as exc:
		raise ValueError("武汉大学中南医院云影像链接参数解密失败，链接可能已损坏。") from exc


def _encrypt_des(text: str) -> str:
	plain = pad(text.encode("utf-8"), DES.block_size)
	encrypted = DES.new(_DES_KEY, DES.MODE_CBC, iv=_DES_IV).encrypt(plain)
	return base64.b64encode(encrypted).decode("ascii")


def _decrypt_des_response(text: str) -> dict:
	text = text.strip()
	if text.startswith("{") or text.startswith("["):
		return json.loads(text)

	ciphertext = text.strip('"')
	padding = "=" * ((4 - len(ciphertext) % 4) % 4)
	raw = base64.b64decode(ciphertext + padding)
	plain = DES.new(_DES_KEY, DES.MODE_CBC, iv=_DES_IV).decrypt(raw)
	return json.loads(unpad(plain, DES.block_size).decode("utf-8"))


def _signed_headers(path: str) -> dict[str, str]:
	timestamp = str(int(time.time() * 1000))
	nonce = str(random.randint(100000, 199999))
	signature = hashlib.md5(f"{nonce}{timestamp}{path}{nonce}".encode("utf-8")).hexdigest().upper()
	return {
		"TimeStamp": timestamp,
		"Nonce": nonce,
		"Signature": signature,
		"vToken": _encrypt_des(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
		"Content-Type": "application/json",
		"Origin": str(_BASE_URL),
		"Referer": f"{_BASE_URL}/index.html",
		"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0",
	}


async def _post_api(client: aiohttp.ClientSession, path: str, data: dict) -> dict:
	async with client.post(path, json=data, headers=_signed_headers(path)) as response:
		payload = _decrypt_des_response(await response.text())

	if payload.get("Success"):
		return payload

	message = str(payload.get("Message") or "").strip()
	raise ValueError(message or f"武汉大学中南医院云影像接口调用失败：{path}")


def _validate_share_link(share: ShareLink):
	date_time = share.query.get("dateTime")
	if date_time:
		expires = _decrypt_aes_hex(date_time)
		try:
			expires_at = datetime.strptime(expires, "%Y-%m-%d %H:%M:%S")
		except ValueError:
			return
		if expires_at < datetime.now():
			raise ValueError("二维码已过期。")


def _third_visit_params(share: ShareLink) -> dict:
	params = [
		{"Key": _PARAMETER_NAMES.get(key, key), "Value": value}
		for key, value in share.query.items()
		if key not in _EXCLUDED_THIRD_VISIT_KEYS
	]
	params.append({"Key": "Url", "Value": share.redacted_url})
	return {
		"HospitalNumber": share.query["h"],
		"VistParms": params,
	}


def _select_report(result) -> dict:
	if isinstance(result, list):
		if not result:
			raise ValueError("武汉大学中南医院云影像没有返回可下载的检查。")
		if len(result) > 1:
			print(f"链接匹配到 {len(result)} 个检查，默认下载第 1 个。", file=sys.stderr)
		report = result[0]
	elif isinstance(result, dict):
		report = result
	else:
		raise ValueError("武汉大学中南医院云影像返回的检查信息格式无法识别。")

	if not isinstance(report, dict):
		raise ValueError("武汉大学中南医院云影像返回的检查信息格式无法识别。")

	required = ("HospitalID", "ID", "PatientID", "ExaminationID", "ReportID")
	if any(not report.get(key) for key in required):
		raise ValueError("武汉大学中南医院云影像返回的检查信息不完整，无法继续下载。")
	return report


def _study_label(report: dict) -> str:
	for key in ("StudyTypeName", "StudyItemName", "StudyPartName", "ExaminationID", "ReportID"):
		text = str(report.get(key) or "").strip()
		if text:
			return text
	return "云影像"


def _study_datetime(report: dict) -> str:
	for key in ("CheckDate", "StudyDate", "VerifyDate", "DiagnosisDate", "CreateDate", "ExaminationID"):
		text = str(report.get(key) or "").strip()
		if text:
			return text
	return "study"


def _download_cache_payload(report: dict) -> dict:
	return {
		"hospitalID": report["HospitalID"],
		"hospitalNumber": report.get("HospitalNumber"),
		"info": {
			"ExaminationID": report.get("ExaminationID"),
			"ReportID": report.get("ReportID"),
			"PatientID": report.get("PatientID"),
			"SeriesInstanceUID": "",
			"PatientName": report.get("PatientName"),
			"StudyTypeName": report.get("StudyTypeName"),
			"StudyDate": report.get("CheckDate") or report.get("StudyDate"),
			"DownloadSource": _PATIENT_DOWNLOAD_SOURCE,
			"ImageDownloadExtentions": _DICOM_EXTENSION,
		},
	}


def _extract_download_file(payload: dict) -> tuple[dict, float | None]:
	result = payload.get("Result")
	if not isinstance(result, dict):
		raise ValueError("武汉大学中南医院云影像没有返回有效的下载任务。")

	file_info = result.get("File")
	if not isinstance(file_info, dict):
		raise ValueError("武汉大学中南医院云影像没有返回有效的下载文件信息。")
	return file_info, result.get("Progress")


def _format_progress(progress) -> float:
	try:
		return float(progress or 0)
	except (TypeError, ValueError):
		return 0


async def _wait_download_ready(client: aiohttp.ClientSession, report: dict, file_info: dict) -> dict:
	status = int(file_info.get("CompressStatus") or 0)
	start = time.monotonic()
	last_label = None
	last_progress = None
	if not file_info.get("ID"):
		raise ValueError("武汉大学中南医院云影像没有返回下载任务 ID。")

	while status < _COMPRESS_DONE:
		if time.monotonic() - start > _POLL_TIMEOUT_SECONDS:
			raise TimeoutError("等待影像压缩包生成超时，请稍后重试。")

		await asyncio.sleep(_POLL_INTERVAL_SECONDS)
		payload = await _post_api(client, _DOWNLOAD_STATUS_PATH, {
			"hospitalID": report["HospitalID"],
			"id": file_info.get("ID"),
		})
		file_info, progress = _extract_download_file(payload)
		status = int(file_info.get("CompressStatus") or 0)
		label = _COMPRESS_STATUS_NAMES.get(status, str(status))

		if status == _COMPRESS_FAILED:
			raise ValueError("影像压缩包生成失败，请稍后重试。")
		if label != last_label or progress != last_progress:
			print(f"影像压缩状态：{label}，进度 {_format_progress(progress):.0f}%")
			last_label = label
			last_progress = progress

	if not file_info.get("DownloadUrl"):
		raise ValueError("影像压缩已完成，但站点没有返回下载地址。")
	return file_info


def _safe_file_name(file_info: dict, report: dict) -> str:
	name = str(file_info.get("FileName") or "").strip()
	if not name:
		base = str(report.get("ExaminationID") or report.get("ReportID") or "whuh-dicom").strip()
		name = f"{base}.zip"
	if not name.lower().endswith(".zip"):
		name += ".zip"
	return pathify(name)


async def _record_download(client: aiohttp.ClientSession, report: dict, file_info: dict):
	try:
		await _post_api(client, _ADD_DOWNLOAD_RECORD_PATH, {
			"UserID": "",
			"UserName": "",
			"DownloadFileID": file_info.get("ID"),
			"DownloadSource": _PATIENT_DOWNLOAD_SOURCE,
			"HospitalID": report["HospitalID"],
		})
	except Exception as exc:
		print(f"下载记录写入失败，已忽略：{exc}", file=sys.stderr)


async def run(url: str, *_):
	share = _parse_share_link(URL(url))
	_validate_share_link(share)

	async with new_http_client(str(_BASE_URL)) as client:
		config_payload = await _post_api(client, _CONFIG_PATH, {"hospitalCode": share.query["h"]})
		config = config_payload.get("Result") or {}
		if config.get("IsImageDownload") is False:
			print("站点配置标记为不显示下载入口，仍尝试调用其电脑端下载接口。", file=sys.stderr)

		report_payload = await _post_api(client, _THIRD_VISIT_PATH, _third_visit_params(share))
		report = _select_report(report_payload.get("Result"))

		exist_payload = await _post_api(client, _EXIST_IMAGE_PATH, {
			"hospitalID": report["HospitalID"],
			"cloudImageReportID": report["ID"],
		})
		if not exist_payload.get("Result"):
			raise ValueError("当前处于高峰期，站点正在从服务器拉取影像，请稍后重试。")

		cache_payload = await _post_api(client, _DOWNLOAD_CACHE_PATH, _download_cache_payload(report))
		file_info, progress = _extract_download_file(cache_payload)
		status = int(file_info.get("CompressStatus") or 0)
		if status == _COMPRESS_FAILED:
			raise ValueError("影像压缩包生成失败，请稍后重试。")
		if status < _COMPRESS_DONE:
			print(f"影像压缩状态：{_COMPRESS_STATUS_NAMES.get(status, status)}，进度 {_format_progress(progress):.0f}%")
			file_info = await _wait_download_ready(client, report, file_info)

		save_dir = make_unique_dir(suggest_save_dir(
			str(report.get("PatientName") or "匿名").strip() or "匿名",
			_study_label(report),
			_study_datetime(report),
		))
		file_name = _safe_file_name(file_info, report)
		target = Path(save_dir) / file_name

		print(f"下载地址：{file_info['DownloadUrl']}")
		print(f"下载武汉大学中南医院云影像 DICOM 压缩包到：{target}")
		await download_to_path(
			client,
			target,
			file_info["DownloadUrl"],
			headers={"Referer": f"{_BASE_URL}/index.html"},
			label=file_name,
		)
		await _record_download(client, report, file_info)
