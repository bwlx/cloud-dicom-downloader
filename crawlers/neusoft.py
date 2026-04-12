import base64
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from yarl import URL

from crawlers._utils import download_to_path, new_http_client, pathify, suggest_save_dir

_SHORT_PREFIX = "/short/"
_VIEWER_ROOT = "/M-Viewer/"
_TOKEN_STATUS_PATH = "/pacs-mobile/mobile_auth/tokenStatus"
_STUDY_INFO_PATH = "/pacs-mobile/mobile/getStudyInfo"
_DOWNLOAD_PATH = "/pacs-mobile/image/downloadZipImage"
_CONFIG_PATH = "/M-Viewer/assets/config/config.json"


@dataclass(slots=True)
class ShareLink:
	checkserialnum: str
	sign: str
	profile_url: str


@dataclass(slots=True)
class StudySummary:
	patient_name: str
	check_item: str
	study_date: str
	device_type: str


def _fragment_parts(address: URL) -> tuple[str, dict[str, str]]:
	fragment = address.fragment.strip()
	if not fragment:
		return "", {}

	if not fragment.startswith("/"):
		fragment = "/" + fragment

	parts = urlsplit(fragment)
	return parts.path, dict(parse_qsl(parts.query, keep_blank_values=True))


def _decode_jwt_payload(token: str) -> dict:
	try:
		payload = token.split(".", 2)[1]
	except IndexError as exc:
		raise ValueError("分享链接里的访问令牌格式不正确。") from exc

	payload += "=" * (-len(payload) % 4)
	try:
		return json.loads(base64.urlsafe_b64decode(payload))
	except (ValueError, json.JSONDecodeError) as exc:
		raise ValueError("分享链接里的访问令牌无法解析。") from exc


def _parse_profile_link(address: URL) -> ShareLink:
	fragment_path, fragment_query = _fragment_parts(address)
	segments = [segment for segment in fragment_path.split("/") if segment]
	if len(segments) < 2 or segments[0] not in {"profile", "profilesign"}:
		raise ValueError("当前链接不是受支持的东软睿影分享链接。")

	checkserialnum = str(segments[1]).strip()
	sign = str(fragment_query.get("sign") or "").strip()
	if not checkserialnum or not sign:
		raise ValueError("分享链接缺少检查号或访问令牌，请重新复制完整链接。")

	return ShareLink(
		checkserialnum=checkserialnum,
		sign=sign,
		profile_url=str(address),
	)


def _parse_share_link(address: URL) -> ShareLink:
	if address.path.startswith(_SHORT_PREFIX):
		return ShareLink(checkserialnum="", sign="", profile_url=str(address))

	if address.path == _VIEWER_ROOT or address.path == "/M-Viewer":
		return _parse_profile_link(address)

	raise ValueError("当前链接不是受支持的东软睿影分享链接。")


async def _resolve_short_link(client, address: URL) -> URL:
	async with client.get(str(address), allow_redirects=False) as response:
		location = response.headers.get("Location")
		if response.status not in {301, 302, 303, 307, 308} or not location:
			raise ValueError("短链没有返回可用的影像分享地址，站点接口可能已变化。")

	redirect = URL(location) if "://" in location else response.url.join(URL(location))
	return redirect


async def _resolve_share_link(client, url: str) -> ShareLink:
	address = URL(url)
	share = _parse_share_link(address)
	if share.checkserialnum:
		return share

	redirect = await _resolve_short_link(client, address)
	return _parse_profile_link(redirect)


def _token_expiry(sign: str) -> str:
	payload = _decode_jwt_payload(sign)
	return str(payload.get("exp") or "")


async def _fetch_study_summary(client, address: URL, share: ShareLink) -> StudySummary:
	headers = {
		"Authorization": f"Bearer {share.sign}",
		"Referer": str(address.origin().with_path("/M-Viewer/")),
	}
	params = {"checkserialnum": share.checkserialnum}

	async with client.get(str(address.origin().with_path(_STUDY_INFO_PATH)), params=params, headers=headers) as response:
		payload = await response.json(content_type=None)

	patient = payload.get("patient") or {}
	study = payload.get("study") or {}
	return StudySummary(
		patient_name=str(patient.get("name") or "匿名").strip() or "匿名",
		check_item=str(study.get("check_item") or study.get("device_type") or "云影像").strip() or "云影像",
		study_date=str(study.get("date") or "").strip(),
		device_type=str(study.get("device_type") or "").strip(),
	)


