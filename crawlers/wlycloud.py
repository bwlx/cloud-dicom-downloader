import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from pydicom import Dataset
from pydicom.dataset import FileMetaDataset
from pydicom.uid import (
	CTImageStorage,
	ComputedRadiographyImageStorage,
	DigitalMammographyXRayImageStorageForPresentation,
	DigitalXRayImageStorageForPresentation,
	ExplicitVRLittleEndian,
	MRImageStorage,
	PYDICOM_IMPLEMENTATION_UID,
	PositronEmissionTomographyImageStorage,
	SecondaryCaptureImageStorage,
	UltrasoundImageStorage,
)
from playwright.async_api import async_playwright
from tqdm import tqdm
from yarl import URL

from crawlers._browser import launch_browser
from crawlers._utils import IncompleteDownloadError, SeriesDirectory, download_bytes, new_http_client, suggest_save_dir

_REPORT_DETAIL_PATH = "/cfilm/getReportDetail"
_VIEWER_PATH = "/api/preDispRender"
_OSS_HOST = "cloud-film.oss-cn-beijing.aliyuncs.com"
_VIEWER_STATS_SCRIPT = """
() => {
	const studies = document.workspaceContext?.studies || [];
	const seriesCount = studies.reduce((count, study) => count + (study.serieses || []).length, 0);
	const imageCount = studies.reduce(
		(count, study) => count + (study.serieses || []).reduce((sum, series) => sum + (series.images || []).length, 0),
		0,
	);
	return { studyCount: studies.length, seriesCount, imageCount };
}
"""
_VIEWER_EXTRACT_SCRIPT = """
() => {
	const studies = document.workspaceContext?.studies || [];
	return studies.map(study => ({
		sdyuid: study.sdyuid,
		name: study.name,
		sex: study.sex,
		age: study.age,
		date: study.date,
		time: study.time,
		access: study.access,
		des: study.des,
		patid: study.patid,
		birthday: study.birthday,
		facid: study.facid,
		baseurl: study.baseurl,
		series: (study.serieses || []).map(series => ({
			srsuid: series.srsuid,
			modality: series.modality,
			num: series.num,
			laterality: series.laterality,
			date: series.date,
			time: series.time,
			des: series.des,
			bodypart: series.bodypart,
			pos: series.pos,
			images: (series.images || []).map(image => ({
				uid: image.uid,
				num: image.num,
				frame_num: image.frame_num,
				oXx: image.oXx,
				oXy: image.oXy,
				oXz: image.oXz,
				oYx: image.oYx,
				oYy: image.oYy,
				oYz: image.oYz,
				posX: image.posX,
				posY: image.posY,
				posZ: image.posZ,
				date: image.date,
				patOrientation: image.patOrientation,
				storebits: image.storebits,
				byte_pp: image.byte_pp,
				sample_pp: image.sample_pp,
				sliceLoc: image.sliceLoc,
				slicethickness: image.slicethickness,
				playrate: image.playrate,
				pixel_pre: image.pixel_pre,
				invert: image.invert,
				imageId: image.imageId,
				slope: image.slope,
				intercept: image.intercept,
				minPixelValue: image.minPixelValue,
				maxPixelValue: image.maxPixelValue,
				windowCenter: image.windowCenter,
				windowWidth: image.windowWidth,
				rows: image.rows,
				columns: image.columns,
				width: image.width,
				height: image.height,
				columnPixelSpacing: image.columnPixelSpacing,
				rowPixelSpacing: image.rowPixelSpacing,
				sizeInBytes: image.sizeInBytes,
				fmt: image.fmt,
				frms: (image.frms || []).map(frame => ({
					num: frame.num,
					furl: frame.furl,
				})),
			})),
		})),
	}));
}
"""
_DATE_PARTS = re.compile(r"\D+")
_TIME_PARTS = re.compile(r"[^0-9.]")


@dataclass(slots=True)
class ShareLink:
	uid: str


