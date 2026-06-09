import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from Cryptodome.Cipher import AES
from tqdm import tqdm
from yarl import URL

from crawlers._utils import IncompleteDownloadError, SeriesDirectory, new_http_client, retry_async, suggest_save_dir

_HOST = "cfsaas.wegopoly.com"
_AES_KEY = b"f9c3a7e8d2b64f1a9e0c5b7d3a8f6c21"
_AES_IV = b"8a3f9c2e6b1d7a04"
_API_PREFIX = "/saas"
_REPORT_INFO_PATH = "/image/h5/api/get/report/info"
_IMAGE_JSON_PATH = "/image/h5/api/image/json/infoH5"
_DOWNLOAD_CHUNK_SIZE = 16384


@dataclass(slots=True)
class ShareAccess:
	hid: str
	study_index: str
	verify_token: str


def _decode_base64_url(text: str) -> bytes:
	padding = "=" * ((4 - len(text) % 4) % 4)
	return base64.urlsafe_b64decode(text + padding)


def _decrypt_query(q: str) -> dict:
	try:
		raw = _decode_base64_url(q)
		cipher = AES.new(_AES_KEY, AES.MODE_CTR, nonce=b"", initial_value=_AES_IV)
		return json.loads(cipher.decrypt(raw).decode("utf-8"))
	except Exception as exc:
		raise ValueError("威众云影像链接参数解密失败，链接可能已损坏或过期。") from exc


def _parse_share_link(address: URL) -> ShareAccess:
	if address.host != _HOST or address.path.rstrip("/") != "/image":
		raise ValueError("当前链接不是受支持的威众云影像分享链接。")

	params = dict(address.query)
	if params.get("q"):
		params.update(_decrypt_query(str(params["q"])))

	hid = str(params.get("hid") or "").strip()
	study_index = str(params.get("studyIndex") or "").strip()
	verify_token = str(params.get("acc") or "").strip()

	if not hid or not study_index or not verify_token:
		raise ValueError("威众云影像链接缺少必要参数：hid / studyIndex / acc。")

	return ShareAccess(hid=hid, study_index=study_index, verify_token=verify_token)


def _api_headers(access: ShareAccess) -> dict[str, str]:
	return {
		"hid": access.hid,
		"verifyToken": access.verify_token,
		"version": "undefined",
		"x-cf-biz": "",
	}


def _parse_api_payload(payload: dict, *, message: str):
	if payload.get("code") == 1 and payload.get("data") is not None:
		return payload["data"]
	raise ValueError(payload.get("message") or message)


def _join_paths(*parts) -> str:
	return "/".join(str(part).strip("/") for part in parts if str(part or "").strip("/"))


def _download_prefix(image_json: dict) -> str:
	prefix = _join_paths(
		image_json.get("serverhost"),
		image_json.get("dicomfile"),
		image_json.get("relativeDir"),
	)
	if not prefix:
		raise ValueError("威众云影像没有返回可用的 DICOM 存储路径。")
	return prefix


def _image_url(prefix: str, image: dict) -> str:
	image_id = str(image.get("imageId") or "").strip()
	if not image_id:
		raise ValueError("威众云影像返回的实例缺少 imageId。")
	if image_id.startswith(("http://", "https://")):
		return image_id
	return f"{prefix.rstrip('/')}/{image_id.lstrip('/')}"


def _study_label(report: dict, image_json: dict) -> str:
	for key in ("examName", "parts", "itemName", "modality", "accessionNumber", "studyIndex"):
		text = str(report.get(key) or image_json.get(key) or "").strip()
		if text:
			return text
	return "云影像"


def _study_datetime(report: dict, image_json: dict) -> str:
	for key in ("studyDatetime", "reportTime", "auditTime", "studyDate", "studyIndex", "accessionNumber"):
		text = str(report.get(key) or image_json.get(key) or "").strip()
		if text:
			return text
	return "study"


def _series_number(series: dict) -> int | None:
	try:
		return int(series.get("seriesNumber"))
	except (TypeError, ValueError):
		return None


async def _post_json(client: aiohttp.ClientSession, path: str) -> dict:
	async with client.post(_API_PREFIX + path) as response:
		return await response.json(content_type=None)


def _validate_dicom_bytes(path: Path, label: str):
	with path.open("rb") as fp:
		header = fp.read(132)

	if header.startswith((b"<html", b"<!doctype", b"<?xml")) or header[128:132] != b"DICM":
		path.unlink(missing_ok=True)
		raise ValueError(f"{label} 下载到的不是有效 DICOM 文件，站点可能没有开放原始影像文件。")


async def _download_dicom(client: aiohttp.ClientSession, path: Path, url: str, *, label: str):
	async def _once():
		temp = path.with_name(path.name + ".part")
		temp.unlink(missing_ok=True)
		size = 0
		try:
			async with client.get(url) as response:
				if response.status == 404:
					raise ValueError(f"{label} 在威众云影像存储中不存在（HTTP 404），可能是源站对象缺失或链接已失效。")
				if response.status >= 400:
					response.raise_for_status()

				path.parent.mkdir(parents=True, exist_ok=True)
				with temp.open("wb") as fp:
					async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
						fp.write(chunk)
						size += len(chunk)

				if response.content_length is not None and size != response.content_length:
					raise IncompleteDownloadError(
						f"{label} 下载不完整，预期 {response.content_length} 字节，实际 {size} 字节。"
					)

				temp.replace(path)
				_validate_dicom_bytes(path, label)
				return path
		except Exception:
			temp.unlink(missing_ok=True)
			raise

	return await retry_async(_once, label=label)


async def run(url: str, *_):
	access = _parse_share_link(URL(url))
	origin = str(URL(url).origin())

	async with new_http_client(origin, headers=_api_headers(access), raise_for_status=False) as client:
		report = _parse_api_payload(
			await _post_json(client, _REPORT_INFO_PATH),
			message="威众云影像没有返回检查报告信息。",
		)
		image_json = _parse_api_payload(
			await _post_json(client, _IMAGE_JSON_PATH),
			message="威众云影像没有返回影像数据。",
		)

		series_list = list(image_json.get("seriesList") or [])
		if not series_list:
			raise ValueError("威众云影像没有返回任何序列。")

		save_to = suggest_save_dir(
			str(report.get("patientName") or image_json.get("patientCnName") or image_json.get("patientName") or "匿名").strip() or "匿名",
			_study_label(report, image_json),
			_study_datetime(report, image_json),
		)
		prefix = _download_prefix(image_json)

		print(f"下载 {report.get('patientName') or image_json.get('patientCnName') or '匿名'} 的 DICOM，共 {len(series_list)} 个序列。")
		print(f"保存到: {save_to}\n")

		for series in series_list:
			images = list(series.get("instanceList") or [])
			if not images:
				continue

			desc = str(series.get("seriesDescription") or "").strip() or "Unnamed"
			directory = SeriesDirectory(save_to, _series_number(series), desc, len(images), resume=True)
			progress = tqdm(images, desc=desc, unit="张", file=sys.stdout)
			try:
				for index, image in enumerate(progress):
					path = directory.get(index, "dcm")
					if path.exists():
						directory.mark_complete(index)
						continue
					label = f"{desc} 第 {index + 1} 张"
					await _download_dicom(client, path, _image_url(prefix, image), label=label)
					directory.mark_complete(index)
			finally:
				progress.close()

			directory.ensure_complete()