async def _check_token_status(client, address: URL, share: ShareLink):
	exp = _token_expiry(share.sign)
	if not exp:
		return

	headers = {
		"Content-Type": "application/json",
		"Referer": str(address.origin().with_path("/M-Viewer/")),
	}
	async with client.post(str(address.origin().with_path(_TOKEN_STATUS_PATH)), json={"exp": exp}, headers=headers) as response:
		payload = await response.json(content_type=None)

	if str(payload.get("code") or "") != "60":
		message = payload.get("message") or "分享令牌已失效。"
		raise ValueError(message)


def _decode_download_url(value: str) -> str:
	try:
		return base64.b64decode(value).decode("utf-8")
	except UnicodeDecodeError:
		return base64.b64decode(value).decode("latin1")


async def _request_download_url(client, address: URL, share: ShareLink) -> str:
	api_url = address.origin().with_path(_DOWNLOAD_PATH)
	headers = {
		"Content-Type": "application/json",
		"Referer": str(URL(share.profile_url).origin().with_path("/M-Viewer/")),
	}
	async with client.post(
		str(api_url),
		json={"checkserialnum": share.checkserialnum},
		headers=headers,
		raise_for_status=False,
	) as response:
		if response.status >= 400:
			text = (await response.text()).strip()
			if text:
				raise ValueError(f"HTTP {response.status}: {text}")
			raise ValueError(f"HTTP {response.status}")

		payload = await response.json(content_type=None)

	if str(payload.get("downloadResult") or "") == "00":
		return _decode_download_url(str(payload.get("returnResult") or ""))

	message = str(payload.get("returnResult") or payload.get("message") or "").strip()
	if message:
		raise ValueError(message)
	raise ValueError("站点没有返回可下载的影像压缩包。")


async def _fetch_config(client, address: URL) -> dict:
	async with client.get(str(address.origin().with_path(_CONFIG_PATH))) as response:
		return await response.json(content_type=None)


def _save_dir(summary: StudySummary) -> Path:
	return suggest_save_dir(summary.patient_name, summary.check_item, summary.study_date or summary.device_type or "study")


def _summarize_download_error(detail: str) -> str:
	if "ORA-00942" in detail or "WEBRIS_DICOM_DOWNLOAD_RECORD" in detail:
		return "医院站点的下载接口配置异常，后端数据库表缺失，当前无法导出影像压缩包。"
	if detail.startswith("HTTP 500"):
		return "医院站点的下载接口返回服务器错误，当前无法导出影像压缩包。"
	if detail.startswith("HTTP 4"):
		return "医院站点拒绝了下载请求，当前无法导出影像压缩包。"
	return detail


def _unsupported_message(config: dict, detail: str) -> str:
	summary = _summarize_download_error(detail)
	if config.get("download") is False:
		return (
			"这个东软睿影视图部署没有开放可用的影像下载能力。"
			"程序已经识别了链接并尝试调用站点自带下载，但接口返回失败。"
			f"\n\n原因：{summary}"
		)
	return f"东软睿影站点没有返回可用下载地址：{summary}"


async def run(url: str, *_):
	address = URL(url)
	async with new_http_client() as client:
		share = await _resolve_share_link(client, url)
		profile_url = URL(share.profile_url)
		config = await _fetch_config(client, profile_url)
		await _check_token_status(client, profile_url, share)
		summary = await _fetch_study_summary(client, profile_url, share)
		save_to = _save_dir(summary)
		file_name = f"{pathify(summary.patient_name or 'study')}-viewer.zip"
		target = Path(save_to) / file_name

		print(f"下载东软睿影影像到：{target}")
		try:
			archive_url = await _request_download_url(client, profile_url, share)
		except ValueError as exc:
			raise ValueError(_unsupported_message(config, str(exc))) from exc

		await download_to_path(client, target, archive_url, label=file_name)