@dataclass(slots=True)
class ViewerImage:
	uid: str
	instance_number: int
	frame_count: int
	format: str
	frame_urls: list[str]
	rows: int
	columns: int
	bits_allocated: int
	bits_stored: int
	samples_per_pixel: int
	pixel_representation: int
	image_position: tuple[float, float, float]
	image_orientation: tuple[float, float, float, float, float, float]
	pixel_spacing: tuple[float, float] | None
	slice_location: float | None
	slice_thickness: float | None
	window_center: float | None
	window_width: float | None
	rescale_slope: float | None
	rescale_intercept: float | None
	invert: bool
	acquisition_time: str
	expected_frame_size: int


@dataclass(slots=True)
class ViewerSeries:
	uid: str
	modality: str
	number: int | None
	description: str
	body_part: str
	date: str
	time: str
	images: list[ViewerImage]


@dataclass(slots=True)
class ViewerStudy:
	uid: str
	patient_name: str
	patient_sex: str
	patient_age: str
	patient_id: str
	patient_birthday: str
	study_date: str
	study_time: str
	accession_number: str
	description: str
	facility_id: int | None
	base_url: str
	series: list[ViewerSeries]


def _fragment_query(address: URL) -> dict[str, str]:
	fragment = address.fragment.strip()
	if not fragment:
		return {}

	if not fragment.startswith("/"):
		fragment = "/" + fragment

	parts = urlsplit(fragment)
	return dict(parse_qsl(parts.query, keep_blank_values=True))


def _parse_share_link(address: URL) -> ShareLink:
	if address.host == "rend.wlycloud.com" and address.path == _VIEWER_PATH:
		raise ValueError("查看器链接需要直接走下载入口，不能按分享页解析。")

	query = dict(address.query)
	fragment_query = _fragment_query(address)
	uid = str(fragment_query.get("uid") or query.get("uid") or "").strip()
	if not uid:
		raise ValueError("当前链接不是受支持的万里云分享链接。")

	return ShareLink(uid=uid)


def _coerce_float(value) -> float | None:
	if value in (None, ""):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _coerce_int(value) -> int | None:
	if value in (None, ""):
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _format_dicom_date(text: str) -> str:
	parts = _DATE_PARTS.sub("", str(text or ""))
	return parts[:8]


def _format_dicom_time(text: str) -> str:
	parts = _TIME_PARTS.sub("", str(text or ""))
	if not parts:
		return ""
	if "." in parts:
		base, fraction = parts.split(".", 1)
	else:
		base, fraction = parts, ""

	base = base[:6]
	if len(base) < 2:
		return ""

	hour = int(base[:2])
	minute = int(base[2:4] or "0")
	second = int(base[4:6] or "0")
	if hour >= 24 or minute >= 60 or second >= 60:
		return ""

	if fraction:
		return base + "." + fraction[:6]
	return base


