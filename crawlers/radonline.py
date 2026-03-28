import asyncio
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from zipfile import BadZipFile, ZipFile

from playwright.async_api import async_playwright
from tqdm import tqdm
from yarl import URL

from crawlers._browser import launch_browser
from crawlers._utils import IncompleteDownloadError, make_unique_dir, pathify, suggest_save_dir, write_bytes_atomic

_HOST = "film.radonline.cn"
_SHARE_PAGE_PATH = "/web/fore-end/index.html"
_SHARE_FRAGMENT_PATH = "/check-detail-share"
_VIEWER_PATH = "/webImageSyn/activeImage.html"
_VIEWER_SELECTOR = ".image_main"
_DOWNLOAD_TIMEOUT_MS = 30 * 60 * 1000
_WAIT_VIEWER_SCRIPT = """
() => {
	const vm = document.querySelector('.image_main')?.__vue__;
	const store = vm?.['$store']?.state;
	const studyIndex = store?.select_index || 0;
	const study = store?.studyList?.[studyIndex];
	return !!study && Array.isArray(study.seriesInfo) && study.seriesInfo.length > 0;
}
"""
_STUDY_SCRIPT = """
() => {
	const vm = document.querySelector('.image_main')?.__vue__;
	if (!vm) {
		return null;
	}
	const store = vm['$store']?.state || {};
	const studyIndex = store.select_index || 0;
	const study = (store.studyList || [])[studyIndex];
	if (!study) {
		return null;
	}
	return {
		patientName: study.patientName || '匿名',
		studyTime: study.studyTime || '',
		studyId: study.studyid || '',
		xeguId: study.xeGUID || '',
		description: study.studydescribe || study.modality || study.studyid || '云影像',
		modality: study.modality || '',
		series: (study.seriesInfo || []).map((series, index) => ({
			index,
			number: series.seriesNumber,
			description: series.seriesDesc || '',
			totalImages: Number(series.imageTotal || 0),
		})),
	};
}
"""
_DOWNLOAD_SERIES_SCRIPT = """
({ seriesIndex }) => {
	const vm = document.querySelector('.image_main')?.__vue__;
	if (!vm) {
		throw new Error('影像查看器尚未准备完成。');
	}
	const store = vm['$store']?.state || {};
	const studyIndex = store.select_index || 0;
	const study = (store.studyList || [])[studyIndex];
	if (!study) {
		throw new Error('影像查看器没有返回检查数据。');
	}
	const series = (study.seriesInfo || [])[seriesIndex];
	if (!series) {
		throw new Error('影像查看器没有返回指定序列。');
	}
	vm.layoutData[vm.sequenceIndex].bind_xeguid = study.xeGUID;
	vm.layoutData[vm.sequenceIndex].seriesId = seriesIndex;
	vm.layoutData[vm.sequenceIndex].totalImage = Number(series.imageTotal || 0);
	return vm.downOriginal('CURRENTSERIES');
}
"""


@dataclass(slots=True)
class ShareLink:
	url: str
	is_viewer: bool


@dataclass(slots=True)
class SeriesInfo:
	index: int
	number: int | None
	description: str
	total_images: int


@dataclass(slots=True)
class StudyInfo:
	patient_name: str
	study_time: str
	study_id: str
	xegu_id: str
	description: str
	modality: str
	series: list[SeriesInfo]


def _fragment_parts(address: URL) -> tuple[str, dict[str, str]]:
	fragment = address.fragment.strip()
	if not fragment:
		return "", {}

	if not fragment.startswith("/"):
		fragment = "/" + fragment

	parts = urlsplit(fragment)
	return parts.path, dict(parse_qsl(parts.query, keep_blank_values=True))


def _parse_int(value) -> int | None:
	if value in (None, ""):
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _parse_share_link(address: URL) -> ShareLink:
	if address.host != _HOST:
		raise ValueError("当前链接不是受支持的锐达云影像链接。")

	if address.path == _VIEWER_PATH and address.query.get("mergeParameters"):
		return ShareLink(url=str(address), is_viewer=True)

	fragment_path, fragment_query = _fragment_parts(address)
	share_id = str(fragment_query.get("shareId") or "").strip()
	if address.path == _SHARE_PAGE_PATH and fragment_path == _SHARE_FRAGMENT_PATH and share_id:
		return ShareLink(url=str(address), is_viewer=False)

	raise ValueError("当前链接不是受支持的锐达云影像分享链接。")


