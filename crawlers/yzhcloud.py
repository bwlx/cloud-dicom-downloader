import re
from dataclasses import dataclass

from yarl import URL

from crawlers._utils import SeriesDirectory, new_http_client, suggest_save_dir, tqdme

_POCKETFILM_PATH = "/pocketfilm/index.php"
_POCKETFILM_ACTIONS = {"itemdetails_qrcode", "itemdetails"}
_LEGACY_API = "w_viewer_2/index.php/home/index/ajax_get_patient_study"
_DICOM2020_API = "dicom_2020/index.php/home/index/ajax_get_study"
# 站点根据 UA 返回不同模板：桌面 UA 只给占位 "影像调阅"，
# 移动 / 微信 UA 才会渲染带患者姓名的完整 <title>。
_MOBILE_UA = (
	"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
	"AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0"
)
_VIEWER_LINK_RE = re.compile(
	r'href=["\'](?P<url>[^"\']*?/dicom_2020/\?[^"\']+)["\']',
	re.IGNORECASE,
)
_VIEWER_TITLE_RE = re.compile(r"<title>([^<]+?)/[男女][^<]*?的影像</title>")


@dataclass(slots=True)
class StudyAccess:
	api_path: str
	study_uid: str
	org_id: str
	patient_name_hint: str | None


async def _fetch_text(client, url: str) -> str:
	async with client.get(url) as response:
		return await response.text()


def _parse_viewer_link(html: str, origin: URL) -> URL:
	match = _VIEWER_LINK_RE.search(html)
	if not match:
		raise ValueError("德阳云影像落地页没有找到查看器链接，站点接口可能已变化。")
	link = match.group("url").replace("&amp;", "&")
	address = URL(link)
	if address.is_absolute():
		return address
	return origin.join(URL(link))


def _parse_patient_name(html: str) -> str | None:
	match = _VIEWER_TITLE_RE.search(html)
	if not match:
		return None
	name = match.group(1).strip()
	return name or None


async def _resolve_pocketfilm(client, share_url: URL) -> StudyAccess:
	origin = share_url.origin()
	landing_html = await _fetch_text(client, str(share_url))
	viewer_url = _parse_viewer_link(landing_html, origin)
	if "org_id" not in viewer_url.query:
		raise ValueError("德阳云影像查看器链接缺少 org_id 参数。")

	viewer_html = await _fetch_text(client, str(viewer_url))
	study_match = re.search(r"study_instance_uid\s*=\s*'([^']+)'", viewer_html)
	if not study_match:
		raise ValueError("德阳云影像查看器页没有暴露 study_instance_uid。")

	return StudyAccess(
		api_path=_DICOM2020_API,
		study_uid=study_match.group(1),
		org_id=viewer_url.query["org_id"],
		patient_name_hint=_parse_patient_name(viewer_html),
	)


def _resolve_direct(share_url: URL) -> StudyAccess:
	if "study_instance_uid" not in share_url.query or "org_id" not in share_url.query:
		raise ValueError("当前链接不是受支持的医众数字云影像分享链接。")
	return StudyAccess(
		api_path=_LEGACY_API,
		study_uid=share_url.query["study_instance_uid"],
		org_id=share_url.query["org_id"],
		patient_name_hint=None,
	)


def _is_pocketfilm_url(address: URL) -> bool:
	if not address.path.startswith(_POCKETFILM_PATH):
		return False
	action = address.query.get("a", "")
	return action in _POCKETFILM_ACTIONS


async def run(share_url: str):
	address = URL(share_url)

	async with new_http_client(address.origin()) as client:
		client.headers["Referer"] = str(address.origin())
		client.headers["Origin"] = str(address.origin())
		client.headers["User-Agent"] = _MOBILE_UA

		if _is_pocketfilm_url(address):
			access = await _resolve_pocketfilm(client, address)
		else:
			access = _resolve_direct(address)

		params = {"study_instance_uid": access.study_uid, "org_id": access.org_id}
		async with client.get(access.api_path, params=params) as response:
			info = await response.json()

		patient_name = (
			info.get("patient_name")
			or access.patient_name_hint
			or info.get("patient_id")
			or "匿名"
		)
		cdn = URL(info["storage"]).with_scheme("https")
		study_dir = suggest_save_dir(patient_name, info["checkitems"], info["study_date"])
		print(f"下载医众数字影像到：{study_dir}")

		for series in info["series"]:
			instances = series["instance_ids"].split(",")
			number = series["series_number"]
			desc = series["series_description"]
			dir_ = SeriesDirectory(study_dir, number, desc, len(instances))

			for i, name in tqdme(instances, desc=desc):
				# 有可能出现 PNG、JPG 截屏图片作为一个序列。
				sep, ext = name.find("|"), "dcm"
				if sep != -1:
					name, ext = name[:sep], name[sep + 1:]

				u = cdn.joinpath(f"{access.study_uid}/{number}.{name}.{ext}")
				await dir_.download(client, i, "dcm", u, label=f"{desc} 第 {i + 1} 张")

			dir_.ensure_complete()