def _parse_viewer_payload(payload: list[dict]) -> list[ViewerStudy]:
	studies: list[ViewerStudy] = []

	for study in payload:
		series_entries: list[ViewerSeries] = []
		for series in study.get("series") or []:
			image_entries: list[ViewerImage] = []
			for image in series.get("images") or []:
				frame_urls = [
					str(frame.get("furl") or "").strip()
					for frame in image.get("frms") or []
					if str(frame.get("furl") or "").strip()
				]
				if not frame_urls and image.get("imageId"):
					frame_urls = [str(image["imageId"]).strip()]

				image_entries.append(ViewerImage(
					uid=str(image.get("uid") or "").strip(),
					instance_number=_coerce_int(image.get("num")) or len(image_entries) + 1,
					frame_count=max(_coerce_int(image.get("frame_num")) or len(frame_urls) or 1, 1),
					format=str(image.get("fmt") or "").strip() or "raw",
					frame_urls=frame_urls,
					rows=_coerce_int(image.get("rows")) or 0,
					columns=_coerce_int(image.get("columns")) or 0,
					bits_allocated=max((_coerce_int(image.get("byte_pp")) or 2) * 8, 8),
					bits_stored=_coerce_int(image.get("storebits")) or 16,
					samples_per_pixel=_coerce_int(image.get("sample_pp")) or 1,
					pixel_representation=_coerce_int(image.get("pixel_pre")) or 0,
					image_position=(
						_coerce_float(image.get("posX")) or 0.0,
						_coerce_float(image.get("posY")) or 0.0,
						_coerce_float(image.get("posZ")) or 0.0,
					),
					image_orientation=(
						_coerce_float(image.get("oXx")) or 0.0,
						_coerce_float(image.get("oXy")) or 0.0,
						_coerce_float(image.get("oXz")) or 0.0,
						_coerce_float(image.get("oYx")) or 0.0,
						_coerce_float(image.get("oYy")) or 0.0,
						_coerce_float(image.get("oYz")) or 0.0,
					),
					pixel_spacing=(
						_coerce_float(image.get("rowPixelSpacing")) or 0.0,
						_coerce_float(image.get("columnPixelSpacing")) or 0.0,
					) if _coerce_float(image.get("rowPixelSpacing")) and _coerce_float(image.get("columnPixelSpacing")) else None,
					slice_location=_coerce_float(image.get("sliceLoc")),
					slice_thickness=_coerce_float(image.get("slicethickness")),
					window_center=_coerce_float(image.get("windowCenter")),
					window_width=_coerce_float(image.get("windowWidth")),
					rescale_slope=_coerce_float(image.get("slope")),
					rescale_intercept=_coerce_float(image.get("intercept")),
					invert=bool(image.get("invert")),
					acquisition_time=str(image.get("date") or "").strip(),
					expected_frame_size=_coerce_int(image.get("sizeInBytes")) or 0,
				))

			image_entries.sort(key=lambda item: (item.instance_number, item.uid))
			series_entries.append(ViewerSeries(
				uid=str(series.get("srsuid") or "").strip(),
				modality=str(series.get("modality") or "").strip() or "OT",
				number=_coerce_int(series.get("num")),
				description=str(series.get("des") or "").strip(),
				body_part=str(series.get("bodypart") or "").strip(),
				date=str(series.get("date") or "").strip(),
				time=str(series.get("time") or "").strip(),
				images=image_entries,
			))

		series_entries.sort(key=lambda item: (item.number or 0, item.description, item.uid))
		studies.append(ViewerStudy(
			uid=str(study.get("sdyuid") or "").strip(),
			patient_name=str(study.get("name") or "匿名").strip() or "匿名",
			patient_sex=str(study.get("sex") or "").strip(),
			patient_age=str(study.get("age") or "").strip(),
			patient_id=str(study.get("patid") or "").strip(),
			patient_birthday=str(study.get("birthday") or "").strip(),
			study_date=str(study.get("date") or "").strip(),
			study_time=str(study.get("time") or "").strip(),
			accession_number=str(study.get("access") or "").strip(),
			description=str(study.get("des") or "").strip(),
			facility_id=_coerce_int(study.get("facid")),
			base_url=str(study.get("baseurl") or "").strip(),
			series=series_entries,
		))

	return studies


def _resolve_viewer_url(payload: dict, address: URL) -> str:
	value = payload.get("val")
	if not isinstance(value, dict):
		raise ValueError("报告详情接口没有返回有效数据。")

	viewer_url = str(value.get("imgPath") or "").strip()
	if not viewer_url:
		raise ValueError("当前分享没有返回影像查看入口。")

	if viewer_url.startswith("//"):
		return "http:" + viewer_url
	if "://" in viewer_url:
		return viewer_url
	return str(address.origin().join(URL(viewer_url)))


async def _fetch_viewer_url(client, address: URL, share: ShareLink) -> str:
	headers = {
		"Accept": "application/json, text/javascript, */*; q=0.01",
		"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
		"Origin": str(address.origin()),
		"Referer": str(address),
	}
	api_url = address.origin().with_path(_REPORT_DETAIL_PATH)
	async with client.post(str(api_url), data={"uid": share.uid}, headers=headers) as response:
		payload = await response.json(content_type=None)

	if payload.get("code") != 0:
		message = payload.get("errMsg") or payload.get("message") or "无法获取报告详情。"
		raise ValueError(message)

	return _resolve_viewer_url(payload, address)


