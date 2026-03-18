import re

from yarl import URL

from crawlers._utils import new_http_client
from crawlers.hinacom import HinacomDownloader

# 元素属性很简单，就直接正则了，懒得再下个解析库。
_hidden_input_re = re.compile(r'<input type="hidden" id="StudyId" name="StudyId" value="([^"]+)" />')


def _resolve_entry(address: URL, study_page_html: str | None = None) -> tuple[str, bool] | None:
	"""
	返回实际可进入下载器的地址，以及该地址是否已经是最终的 ImageViewer 页面。
	"""
	if address.path.endswith("/ImageViewer/StudyView"):
		if address.query.get("StudyId"):
			return str(address), True

		return_url = address.query.get("returnUrl")
		if return_url:
			return return_url, False

	if address.path.endswith("/Study/ViewImage"):
		return str(address), False

	if study_page_html:
		fields = _hidden_input_re.search(study_page_html)
		if fields:
			sid = fields.group(1)
			return f"{address.origin()}/Study/ViewImage?studyId={sid}", False

	return_url = address.query.get("returnUrl")
	if return_url:
		return return_url, False

	return None


def _looks_like_login_page(html: str) -> bool:
	return "/Account/LogOn" in html or "<title>登录" in html


async def run(share_url, *args):
	address = URL(share_url)

	async with new_http_client() as client:
		# StudyView 页面里通常直接写了 StudyId，直接取出来构造 ViewImage 即可。
		study_page_html = None
		if address.path.endswith("/Study/StudyView"):
			async with client.get(share_url) as response:
				study_page_html = await response.text()
			if _looks_like_login_page(study_page_html):
				raise ValueError("该 StudyView 链接当前需要登录，不能直接匿名下载。请改用 /Study/ViewImage 影像分享链接。")

		entry = _resolve_entry(address, study_page_html)
		if not entry:
			raise ValueError("无法识别的链接格式。")

		entry_url, is_viewer_url = entry
		if is_viewer_url:
			downloader = await HinacomDownloader.from_url(client, entry_url)
		else:
			downloader = await HinacomDownloader.from_viewer_link(client, entry_url)

		async with downloader:
			await downloader.download_all("--raw" in args)
