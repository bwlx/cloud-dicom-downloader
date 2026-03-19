import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from tqdm import tqdm
from yarl import URL

from crawlers._utils import SeriesDirectory, new_http_client, suggest_save_dir

_PHONE_VISIBLE_ROUTE = "phone-visible"
_INFO_ROUTE = "info"
_REDIRECT_ROUTE = "redirect"
_VIEWER_PATH = "/M-Viewer/m/2D"
_SHORTSERVER_PREFIX = "/M-Viewer/shortserver/"
_VERIFY_PATH = "/M-Viewer/mobilebackend/qrcode/checkIdNum"
_XML_PATH = "/M-Viewer/m/NeuVnaimage/getxmltowebpacs.action"
_PUBLIC_VIEWER_PREFIX = "/M-Viewer/m"
_WECHAT_USER_AGENT = (
	"Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
	"AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
	"MicroMessenger/8.0.50 NetType/WIFI Language/zh_CN"
)


@dataclass(slots=True)
class ShareLink:
	buss_id: str
	requires_authority_code: bool
	short_server_id: str | None = None
	hide_qrcode: str | None = None
	forward: str | None = None
	short_url: str | None = None
	id_type: str | None = None
	sign: str | None = None


@dataclass(slots=True)
class ImageEntry:
	url: str
	instance_number: int


@dataclass(slots=True)
class SeriesEntry:
	series_number: int | None
	description: str
	images: list[ImageEntry]


@dataclass(slots=True)
class StudyEntry:
	checkserialnum: str
	patient_name: str
	modality: str
	study_time: str
	series: list[SeriesEntry]


def _split_fragment(address: URL) -> tuple[str, dict[str, str]]:
	fragment = address.fragment.strip()
	if not fragment:
		return "", {}

	if not fragment.startswith("/"):
		fragment = "/" + fragment

	parts = urlsplit(fragment)
	return parts.path, dict(parse_qsl(parts.query, keep_blank_values=True))


def _parse_share_link(address: URL) -> ShareLink:
	if address.path == _VIEWER_PATH:
		checkserialnum = str(address.query.get("checkserialnum") or "").strip()
		if checkserialnum:
			return ShareLink(buss_id=checkserialnum, requires_authority_code=False)

	if address.path.startswith(_SHORTSERVER_PREFIX):
		short_server_id = address.path.rsplit("/", 1)[-1].strip()
		if short_server_id:
			return ShareLink(
				buss_id="",
				requires_authority_code=True,
				short_server_id=short_server_id,
				short_url=short_server_id,
			)

	fragment_path, fragment_query = _split_fragment(address)
	segments = [segment for segment in fragment_path.split("/") if segment]
	if len(segments) >= 2 and segments[0] in {_PHONE_VISIBLE_ROUTE, _INFO_ROUTE, _REDIRECT_ROUTE}:
		buss_id = segments[1]
		forward = fragment_query.get("forward")
		requires_authority_code = segments[0] == _PHONE_VISIBLE_ROUTE or (
			segments[0] == _REDIRECT_ROUTE and forward == _PHONE_VISIBLE_ROUTE
		)
		return ShareLink(
			buss_id=buss_id,
			requires_authority_code=requires_authority_code,
			hide_qrcode=fragment_query.get("hideQrcode"),
			forward=forward,
			short_url=fragment_query.get("shortUrl"),
			id_type=fragment_query.get("idType"),
			sign=fragment_query.get("sign"),
		)

	raise ValueError("当前链接不是受支持的云影像分享链接。")


def requires_authority_code(url: str) -> bool:
	try:
		return _parse_share_link(URL(url)).requires_authority_code
	except ValueError:
		return False


def authority_code_prompt(url: str) -> str | None:
	if requires_authority_code(url):
		return "身份证后四位"
	return None


def _require_verification_params(link: ShareLink):
	if not link.short_url or not link.id_type or not link.sign:
		raise ValueError("该分享链接缺少验证参数，请重新从微信里打开后完整复制链接。")


async def _resolve_short_server_link(client, address: URL, link: ShareLink) -> ShareLink:
	if not link.short_server_id:
		return link

	headers = {
		"Referer": str(address),
		"User-Agent": _WECHAT_USER_AGENT,
	}
	async with client.get(str(address), headers=headers, allow_redirects=False) as response:
		location = response.headers.get("Location")
		if response.status not in {301, 302, 303, 307, 308} or not location:
			raise ValueError("短链没有返回可用的分享地址，站点接口可能已变化。")

	redirect = URL(location) if "://" in location else response.url.join(URL(location))
	return _parse_share_link(redirect)


async def _verify_authority_code(client, address: URL, link: ShareLink, authority_code: str):
	_require_verification_params(link)

	headers = {
		"Content-Type": "application/json;charset=UTF-8",
		"Referer": str(address),
		"User-Agent": _WECHAT_USER_AGENT,
	}
	payload = {
		"idNum": authority_code,
		"bussId": link.buss_id,
		"hideQrcode": link.hide_qrcode or "1",
		"forward": link.forward or _PHONE_VISIBLE_ROUTE,
		"shortUrl": link.short_url,
		"idType": link.id_type,
		"sign": link.sign,
	}
	api_url = address.origin().with_path(_VERIFY_PATH)

	async with client.post(str(api_url), json=payload, headers=headers) as response:
		data = await response.json(content_type=None)

	if data.get("status") == "ok" and data.get("data") is True:
		return

	message = data.get("message") or data.get("msg") or "站点没有通过后四位验证。"
	raise ValueError(f"验证失败，请检查身份证后四位是否正确。{message}")


