import sys
from dataclasses import dataclass

from tqdm import tqdm
from yarl import URL

from crawlers._utils import SeriesDirectory, new_http_client, suggest_save_dir

_SHORT_URL_SOURCE = 103
_ENTRANCE_PATH = "/CloudHospital/EntranceValidate"
_STUDY_DOCUMENTS_PATH = "/CloudHospital/Study/StudyMedicaldocuments"
_FRONT_END_DATA_PATH = "/FrontEndData"


@dataclass(slots=True)
class ShortLink:
	short_url: str


@dataclass(slots=True)
class StudyBaseInfo:
	hospital_id: int
	ssystem_id: int
	patient_id: str
	accession_number: str
	study_key: int


@dataclass(slots=True)
class ViewerAccess:
	url: str
	token: str
	web_api_url: str
	hospital_id: str
	source: str
	accession_number: str


def _build_origin(address: URL, port: int) -> URL:
	return URL.build(scheme=address.scheme or "https", host=address.host, port=port)


def _parse_short_link(address: URL) -> ShortLink:
	if not (address.host or "").endswith(".efilmcloud.com"):
		raise ValueError("当前链接不是受支持的富医睿影分享链接。")

	if address.path == "/" and address.query.get("token") and address.query.get("webApiUrl"):
		raise ValueError("当前链接是影像查看器链接，不是短链。")

	parts = [part for part in address.path.split("/") if part]
	if len(parts) != 1:
		raise ValueError("当前链接不是受支持的富医睿影分享链接。")

	return ShortLink(short_url=parts[0])


def _parse_viewer_access(url: str, fallback_accession_number: str = "") -> ViewerAccess:
	address = URL(url)
	query = address.query
	token = str(query.get("token") or "").strip()
	web_api_url = str(query.get("webApiUrl") or "").strip()
	hospital_id = str(query.get("hID") or "").strip()
	source = str(query.get("source") or "").strip()
	accession_number = str(query.get("accNum") or fallback_accession_number or "").strip()

	if not token or not web_api_url or not hospital_id or not source:
		raise ValueError("影像查看器链接缺少必要参数，无法继续下载。")

	return ViewerAccess(
		url=url,
		token=token,
		web_api_url=web_api_url.rstrip("/"),
		hospital_id=hospital_id,
		source=source,
		accession_number=accession_number,
	)


def _authorized_headers(token: str) -> dict[str, str]:
	return {
		"apikey": token,
		"content-type": "application/json-patch+json",
	}


def _viewer_headers(viewer: ViewerAccess) -> dict[str, str]:
	return {
		"Accept": "application/json",
		"Authorization": f"Bearer {viewer.token}",
		"Content-Type": "application/json",
	}


def _download_headers(viewer: ViewerAccess) -> dict[str, str]:
	return {"Authorization": f"Bearer {viewer.token}"}


def _parse_study_base_info(payload: dict) -> tuple[str, StudyBaseInfo]:
	if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
		message = (payload.get("error") or {}).get("errMessage") if isinstance(payload.get("error"), dict) else None
		raise ValueError(message or "短链验证失败，站点没有返回检查信息。")

	data = payload["data"]
	token = str(data.get("token") or "").strip()
	info = data.get("studyBaseInfo")
	if not token or not isinstance(info, dict):
		raise ValueError("短链验证成功，但没有返回可用的访问令牌或检查参数。")

	return token, StudyBaseInfo(
		hospital_id=int(info["hospitalID"]),
		ssystem_id=int(info["ssystemID"]),
		patient_id=str(info["patientID"]),
		accession_number=str(info["accNum"]),
		study_key=int(info["studyKey"]),
	)


def _extract_dicom_viewer_url(payload: dict) -> str:
	if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
		message = (payload.get("error") or {}).get("errMessage") if isinstance(payload.get("error"), dict) else None
		raise ValueError(message or "站点没有返回可用的医疗文书信息。")

	dicom = payload["data"].get("dicom")
	entries = dicom.get("dicomMedicaldocumentInfos") if isinstance(dicom, dict) else None
	if not entries:
		raise ValueError("该检查没有开放 DICOM 影像下载。")

	viewer_url = str(entries[0].get("url") or "").strip()
	if not viewer_url:
		raise ValueError("DICOM 影像入口缺少查看器链接。")

	return viewer_url


def _study_datetime(study: dict) -> str:
	for key in ("studyDate", "studyTime"):
		text = str(study.get(key) or "").strip()
		if text:
			return text

	for series in study.get("series") or []:
		text = str(series.get("seriesTime") or "").strip()
		if text:
			return text

	for key in ("accessionNumber", "studyInstanceUid"):
		text = str(study.get(key) or "").strip()
		if text:
			return text
	return "study"


