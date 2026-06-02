import json
import sys
from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from pydicom import dcmread
from tqdm import tqdm
from yarl import URL

from crawlers._utils import SeriesDirectory, new_http_client, suggest_save_dir


_HOST = "mic.ljjk.org.cn"
_MOBILE_PATH = "/NeuView/mobile/"
_RIS_PACS_PATH = "/nwservice/rispacsresp"
_MOBILE_WECHAT_UA = (
	"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
	"AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
	"MicroMessenger/8.0.45 NetType/WIFI Language/zh_CN"
)


@dataclass(slots=True)
class ShareLink:
	token: str
	b_type: str


@dataclass(slots=True)
class ImageInfo:
	url: str
	instance_number: int | None


@dataclass(slots=True)
class SeriesInfo:
	number: int | None
	description: str
	images: list[ImageInfo]


@dataclass(slots=True)
class StudyInfo:
	patient_name: str
	modality: str
	description: str
	time_key: str
	series: list[SeriesInfo]


def _split_fragment(fragment: str) -> tuple[str, dict[str, str]]:
	token, _, query = fragment.partition("&")
	params = {}
	for part in query.split("&"):
		if not part:
			continue
		key, _, value = part.partition("=")
		params[key] = value
	return token.strip(), params


def _parse_share_link(address: URL) -> ShareLink:
	if address.host != _HOST or address.path != _MOBILE_PATH:
		raise ValueError("当前链接不是受支持的龙江医学影像云分享链接。")

	token, params = _split_fragment(address.fragment)
	if not token:
		raise ValueError("龙江医学影像云分享链接缺少检查标识。")

	return ShareLink(token=token, b_type=(params.get("bType") or "2d").strip() or "2d")


def _strip_scheme_prefix(value: str) -> str:
	text = str(value or "").strip()
	if text.startswith("PK:"):
		return text[3:]
	return text


def _to_int(value) -> int | None:
	try:
		return int(str(value).strip())
	except (TypeError, ValueError):
		return None


def _parse_images_metadata(value) -> dict[str, dict]:
	if not value:
		return {}
	if isinstance(value, str):
		try:
			items = json.loads(value)
		except json.JSONDecodeError:
			return {}
	else:
		items = value
	if not isinstance(items, list):
		return {}
	return {
		str(item.get("fileHash") or "").strip(): item
		for item in items
		if isinstance(item, dict) and str(item.get("fileHash") or "").strip()
	}


def _parse_study(payload: dict) -> StudyInfo:
	if payload.get("code") != "2000" or not isinstance(payload.get("data"), dict):
		raise ValueError(str(payload.get("msg") or payload.get("message") or "龙江医学影像云没有返回可下载影像。"))

	data = payload["data"]
	series_list: list[SeriesInfo] = []
	for index, raw_series in enumerate(data.get("series") or [], start=1):
		if not isinstance(raw_series, dict):
			continue
		metadata = _parse_images_metadata(raw_series.get("images"))
		images: list[ImageInfo] = []
		for raw in raw_series.get("image") or []:
			url = _strip_scheme_prefix(raw)
			if not url:
				continue
			file_hash = URL(url).path.rsplit("/", 1)[-1]
			item = metadata.get(file_hash, {})
			images.append(ImageInfo(url=url, instance_number=_to_int(item.get("instanceNumber"))))
		if not images:
			continue
		series_list.append(SeriesInfo(
			number=_to_int(raw_series.get("seriesnumber")) or index,
			description=str(raw_series.get("seriesdescription") or raw_series.get("seriesuniqueid") or data.get("modality") or "Unnamed"),
			images=images,
		))

	if not series_list:
		raise ValueError("龙江医学影像云没有返回可下载的 DICOM 序列。")

	return StudyInfo(
		patient_name=str(data.get("patientname") or "匿名").strip() or "匿名",
		modality=str(data.get("modality") or "").strip(),
		description=str(data.get("studydescription") or data.get("modality") or "云影像").strip() or "云影像",
		time_key=str(data.get("checktime") or data.get("studydate") or data.get("checkserialnum") or "study").strip() or "study",
		series=series_list,
	)


async def _load_study(client, address: URL, share: ShareLink) -> StudyInfo:
	url = str(address.origin().with_path(f"{_RIS_PACS_PATH}/{share.token}"))
	async with client.get(url, params={"bType": share.b_type}) as response:
		payload = await response.json(content_type=None)
	return _parse_study(payload)


def _extract_dicom_from_zip(body: bytes) -> bytes:
	try:
		with ZipFile(BytesIO(body)) as archive:
			for item in archive.infolist():
				if item.is_dir():
					continue
				data = archive.read(item)
				if len(data) >= 132 and data[128:132] == b"DICM":
					return data
	except BadZipFile as exc:
		raise ValueError("龙江医学影像云返回的影像压缩包格式无效。") from exc
	raise ValueError("龙江医学影像云返回的影像压缩包内没有 DICOM 文件。")


def _study_save_dir(study: StudyInfo):
	return suggest_save_dir(study.patient_name, study.description or study.modality or "云影像", study.time_key)


async def _download_zip_dicom(client, image: ImageInfo, label: str) -> bytes:
	async with client.get(image.url) as response:
		body = await response.read()
	dicom = _extract_dicom_from_zip(body)
	try:
		dcmread(BytesIO(dicom), stop_before_pixels=True)
	except Exception as exc:
		raise ValueError(f"{label} 不是有效的 DICOM 文件。") from exc
	return dicom


async def run(share_url: str, *_):
	address = URL(share_url)
	share = _parse_share_link(address)
	async with new_http_client(headers={"User-Agent": _MOBILE_WECHAT_UA}) as client:
		study = await _load_study(client, address, share)
		save_to = _study_save_dir(study)
		total = sum(len(series.images) for series in study.series)
		print(f"下载 {study.patient_name} 的龙江医学影像云 DICOM，共 {total} 张。")
		print(f"保存到: {save_to}")

		progress = tqdm(total=total, unit="张", file=sys.stdout)
		for series in study.series:
			images = sorted(
				enumerate(series.images),
				key=lambda item: item[1].instance_number if item[1].instance_number is not None else item[0] + 1,
			)
			directory = SeriesDirectory(save_to, series.number, series.description, len(images), resume=True)
			for local_index, (_, image) in enumerate(images):
				path = directory.get(local_index, "dcm")
				if path.exists():
					directory.mark_complete(local_index)
					progress.update(1)
					continue
				label = f"{series.description} 第 {local_index + 1} 张"
				dicom = await _download_zip_dicom(client, image, label)
				directory.write_bytes(local_index, "dcm", dicom)
				progress.update(1)
			directory.ensure_complete()

		progress.close()
		print(f"下载完成，保存位置 {save_to}")