def _parse_study_info(payload: dict | None) -> StudyInfo:
	if not isinstance(payload, dict):
		raise ValueError("影像查看页没有返回可识别的检查信息。")

	series = [
		SeriesInfo(
			index=int(item.get("index") or 0),
			number=_parse_int(item.get("number")),
			description=str(item.get("description") or "").strip(),
			total_images=max(int(item.get("totalImages") or 0), 0),
		)
		for item in payload.get("series") or []
	]

	if not series:
		raise ValueError("影像查看页没有返回任何序列信息。")

	return StudyInfo(
		patient_name=str(payload.get("patientName") or "匿名").strip() or "匿名",
		study_time=str(payload.get("studyTime") or "").strip(),
		study_id=str(payload.get("studyId") or "").strip(),
		xegu_id=str(payload.get("xeguId") or "").strip(),
		description=str(payload.get("description") or "").strip(),
		modality=str(payload.get("modality") or "").strip(),
		series=series,
	)


def _study_label(study: StudyInfo) -> str:
	if study.description:
		return study.description
	if study.modality:
		return study.modality
	if study.study_id:
		return study.study_id
	return "云影像"


def _save_dir(study: StudyInfo) -> Path:
	time_key = re.sub(r"\D", "", study.xegu_id or study.study_time) or study.study_id or "study"
	return make_unique_dir(suggest_save_dir(study.patient_name, _study_label(study), time_key))


def _series_dir_name(study_dir: Path, series: SeriesInfo) -> Path:
	desc = pathify(series.description)
	number = series.number
	if desc and number is None:
		return make_unique_dir(study_dir / desc)
	if desc:
		return make_unique_dir(study_dir / f"[{number}] {desc}")
	if number is None:
		return make_unique_dir(study_dir / "Unnamed")
	return make_unique_dir(study_dir / str(number))


def _normalize_entry_name(name: str) -> str:
	path = Path(name)
	if path.suffix.lower() == ".pk":
		path = path.with_suffix("")
	return path.name


def _extract_series_archive(archive_path: Path, target_dir: Path, expected_files: int):
	try:
		with ZipFile(archive_path) as archive:
			names = [item for item in archive.infolist() if not item.is_dir() and Path(item.filename).name]
			if not names:
				raise IncompleteDownloadError(f"{archive_path.name} 不包含任何影像文件。")

			for item in names:
				target_name = _normalize_entry_name(item.filename)
				with archive.open(item) as source:
					write_bytes_atomic(target_dir / target_name, source.read())
	except BadZipFile as exc:
		raise ValueError(f"{archive_path.name} 不是有效的影像压缩包。") from exc

	series_files = [path for path in target_dir.iterdir() if path.is_file()]
	actual_files = len(series_files)
	if actual_files != expected_files:
		raise IncompleteDownloadError(
			f"{target_dir} 下载不完整，预期 {expected_files} 张，实际 {actual_files} 张。"
		)


async def _open_viewer(page, share: ShareLink):
	await page.goto(share.url, wait_until="networkidle")
	if not share.is_viewer:
		await page.get_by_text("查看影像").click()
		await page.wait_for_url(re.compile(r".*/webImageSyn/activeImage\.html.*"), timeout=60000)
	await page.wait_for_selector(_VIEWER_SELECTOR, timeout=60000)
	await page.wait_for_function(_WAIT_VIEWER_SCRIPT, timeout=120000)


async def _download_series_archive(page, series: SeriesInfo, temp_dir: Path) -> Path:
	label = series.description or (f"序列 {series.index + 1}")

	for attempt in range(1, 4):
		try:
			async with page.expect_download(timeout=_DOWNLOAD_TIMEOUT_MS) as download_info:
				await page.evaluate(_DOWNLOAD_SERIES_SCRIPT, {"seriesIndex": series.index})
			download = await download_info.value
			target = temp_dir / download.suggested_filename
			target.unlink(missing_ok=True)
			await download.save_as(str(target))
			return target
		except Exception:
			if attempt >= 3:
				raise
			await asyncio.sleep(min(2 ** (attempt - 1), 4))
			print(f"{label} 下载包生成失败，正在重试（第 {attempt + 1} 次，共 3 次）。")

	raise RuntimeError("unreachable")


async def run(url: str, *_):
	share = _parse_share_link(URL(url))

	async with async_playwright() as driver:
		browser = await launch_browser(driver, headless=True)
		try:
			async with await browser.new_context(accept_downloads=True) as context:
				page = await context.new_page()
				await _open_viewer(page, share)
				study = _parse_study_info(await page.evaluate(_STUDY_SCRIPT))
				save_to = _save_dir(study)

				print(f"{study.patient_name}，{len(study.series)} 个序列。")
				print(f"保存到: {save_to}")

				with tempfile.TemporaryDirectory() as temp_root:
					temp_dir = Path(temp_root)
					progress = tqdm(study.series, unit="序列", file=sys.stdout)
					try:
						for series in progress:
							label = series.description or (f"序列 {series.index + 1}")
							progress.set_description(label)
							archive = await _download_series_archive(page, series, temp_dir)
							target_dir = _series_dir_name(save_to, series)
							_extract_series_archive(archive, target_dir, series.total_images)
							archive.unlink(missing_ok=True)
					finally:
						progress.close()

				print(f"下载完成，保存位置 {save_to}")
		finally:
			await browser.close()
