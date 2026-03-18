import re

from yarl import URL

from crawlers._utils import new_http_client
from crawlers.hinacom import HinacomDownloader

# 元素属性很简单，就直接正则了，懒得再下个解析库。
_HIDDEN_INPUT_RE = re.compile(r'<input type="hidden" id="([^"]+)" name="[^"]+" value="([^"]+)" />')
_FAIL_MESSAGE_RE = re.compile(r'<span class="fail-msg">([^<]+)</span>')
_FAIL_CODE_RE = re.compile(r'<span class="fail-code">\(([^<]+)\)</span>')
_CHECKED_AUTH_TYPE_RE = re.compile(r'name="AuthorityType" value="([^"]+)"\s+checked=&quot;checked&quot;')
_AUTH_TYPE_RE = re.compile(r'name="AuthorityType" value="([^"]+)"(?:\s+checked=&quot;checked&quot;)?')

_SEARCH_HEADERS = {
	"X-Requested-With": "XMLHttpRequest",
	"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
_SEARCH_FORM = {
	"PageIndex": "1",
	"PageSize": "50",
	"DateFrom": "2000-01-01",
	"DateTo": "2030-12-31",
}


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
		fields = dict(_HIDDEN_INPUT_RE.findall(study_page_html))
		sid = fields.get("StudyId")
		if sid:
			return f"{address.origin()}/Study/ViewImage?studyId={sid}", False

	return_url = address.query.get("returnUrl")
	if return_url:
		return return_url, False

	return None


def _looks_like_login_page(html: str) -> bool:
	return "/Account/LogOn" in html or "<title>登录" in html


def _is_login_free_link(address: URL) -> bool:
	return address.path.startswith("/Account/ViewListLoginFree/")


def requires_authority_code(url: str) -> bool:
	return _is_login_free_link(URL(url))


def authority_code_prompt(url: str) -> str | None:
	if requires_authority_code(url):
		return "手机号/身份证后四位"
	return None


def _parse_login_free_form(html: str) -> tuple[str, str]:
	fields = dict(_HIDDEN_INPUT_RE.findall(html))
	account_id = fields.get("AccountId")
	if not account_id:
		raise ValueError("免登录验证页里没有找到账号信息，站点页面结构可能已变化。")

	checked = _CHECKED_AUTH_TYPE_RE.search(html)
	if checked:
		return account_id, checked.group(1)

	matches = _AUTH_TYPE_RE.findall(html)
	if not matches:
		raise ValueError("免登录验证页里没有找到验证方式，站点页面结构可能已变化。")

	return account_id, matches[0]


def _parse_login_free_error(html: str) -> str | None:
	match = _FAIL_MESSAGE_RE.search(html)
	if not match:
		return None

	message = match.group(1).strip()
	code_match = _FAIL_CODE_RE.search(html)
	if code_match:
		return f"{message} ({code_match.group(1).strip()})"
	return message


def _extract_list_keyword(address: URL) -> str:
	if address.query.get("idType") == "accessionnumber":
		return address.path.rsplit("/", 1)[-1]
	return ""


def _is_ct_study(item: dict) -> bool:
	modality = str(item.get("ModalityName") or item.get("Modalities") or "").upper()
	return "CT" in modality


def _pick_login_free_study(address: URL, items: list[dict]) -> dict:
	return _filter_login_free_studies(address, items)[0]


def _filter_login_free_studies(address: URL, items: list[dict]) -> list[dict]:
	keyword = _extract_list_keyword(address)
	candidates = items

	if keyword:
		exact = [item for item in candidates if str(item.get("AccessionNumber") or "") == keyword]
		if exact:
			candidates = exact

	ct_items = [item for item in candidates if _is_ct_study(item)]
	if ct_items:
		return ct_items

	if candidates:
		raise ValueError("列表中没有找到 CT 检查，请改用具体的 CT 检查链接，或在网页里先确认检查类型。")

	if keyword:
		raise ValueError("验证成功，但列表里没有找到与该检查号对应的项目。")
	raise ValueError("验证成功，但检查列表为空。")


async def _load_login_free_study(client, address: URL, authority_code: str) -> str:
	studies = await _load_login_free_studies(client, address, authority_code)
	study = studies[0]
	description = str(study.get("StudyDescription") or "").strip()
	modality = str(study.get("ModalityName") or study.get("Modalities") or "").strip()
	accession = str(study.get("AccessionNumber") or "").strip()
	print(f"已从检查列表中选择 {modality or '目标'} 检查：{accession} {description}".strip())
	return build_login_free_view_image_url(str(address), study)


async def _load_login_free_studies(client, address: URL, authority_code: str) -> list[dict]:
	async with client.get(str(address)) as response:
		login_html = await response.text()

	account_id, auth_type = _parse_login_free_form(login_html)
	form = {
		"AuthorityType": auth_type,
		"AuthorityCode": authority_code,
		"AccountId": account_id,
	}
	async with client.post(str(address), data=form) as response:
		post_html = await response.text()

	error = _parse_login_free_error(post_html)
	if error:
		raise ValueError(f"验证失败，请检查后四位是否正确。{error}")

	search_form = dict(_SEARCH_FORM)
	search_form["Keyword"] = _extract_list_keyword(address)
	search_url = str(address.origin().with_path("/Study/SearchStudies"))
	async with client.post(search_url, data=search_form, headers=_SEARCH_HEADERS) as response:
		payload = await response.json(content_type=None)

	if not payload.get("Success"):
		message = payload.get("Message") or "无法读取检查列表。"
		raise ValueError(message)

	return _filter_login_free_studies(address, payload.get("Items") or [])


async def list_login_free_ct_studies(url: str, authority_code: str) -> list[dict]:
	address = URL(url)
	if not _is_login_free_link(address):
		raise ValueError("当前链接不是免登录检查列表链接。")

	async with new_http_client() as client:
		return await _load_login_free_studies(client, address, authority_code)


def build_login_free_view_image_url(url: str, study: dict) -> str:
	address = URL(url)
	return f"{address.origin()}/Study/ViewImage?studyId={study['Id']}"


async def run(share_url, *args):
	address = URL(share_url)
	password = args[0] if args and not args[0].startswith("--") else None
	raw = "--raw" in args

	async with new_http_client() as client:
		if _is_login_free_link(address):
			if not password:
				raise ValueError("该链接需要填写手机号或身份证后四位。")
			entry_url = await _load_login_free_study(client, address, password)
			async with await HinacomDownloader.from_viewer_link(client, entry_url) as downloader:
				await downloader.download_all(raw)
			return

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
			await downloader.download_all(raw)