async def _extract_viewer_payload(viewer_url: str) -> list[ViewerStudy]:
	async with async_playwright() as driver:
		browser = await launch_browser(driver, headless=True)
		try:
			async with await browser.new_context() as context:
				page = await context.new_page()
				await page.route(f"**://{_OSS_HOST}/**", lambda route: route.abort())
				await page.goto(viewer_url, wait_until="domcontentloaded")

				last_total = -1
				stable_rounds = 0
				for _ in range(60):
					await page.wait_for_timeout(500)
					stats = await page.evaluate(_VIEWER_STATS_SCRIPT)
					total = int(stats.get("imageCount") or 0)
					if total > 0 and total == last_total:
						stable_rounds += 1
					else:
						stable_rounds = 0
					last_total = total
					if total > 0 and stable_rounds >= 2:
						payload = await page.evaluate(_VIEWER_EXTRACT_SCRIPT)
						studies = _parse_viewer_payload(payload)
						if studies:
							return studies

			raise ValueError("影像查看器没有返回可识别的序列数据，站点接口可能已变化。")
		finally:
			await browser.close()


def _study_label(study: ViewerStudy) -> str:
	if study.description:
		return study.description
	if study.accession_number:
		return study.accession_number
	for series in study.series:
		if series.modality:
			return series.modality
	return "云影像"


def _save_dir(study: ViewerStudy) -> Path:
	return suggest_save_dir(
		study.patient_name,
		_study_label(study),
		f"{study.study_date} {study.study_time}".strip() or study.uid,
	)


def _sop_class_uid(modality: str):
	return {
		"CT": CTImageStorage,
		"CR": ComputedRadiographyImageStorage,
		"DX": DigitalXRayImageStorageForPresentation,
		"MG": DigitalMammographyXRayImageStorageForPresentation,
		"MR": MRImageStorage,
		"PT": PositronEmissionTomographyImageStorage,
		"US": UltrasoundImageStorage,
	}.get(modality.upper(), SecondaryCaptureImageStorage)


def _photometric_interpretation(image: ViewerImage) -> str:
	if image.samples_per_pixel > 1:
		return "RGB"
	return "MONOCHROME1" if image.invert else "MONOCHROME2"


def _build_dicom(study: ViewerStudy, series: ViewerSeries, image: ViewerImage, pixel_data: bytes) -> Dataset:
	ds = Dataset()
	ds.file_meta = FileMetaDataset()

	ds.SOPClassUID = _sop_class_uid(series.modality)
	ds.SOPInstanceUID = image.uid
	ds.StudyInstanceUID = study.uid
	ds.SeriesInstanceUID = series.uid
	ds.Modality = series.modality or "OT"
	ds.PatientName = study.patient_name
	if study.patient_id:
		ds.PatientID = study.patient_id
	if study.patient_sex:
		ds.PatientSex = study.patient_sex
	if study.patient_age:
		ds.PatientAge = study.patient_age
	if study.patient_birthday:
		ds.PatientBirthDate = _format_dicom_date(study.patient_birthday)
	if study.accession_number:
		ds.AccessionNumber = study.accession_number
	if study.description:
		ds.StudyDescription = study.description
	if series.description:
		ds.SeriesDescription = series.description
	if series.body_part:
		ds.BodyPartExamined = series.body_part
	if series.number is not None:
		ds.SeriesNumber = series.number
	ds.InstanceNumber = image.instance_number

	study_date = _format_dicom_date(study.study_date)
	study_time = _format_dicom_time(study.study_time)
	acquisition_time = _format_dicom_time(image.acquisition_time)
	if study_date:
		ds.StudyDate = study_date
		ds.SeriesDate = study_date
		ds.AcquisitionDate = study_date
		ds.ContentDate = study_date
	if study_time:
		ds.StudyTime = study_time
	if acquisition_time:
		ds.AcquisitionTime = acquisition_time
		ds.ContentTime = acquisition_time
	elif study_time:
		ds.SeriesTime = study_time
		ds.ContentTime = study_time

	ds.Rows = image.rows
	ds.Columns = image.columns
	ds.SamplesPerPixel = image.samples_per_pixel
	ds.PhotometricInterpretation = _photometric_interpretation(image)
	if image.samples_per_pixel > 1:
		ds.PlanarConfiguration = 0
	ds.BitsAllocated = image.bits_allocated
	ds.BitsStored = image.bits_stored
	ds.HighBit = max(image.bits_stored - 1, 0)
	ds.PixelRepresentation = image.pixel_representation
	ds.ImageOrientationPatient = list(image.image_orientation)
	ds.ImagePositionPatient = list(image.image_position)
	if image.pixel_spacing:
		ds.PixelSpacing = [image.pixel_spacing[0], image.pixel_spacing[1]]
	if image.slice_location is not None:
		ds.SliceLocation = image.slice_location
	if image.slice_thickness is not None:
		ds.SliceThickness = image.slice_thickness
	if image.window_center is not None:
		ds.WindowCenter = image.window_center
	if image.window_width is not None:
		ds.WindowWidth = image.window_width
	if image.rescale_slope is not None:
		ds.RescaleSlope = image.rescale_slope
	if image.rescale_intercept is not None:
		ds.RescaleIntercept = image.rescale_intercept
	if image.frame_count > 1:
		ds.NumberOfFrames = str(image.frame_count)
	ds.PixelData = pixel_data

	ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
	ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
	ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
	ds.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
	return ds