def _series_sort_key(series: dict):
	try:
		number = int(series.get("seriesId") or 0)
	except (TypeError, ValueError):
		number = 0
	return number, str(series.get("seriesDesc") or ""), str(series.get("seriesUid") or "")


def _image_sort_key(image: dict):
	try:
		number = int(image.get("instanceNumber") or 0)
	except (TypeError, ValueError):
		number = 0
	return number, str(image.get("objestInstanceUid") or "")


def _series_number(series: dict) -> int | None:
	try:
		return int(series.get("seriesId"))
	except (TypeError, ValueError):
		return None


async def _resolve_short_link(client, address: URL) -> tuple[str, StudyBaseInfo]:
	link = _parse_short_link(address)
	api_url = str(_build_origin(address, 8809).with_path(_ENTRANCE_PATH))
	async with client.post(api_url, json={
		"urlSource": _SHORT_URL_SOURCE,
		"shortUrl": link.short_url,
		"isOldVersionUrl": False,
	}) as response:
		payload = await response.json(content_type=None)

	return _parse_study_base_info(payload)


async def _load_study_documents(client, address: URL, token: str, info: StudyBaseInfo) -> str:
	api_url = str(_build_origin(address, 8809).with_path(_STUDY_DOCUMENTS_PATH))
	async with client.get(api_url, params={
		"accNum": info.accession_number,
		"hospitalID": info.hospital_id,
		"isUrlForNote": "false",
		"patientID": info.patient_id,
		"ssystemID": info.ssystem_id,
		"studyKey": info.study_key,
		"stamp": "3",
	}, headers=_authorized_headers(token)) as response:
		payload = await response.json(content_type=None)

	return _extract_dicom_viewer_url(payload)


async def _load_front_end_data(client, viewer: ViewerAccess) -> dict:
	api_url = str(URL(viewer.web_api_url).with_path(_FRONT_END_DATA_PATH))
	async with client.get(api_url, params={
		"token": viewer.token,
		"hID": viewer.hospital_id,
		"source": viewer.source,
		"accNum": viewer.accession_number,
	}, headers=_viewer_headers(viewer)) as response:
		payload = await response.json(content_type=None)

	study = payload.get("studyInfo")
	if not isinstance(study, dict):
		raise ValueError("影像查看器没有返回可识别的检查数据。")
	return study


async def _resolve_viewer(client, url: str) -> ViewerAccess:
	address = URL(url)
	if address.path == "/" and address.query.get("token") and address.query.get("webApiUrl"):
		return _parse_viewer_access(url)

	token, info = await _resolve_short_link(client, address)
	viewer_url = await _load_study_documents(client, address, token, info)
	return _parse_viewer_access(viewer_url, fallback_accession_number=info.accession_number)


async def run(url: str, *_):
	async with new_http_client() as client:
		viewer = await _resolve_viewer(client, url)
		study = await _load_front_end_data(client, viewer)
		series_list = sorted(study.get("series") or [], key=_series_sort_key)
		if not series_list:
			raise ValueError("影像查看器没有返回任何序列。")

		save_to = suggest_save_dir(
			str(study.get("patientName") or "匿名").strip() or "匿名",
			str(study.get("studyDescription") or study.get("accessionNumber") or "云影像").strip() or "云影像",
			_study_datetime(study),
		)

		print(f"下载 {study.get('patientName') or '匿名'} 的 DICOM，共 {len(series_list)} 个序列。")
		print(f"保存到: {save_to}\n")

		for series in series_list:
			images = sorted(series.get("images") or [], key=_image_sort_key)
			if not images:
				continue

			desc = str(series.get("seriesDesc") or "").strip() or "Unnamed"
			directory = SeriesDirectory(save_to, _series_number(series), desc, len(images), resume=True)
			progress = tqdm(images, desc=desc, unit="张", file=sys.stdout)
			try:
				for index, image in enumerate(progress):
					wado_url = str(image.get("wadoUrl") or image.get("localWadoUrl") or "").strip()
					if not wado_url:
						raise ValueError(f"{desc} 第 {index + 1} 张缺少 DICOM 下载地址。")
					await directory.download(
						client,
						index,
						"dcm",
						wado_url,
						headers=_download_headers(viewer),
						label=f"{desc} 第 {index + 1} 张",
					)
			finally:
				progress.close()

			directory.ensure_complete()