async def _load_study_xml(client, address: URL, buss_id: str) -> str:
	xml_url = address.origin().with_path(_XML_PATH).with_query({
		"checkserialnum": buss_id,
		"mo": "true",
	})
	async with client.get(str(xml_url), headers={"User-Agent": _WECHAT_USER_AGENT}) as response:
		text = await response.text()
		if response.status != 200:
			raise ValueError("影像数据入口没有返回可用结果，站点接口可能已变化。")

	if "<patient" not in text:
		raise ValueError("影像数据入口没有返回可识别的 XML，站点接口可能已变化。")

	return text


def _parse_int(text: str | None) -> int | None:
	if text is None:
		return None
	try:
		return int(str(text).strip())
	except ValueError:
		return None


def _build_public_storage_url(address: URL, httpurl0: str) -> str:
	base = f"{address.origin()}{_PUBLIC_VIEWER_PREFIX}"

	if "/vnaHttp/" in httpurl0:
		tail = httpurl0.split("/vnaHttp/", 1)[1]
		return f"{base}/vnaHttp/{tail}"

	if httpurl0.startswith(("http://", "https://")):
		source = URL(httpurl0)
		return f"{base}/{source.path_qs.lstrip('/')}"

	if httpurl0.startswith("/"):
		return f"{base}{httpurl0}"

	return f"{base}/{httpurl0.lstrip('/')}"


def _join_image_url(prefix: str, suffix: str) -> str:
	suffix = suffix.strip()
	if prefix.endswith("?") or suffix.startswith("?"):
		return prefix + suffix.lstrip("?")
	if "?" in prefix:
		return f"{prefix}&{suffix.lstrip('?')}"
	return f"{prefix}?{suffix.lstrip('?')}"


def _parse_study_xml(xml_text: str, address: URL) -> StudyEntry:
	root = ET.fromstring(xml_text)
	study_element = root.find("study")
	if study_element is None:
		raise ValueError("影像 XML 里没有 study 节点，站点接口可能已变化。")

	study = StudyEntry(
		checkserialnum=str(study_element.attrib.get("checkserialnum") or study_element.attrib.get("uid") or "").strip(),
		patient_name=str(study_element.attrib.get("patientname") or "匿名").strip() or "匿名",
		modality=str(study_element.attrib.get("devicetypename") or "云影像").strip() or "云影像",
		study_time=str(study_element.attrib.get("studytime") or "").strip(),
		series=[],
	)

	for series_element in study_element.findall("series"):
		images: list[ImageEntry] = []
		for storage in series_element.findall("storage"):
			httpurl0 = str(storage.attrib.get("httpurl0") or "").strip()
			if not httpurl0:
				continue

			prefix = _build_public_storage_url(address, httpurl0)
			for image_index, image_element in enumerate(storage.findall("im")):
				query = (image_element.text or "").strip()
				if not query:
					continue

				instance_number = (
					_parse_int(image_element.attrib.get("num"))
					or _parse_int(image_element.attrib.get("index"))
					or image_index + 1
				)
				images.append(ImageEntry(url=_join_image_url(prefix, query), instance_number=instance_number))

		if not images:
			continue

		images.sort(key=lambda item: (item.instance_number, item.url))
		study.series.append(SeriesEntry(
			series_number=_parse_int(series_element.attrib.get("seriesnumber")),
			description=str(series_element.attrib.get("seriesdescription") or "").strip(),
			images=images,
		))

	if not study.series:
		raise ValueError("影像数据入口有效，但没有返回任何序列。")

	return study


async def _download_study(client, study: StudyEntry):
	save_to = suggest_save_dir(
		study.patient_name,
		study.modality or study.checkserialnum or "云影像",
		study.study_time or study.checkserialnum,
	)
	print(f"保存到: {save_to}")

	for series in study.series:
		label = series.description or (str(series.series_number) if series.series_number is not None else "Unnamed")
		directory = SeriesDirectory(save_to, series.series_number, series.description, len(series.images))
		for index, image in enumerate(tqdm(series.images, desc=label, unit="张")):
			async with client.get(image.url) as response:
				dicom = await response.read()
			directory.get(index, "dcm").write_bytes(dicom)


async def run(share_url, *args):
	address = URL(share_url)
	link = _parse_share_link(address)
	password = args[0] if args and not args[0].startswith("--") else None

	if link.requires_authority_code and not password:
		raise ValueError("该链接需要填写身份证后四位。")

	print("下载影像 DICOM")
	async with new_http_client() as client:
		if link.short_server_id:
			link = await _resolve_short_server_link(client, address, link)

		if link.requires_authority_code:
			await _verify_authority_code(client, address, link, password)

		xml_text = await _load_study_xml(client, address, link.buss_id)
		study = _parse_study_xml(xml_text, address)
		await _download_study(client, study)