def _write_dicom_file(study: ViewerStudy, series: ViewerSeries, image: ViewerImage, pixel_data: bytes, filename: Path):
	ds = _build_dicom(study, series, image, pixel_data)
	filename.parent.mkdir(parents=True, exist_ok=True)
	temp = filename.with_name(filename.name + ".part")
	temp.unlink(missing_ok=True)
	try:
		ds.save_as(temp, enforce_file_format=True)
		temp.replace(filename)
	except Exception:
		temp.unlink(missing_ok=True)
		raise


async def _download_image(client, study: ViewerStudy, series: ViewerSeries, image: ViewerImage, label: str) -> bytes:
	if image.format != "raw":
		raise ValueError(f"{label} 返回了不受支持的 {image.format} 格式，当前仅支持 raw。")
	if not image.frame_urls:
		raise ValueError(f"{label} 没有返回可下载的图像地址。")

	chunks = []
	for frame_index, frame_url in enumerate(image.frame_urls):
		chunks.append(await download_bytes(client, frame_url, label=f"{label} 第 {frame_index + 1} 帧"))

	body = b"".join(chunks)
	expected = image.expected_frame_size * max(len(image.frame_urls), 1)
	if expected and len(body) != expected:
		raise IncompleteDownloadError(f"{label} 下载不完整，预期 {expected} 字节，实际 {len(body)} 字节。")
	return body


async def _download_study(client, study: ViewerStudy):
	save_to = _save_dir(study)
	print(f"保存到: {save_to}")

	for series in study.series:
		if not series.images:
			continue

		label = series.description or (str(series.number) if series.number is not None else series.modality or "Unnamed")
		directory = SeriesDirectory(save_to, series.number, series.description or label, len(series.images))

		for index, image in enumerate(tqdm(series.images, desc=label, unit="张")):
			pixel_data = await _download_image(client, study, series, image, f"{label} 第 {index + 1} 张")
			path = directory.get(index, "dcm")
			_write_dicom_file(study, series, image, pixel_data, path)
			directory.mark_complete(index)

		directory.ensure_complete()


async def run(share_url, *_):
	address = URL(share_url)
	print("下载万里云影像 DICOM")

	if address.host == "rend.wlycloud.com" and address.path == _VIEWER_PATH:
		viewer_url = share_url
	else:
		share = _parse_share_link(address)
		async with new_http_client() as client:
			viewer_url = await _fetch_viewer_url(client, address, share)

	studies = await _extract_viewer_payload(viewer_url)
	if not studies:
		raise ValueError("影像查看器有效，但没有返回任何可下载的检查。")

	async with new_http_client() as client:
		for study in studies:
			await _download_study(client, study)
