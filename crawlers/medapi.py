import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl

from Cryptodome.Cipher import AES
from yarl import URL
from tqdm import tqdm

from crawlers._utils import SeriesDirectory, download_bytes, new_http_client, pkcs7_pad, pkcs7_unpad, suggest_save_dir

_SHARE_CLIENT_ID = "eworld.spa.imagecloud"
_SHARE_SCOPE = "openid api-operate api-imaging api-archive api-idcas api-ability api-pacs-common"
_REDIRECT_PATH = "/oidc/login-callback.html"
_SHORT_LINK_PATH = "/s/"
_SHARE_INDEX_PATH = "/sharevisit/mobile/digitalimage/index"
_SHORT_REDIRECT_PATH = "/redirect/index.html"

_AES_KEY = b"561382DAD3AE48A89AC3003E15D75CC0"
_AES_IV = b"1234567890000000"
_REDIRECT_CODES = {301, 302, 303, 307, 308}


@dataclass(slots=True)
class ShareInfo:
	share_sid: str
	observation_id: str


@dataclass(slots=True)
class AccessToken:
	access_token: str
	token_type: str = "Bearer"


def _encrypt_text(text: str) -> str:
	cipher = AES.new(_AES_KEY, AES.MODE_CBC, iv=_AES_IV)
	return cipher.encrypt(pkcs7_pad(text.encode("utf-8"))).hex()


def _decrypt_text(cipher_hex: str) -> str:
	cipher = AES.new(_AES_KEY, AES.MODE_CBC, iv=_AES_IV)
	return pkcs7_unpad(cipher.decrypt(bytes.fromhex(cipher_hex))).decode("utf-8")


def _extract_share_sid(address: URL) -> str:
	if address.path.startswith(_SHORT_LINK_PATH):
		parts = [part for part in address.path.split("/") if part]
		if len(parts) >= 2 and parts[0] == "s":
			return parts[1]

	if address.path in {_SHARE_INDEX_PATH, _SHORT_REDIRECT_PATH}:
		sid = address.query.get("sid")
		if sid:
			return sid

	raise ValueError("当前链接不是受支持的数字影像分享链接。")


def _parse_short_url_payload(payload: dict) -> ShareInfo:
	data = payload.get("data")
	if not isinstance(data, dict):
		raise ValueError("短链解析失败，站点没有返回有效数据。")

	share_sid = str(data.get("hash_id") or "").strip()
	if not share_sid:
		raise ValueError("短链解析失败，站点没有返回分享标识。")

	observation_id = str(data.get("observation_id") or "").strip()
	if observation_id:
		return ShareInfo(share_sid=share_sid, observation_id=observation_id)

	extras_text = data.get("extras")
	if isinstance(extras_text, str) and extras_text.strip():
		try:
			extras = json.loads(extras_text)
		except json.JSONDecodeError:
			extras = None

		if isinstance(extras, dict):
			observation_id = str(
				extras.get("ObservationId")
				or extras.get("observation_id")
				or extras.get("BusinessId")
				or extras.get("business_id")
				or ""
			).strip()
			if observation_id:
				return ShareInfo(share_sid=share_sid, observation_id=observation_id)

	raise ValueError("短链解析成功，但没有拿到检查标识，站点接口可能已变化。")


def _decode_api_data(payload: dict):
	code = payload.get("Code")
	if code == 10:
		return json.loads(_decrypt_text(payload["Data"]))
	if code == 0:
		return payload.get("Data")

	message = payload.get("Msg") or payload.get("Message") or "站点接口返回失败。"
	raise ValueError(message)


def _authorized_headers(token: AccessToken) -> dict[str, str]:
	return {"Authorization": f"{token.token_type} {token.access_token}"}


def _study_datetime(study: dict) -> str:
	return (
		str(study.get("StudyDateTime") or "").strip()
		or f"{study.get('StudyDate', '')} {study.get('StudyTime', '')}".strip()
	)


def _series_sort_key(series: dict):
	return (
		int(series.get("SeriesNumber") or 0),
		str(series.get("SeriesDescription") or ""),
	)


def _image_sort_key(image: dict):
	return (
		int(image.get("InstanceNumber") or 0),
		int(image.get("FrameId") or 0),
		str(image.get("SOPInstanceUID") or ""),
	)


async def _resolve_share_info(client, address: URL) -> ShareInfo:
	share_sid = _extract_share_sid(address)
	api_url = address.origin().with_path(f"/api/api-operate/short-url/{share_sid}")

	async with client.get(str(api_url)) as response:
		payload = await response.json(content_type=None)

	return _parse_short_url_payload(payload)


