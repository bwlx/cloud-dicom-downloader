import re
import sys
from dataclasses import dataclass

from tqdm import tqdm
from yarl import URL

from crawlers._utils import SeriesDirectory, new_http_client, suggest_save_dir

_REPORT_HOST = "efilm.fs-salon.cn"
_API_ORIGIN = URL("https://efilmapi.fs-salon.cn")
_REPORT_PATHS = {"/index", "/index.html"}
_CLOUDFILM_PATH = "/cloudFilm"
_DETAIL_PATH = "/api/v1.0/Report/detail"
_WADO_GET_STUDY_INFO = "/GetStudyInfo"
_WADO_GET_SERIES_META = "/GetSeriesMeta"


@dataclass(slots=True)
class ShareLink:
	report_no: str
	hospital_code: str


@dataclass(slots=True)
class StudyInfo:
	patient_name: str
	study_label: str
	datetime_key: str
	wado_url: str
	study_uid: str
	hospital_code: str
	depart_code: str


def _normalized_datetime(detail: dict) -> str:
	for value in (
		detail.get("checkDate"),
		detail.get("studyDate"),
		detail.get("reportTime"),
		detail.get("studyUId"),
		detail.get("reportNo"),
	):
		text = str(value or "").strip()
		if text:
			return re.sub(r"\D", "", text) or text
	return "study"


def _study_label(detail: dict) -> str:
	return (
		str(detail.get("checkPart") or "").strip()
		or str(detail.get("device") or "").strip()
		or str(detail.get("reportNo") or "").strip()
		or "云影像"
	)


def _parse_share_link(address: URL) -> ShareLink:
	if address.host != _REPORT_HOST:
		raise ValueError("当前链接不是受支持的云胶片分享链接。")

	if address.path in _REPORT_PATHS:
		report_no = str(address.query.get("barcode") or "").strip()
		hospital_code = str(address.query.get("hospitalcode") or "").strip()
	elif address.path == _CLOUDFILM_PATH:
		report_no = str(address.query.get("reportid") or "").strip()
		hospital_code = str(address.query.get("hospitalCode") or "").strip()
	else:
		raise ValueError("当前链接不是受支持的云胶片分享链接。")

	if not report_no or not hospital_code:
		raise ValueError("云胶片链接缺少必要参数。")

	return ShareLink(report_no=report_no, hospital_code=hospital_code)


def _parse_report_detail(payload: dict) -> StudyInfo:
	if payload.get("statusCode") != 200 or not payload.get("result"):
		message = payload.get("message") or "站点没有返回有效的检查详情。"
		raise ValueError(message)

	detail = payload["result"]
	wado_url = str(detail.get("imServerUrl") or "").strip()
	study_uid = str(detail.get("studyUId") or "").strip()
	hospital_code = str(detail.get("orgCode") or "").strip()
	depart_code = str(detail.get("departCode") or "").strip()

	if not detail.get("hasCloudFilm"):
		raise ValueError("该检查没有开放原始影像。")
	if not wado_url or not study_uid or not hospital_code:
		raise ValueError("站点返回的影像元数据不完整，无法继续下载。")

	return StudyInfo(
		patient_name=str(detail.get("name") or "匿名").strip() or "匿名",
		study_label=_study_label(detail),
		datetime_key=_normalized_datetime(detail),
		wado_url=wado_url.rstrip("/"),
		study_uid=study_uid,
		hospital_code=hospital_code,
		depart_code=depart_code,
	)


def _wado_params(study: StudyInfo, *, image_type: int = 1) -> dict[str, str]:
	value = str(image_type)
	return {
		"hospID": study.hospital_code,
		"hospid": study.hospital_code,
		"hospId": study.hospital_code,
		"hospitalid": study.hospital_code,
		"imageType": value,
		"isDcm": value,
		"studyUID": study.study_uid,
		"studyuid": study.study_uid,
		"departCode": study.depart_code,
		"hasDesensitize": "0",
		"isInternal": "0",
		"isKeyImage": "0",
	}


def _series_identity(series: dict) -> str:
	return str(series.get("uuid") or series.get("uid") or "").strip()


def _parse_wado_response(payload: dict, *, error_message: str):
	if payload.get("success") and payload.get("data") is not None:
		return payload["data"]

	message = payload.get("msg") or error_message
	raise ValueError(message)


async def _request_report_detail(client, share: ShareLink) -> StudyInfo:
	api_url = str(_API_ORIGIN.with_path(_DETAIL_PATH))
	async with client.get(api_url, params={
		"hospitalCode": share.hospital_code,
		"reportNo": share.report_no,
		"authenCode": "",
		"autuType": "0",
	}) as response:
		return _parse_report_detail(await response.json(content_type=None))


async def _request_study_data(client, study: StudyInfo) -> dict:
	url = str(URL(study.wado_url + _WADO_GET_STUDY_INFO).with_query(_wado_params(study)))
	async with client.get(url) as response:
		payload = await response.json(content_type=None)
	return _parse_wado_response(payload, error_message="站点没有返回可识别的检查序列。")


async def _request_series_data(client, study: StudyInfo, series: dict) -> dict:
	series_id = _series_identity(series)
	if not series_id:
		raise ValueError("站点返回的序列缺少唯一标识。")

	params = _wado_params(study)
	params.update({
		"seriesUID": series_id,
		"uuid": series_id,
		"clientType": "0",
	})
	url = str(URL(study.wado_url + _WADO_GET_SERIES_META).with_query(params))
	async with client.get(url) as response:
		payload = await response.json(content_type=None)
	return _parse_wado_response(payload, error_message=f"无法读取序列元数据：{series.get('desp') or series.get('num') or series_id}")


async def run(url: str, *_):
	share = _parse_share_link(URL(url))
	origin = URL(url).origin()

	async with new_http_client(origin) as client:
		study = await _request_report_detail(client, share)
		study_data = await _request_study_data(client, study)
		series_list = list(study_data.get("serieses") or [])
		if not series_list:
			raise ValueError("站点没有返回任何影像序列。")

		save_to = suggest_save_dir(study.patient_name, study.study_label, study.datetime_key)
		print(f"下载 {study.patient_name} 的 DICOM，共 {len(series_list)} 个序列。")
		print(f"保存到: {save_to}\n")

		for series in series_list:
			series_data = await _request_series_data(client, study, series)
			images = list(series_data.get("imgs") or [])
			if not images:
				continue

			desc = str(series.get("desp") or "").strip() or "Unnamed"
			number = series.get("num")
			directory = SeriesDirectory(
				save_to,
				int(number) if str(number or "").strip() else None,
				desc,
				len(images),
			)

			progress = tqdm(images, desc=desc, unit="张", file=sys.stdout)
			try:
				for index, image in enumerate(progress):
					image_url = str(image.get("url") or "").strip()
					if not image_url:
						raise ValueError(f"{desc} 缺少原始影像下载地址。")
					label = f"{desc} 第 {index + 1} 张"
					await directory.download(client, index, "dcm", image_url, label=label)
			finally:
				progress.close()

			directory.ensure_complete()
