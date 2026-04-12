"""
下载瑞金医院 RJZYV2 影像查看器上的云影像。

该站点使用 WebSocket 传输影像数据，通过 dicomProvider.getImageFileAsync 逐张获取 DICOM 文件。
"""
import asyncio
import re
import sys
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import async_playwright
from tqdm import tqdm
from yarl import URL

from crawlers._browser import launch_browser
from crawlers._utils import make_unique_dir, pathify, suggest_save_dir, write_bytes_atomic, SeriesDirectory

_HOST = "lk-pacsview.rjh.com.cn"
_SHARE_PAGE_PATH = "/web/fore-end/index.html"
_SHARE_FRAGMENT_PATH = "/check-detail-share"
_VIEWER_SELECTOR = ".image_main"

# RJZYV2 store 结构：indexList 而非 studyList，seriesInfo 在根级别。
_WAIT_VIEWER_SCRIPT = """
() => {
	const vm = document.querySelector('.image_main')?.__vue__;
	const store = vm?.['$store']?.state;
	const studyIndex = store?.select_index || 0;
	const study = store?.indexList?.[studyIndex];
	return !!study && Array.isArray(store.seriesInfo) && store.seriesInfo.length > 0;
}
"""
_STUDY_SCRIPT = """
() => {
	const vm = document.querySelector('.image_main')?.__vue__;
	if (!vm) return null;
	const store = vm['$store']?.state || {};
	const studyIndex = store.select_index || 0;
	const study = (store.indexList || [])[studyIndex];
	if (!study) return null;
	return {
		patientName: study.patientName || '匿名',
		studyTime: study.studyTime || '',
		studyId: study.studyid || '',
		xeguId: study.xeGUID || '',
		description: study.studydescribe || study.modality || study.studyid || '云影像',
		modality: study.modality || '',
		series: (store.seriesInfo || []).map((s, i) => ({
			index: i,
			number: s.seriesNumber,
			description: s.seriesDesc || '',
			totalImages: Number(s.imageTotal || 0),
		})),
	};
}
"""
_DOWNLOAD_IMAGE_SCRIPT = """
async ({ xeGuid, seriesIndex, imageIndex }) => {
	const vm = document.querySelector('.image_main')?.__vue__;
	const dp = vm?.['$store']?.state?.dicomProvider;
	if (!dp) throw new Error('影像查看器尚未准备完成。');

	const file = await dp.getImageFileAsync(xeGuid, seriesIndex, imageIndex);
	const reader = new FileReader();
	const base64 = await new Promise((resolve, reject) => {
		reader.onload = () => resolve(reader.result.split(',')[1]);
		reader.onerror = () => reject(reader.error);
		reader.readAsDataURL(file.blob);
	});
	return { data: base64, filename: file.filename };
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
		raise ValueError("当前链接不是受支持的瑞金医院影像链接。")

	fragment_path, fragment_query = _fragment_parts(address)
	share_id = str(fragment_query.get("shareId") or "").strip()
	if address.path == _SHARE_PAGE_PATH and fragment_path == _SHARE_FRAGMENT_PATH and share_id:
		return ShareLink(url=str(address), is_viewer=False)

	# 直接查看器链接（跨域跳转后的 URL）
	if "activeImage.html" in address.path:
		return ShareLink(url=str(address), is_viewer=True)

	raise ValueError("当前链接不是受支持的瑞金医院影像分享链接。")


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


async def _open_viewer(page, share: ShareLink):
	await page.goto(share.url, wait_until="networkidle")
	if not share.is_viewer:
		for _ in range(120):
			btn = page.get_by_text("查看影像")
			if await btn.is_enabled():
				break
			await asyncio.sleep(0.5)
		else:
			raise RuntimeError("查看影像按钮始终不可用")
		await btn.click()
		await page.wait_for_url(re.compile(r".*/activeImage\.html.*"), timeout=60000)
	await page.wait_for_selector(_VIEWER_SELECTOR, timeout=60000)
	await page.wait_for_function(_WAIT_VIEWER_SCRIPT, timeout=120000)


async def _download_image(page, xegu_id: str, series_index: int, image_index: int) -> bytes:
	result = await page.evaluate(_DOWNLOAD_IMAGE_SCRIPT, {
		"xeGuid": xegu_id,
		"seriesIndex": series_index,
		"imageIndex": image_index,
	})
	return b64decode(result["data"])


async def run(url: str, *_):
	share = _parse_share_link(URL(url))

	async with async_playwright() as driver:
		browser = await launch_browser(driver, headless=True)
		try:
			async with await browser.new_context() as context:
				page = await context.new_page()
				await _open_viewer(page, share)
				study = _parse_study_info(await page.evaluate(_STUDY_SCRIPT))
				save_to = _save_dir(study)

				total_images = sum(s.total_images for s in study.series)
				print(f"{study.patient_name}，{len(study.series)} 个序列，共 {total_images} 张。")
				print(f"保存到: {save_to}")

				progress = tqdm(total=total_images, unit="张", file=sys.stdout)
				try:
					for series in study.series:
						label = series.description or f"序列 {series.index + 1}"
						progress.set_description(label)
						sd = SeriesDirectory(save_to, series.number, series.description, series.total_images)
						for i in range(series.total_images):
							data = await _download_image(page, study.xegu_id, series.index, i)
							sd.write_bytes(i, "dcm", data)
							progress.update(1)
						sd.ensure_complete()
				finally:
					progress.close()

				print(f"下载完成，保存位置 {save_to}")
		finally:
			await browser.close()