async def _authorize_share(client, address: URL, share_sid: str) -> AccessToken:
	state = secrets.token_hex(16)
	nonce = secrets.token_hex(16)
	redirect_uri = str(address.origin().with_path(_REDIRECT_PATH))
	auth_url = address.origin().with_path("/oauth/connect/authorize").with_query({
		"client_id": _SHARE_CLIENT_ID,
		"redirect_uri": redirect_uri,
		"response_type": "id_token token",
		"scope": _SHARE_SCOPE,
		"state": state,
		"nonce": nonce,
		"indicator": "sharevisit",
		"sid": share_sid,
	})

	next_url = auth_url
	for _ in range(12):
		async with client.get(str(next_url), allow_redirects=False) as response:
			location = response.headers.get("Location")
			if response.status in _REDIRECT_CODES and location:
				redirect = URL(location) if "://" in location else response.url.join(URL(location))
				fragment = dict(parse_qsl(redirect.fragment))
				access_token = fragment.get("access_token")
				if access_token:
					return AccessToken(
						access_token=access_token,
						token_type=fragment.get("token_type") or "Bearer",
					)
				next_url = redirect.with_fragment("")
				continue

			raise ValueError("匿名授权失败，站点没有返回可用的访问令牌。")

	raise ValueError("匿名授权失败，跳转次数超出预期。")


async def _resolve_image_view_sid(client, address: URL, token: AccessToken, observation_id: str) -> str:
	api_url = address.origin().with_path("/api/api-idcas/Observations/image-web-view-url").with_query({
		"business_id": observation_id,
		"hide_download": "true",
		"client_kind": "5",
	})
	async with client.get(str(api_url), headers=_authorized_headers(token)) as response:
		payload = await response.json(content_type=None)

	if payload.get("code") != 0 or not payload.get("data"):
		message = payload.get("msg") or "无法获取影像查看入口。"
		raise ValueError(message)

	return str(payload["data"])


async def _load_access_params(client, address: URL, token: AccessToken, image_view_sid: str) -> tuple[str, dict[str, str]]:
	api_url = address.origin().with_path("/api/api-imaging/Params/GetSearchParams").with_query({"hashId": image_view_sid})
	async with client.post(str(api_url), headers=_authorized_headers(token)) as response:
		payload = await response.json(content_type=None)

	if payload.get("Code") != 0 or not payload.get("Data"):
		message = payload.get("Msg") or "无法解析影像访问参数。"
		raise ValueError(message)

	params_text = _decrypt_text(payload["Data"])
	params = dict(parse_qsl(params_text, keep_blank_values=True))
	return params_text, params


async def _load_studies(client, address: URL, token: AccessToken, params_text: str) -> list[dict]:
	encrypted = _encrypt_text(params_text)
	api_url = address.origin().with_path("/api/api-imaging/study/studyinfo").with_query({"data": encrypted})
	async with client.post(str(api_url), headers=_authorized_headers(token)) as response:
		payload = await response.json(content_type=None)

	studies = _decode_api_data(payload)
	if not studies:
		raise ValueError("影像查看链接有效，但没有返回任何检查序列。")

	return studies


async def _download_dicom(client, address: URL, token: AccessToken, study: dict, image: dict, params: dict[str, str]) -> bytes:
	query = {
		"ImagePath": str(image.get("ImagePath") or ""),
		"DeviceID": str(image.get("DeviceID") or study.get("DeviceID") or ""),
		"TenancyID": str(image.get("TenancyID") or study.get("TenancyID") or params.get("tenancy_id") or ""),
	}
	api_url = address.origin().with_path("/api/api-imaging/Dicom/File").with_query(query)
	return await download_bytes(
		client,
		str(api_url),
		headers=_authorized_headers(token),
		label=f"{study.get('StudyDescription') or '数字影像'} DICOM",
	)


async def _download_study(client, address: URL, token: AccessToken, study: dict, params: dict[str, str]):
	save_to = suggest_save_dir(
		str(study.get("PatientName") or "匿名"),
		str(study.get("StudyDescription") or study.get("AccessionNumber") or "数字影像"),
		_study_datetime(study),
	)
	print(f"保存到: {save_to}")

	series_list = sorted(study.get("SeriesList") or [], key=_series_sort_key)
	for series in series_list:
		images = sorted(series.get("ImageList") or [], key=_image_sort_key)
		if not images:
			continue

		series_name = str(series.get("SeriesDescription") or "").strip() or "Unnamed"
		series_number = series.get("SeriesNumber")
		directory = SeriesDirectory(save_to, int(series_number) if series_number is not None else None, series_name, len(images))

		for index, image in enumerate(tqdm(images, desc=series_name, unit="张")):
			dicom = await _download_dicom(client, address, token, study, image, params)
			directory.write_bytes(index, "dcm", dicom)

		directory.ensure_complete()


async def run(share_url, *args):
	address = URL(share_url)
	print("下载数字影像 DICOM")

	async with new_http_client() as client:
		share_info = await _resolve_share_info(client, address)
		token = await _authorize_share(client, address, share_info.share_sid)
		image_view_sid = await _resolve_image_view_sid(client, address, token, share_info.observation_id)
		params_text, params = await _load_access_params(client, address, token, image_view_sid)
		studies = await _load_studies(client, address, token, params_text)

		# 当前分享链路只会落到单个检查，仍保留第一个以便后续同类站点复用。
		await _download_study(client, address, token, studies[0], params)
