"""
下载微影患者云（radinfo）yyx.zy91.com 上的 DICOM 影像。

分享链接形如：
    https://yyx.zy91.com:6443/PC/#/share_report?pid=<patientsId>&sid=<accessionNumber>&Expires=<unix>&Signature=<sig>

整体流程：
1. 解析 hash 中的查询参数。
2. 调 /api/OAuth/User/StudyReport 拿检查元数据和 ViewerUrl（无需登录，凭 Signature 通过）。
3. 拉 ViewerUrl 返回的 HTML，内嵌的 JS 里有完整的 patientInfo / studyInfo / serInfo* / imgInfo* 列表。
4. 每张图片在 imgInfo 里都带 imageURL，形如 http://localhost:1000/api/Wado/ImageReader?FN=...，
   把 localhost 部分换成分享接口所在的域名即可直接 GET 出标准 DICOM 文件。

页面有图形验证码和身份证后六位的弹窗，但那只是前端 UI，后端 OAuth 接口本身是凭 Signature 鉴权，
分享链接没过期就能直接下，所以这里不需要用户输入。
"""
import re
import sys
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import parse_qsl, urlsplit

from tqdm import tqdm
from yarl import URL

from crawlers._utils import SeriesDirectory, new_http_client, suggest_save_dir


_STUDY_REPORT_PATH = "/api/OAuth/User/StudyReport"
_LOCALHOST_RE = re.compile(r"^https?://[0-9a-z.\-]+(?::\d+)?", re.IGNORECASE)
_KV_RE = re.compile(r'"(\w+)":"([^"]*)"')
_WV_CALL_RE = re.compile(r'WV_Add(PatientInfo|StudyInfo|SeriesInfo|ImageInfo)\((\w+)\)')
_VAR_DEF_RE = re.compile(r"var (\w+)\s*=\s*('[\s\S]+?');\s*\n")


@dataclass(slots=True)
class ShareParams:
	patients_id: str
	accession_number: str
	expires: str
	signature: str


@dataclass(slots=True)
class StudyReport:
	patient_name: str
	patient_id: str
	patient_sex: str
	study_date: str
	study_time: str
	study_modalities: str
	study_describe: str
	hospital_name: str
	viewer_url: str


@dataclass(slots=True)
class ImageInfo:
	sop_instance_uid: str
	sop_class_uid: str
	image_url: str
	instance_number: int


@dataclass(slots=True)
class SeriesInfo:
	series_instance_uid: str
	modality: str
	body_part: str
	series_number: int | None
	images: list[ImageInfo]


def _parse_share_link(share_url: str) -> ShareParams:
	address = URL(share_url)
	if address.host != "yyx.zy91.com":
		raise ValueError("当前链接不是受支持的微影患者云分享链接。")

	fragment = address.fragment
	if not fragment:
		raise ValueError("分享链接缺少必要的参数（hash 部分为空）。")

	# yarl 把 # 后面的部分作为 fragment 整体返回，需要再拆出 query 段。
	parts = urlsplit(fragment if fragment.startswith("/") else "/" + fragment)
	query = dict(parse_qsl(parts.query, keep_blank_values=True))

	pid = (query.get("pid") or "").strip()
	sid = (query.get("sid") or "").strip()
	expires = (query.get("Expires") or query.get("expires") or "").strip()
	signature = (query.get("Signature") or query.get("signature") or "").strip()

	if not (pid and sid and expires and signature):
		raise ValueError("分享链接缺少必要的参数：pid / sid / Expires / Signature。")

	return ShareParams(patients_id=pid, accession_number=sid, expires=expires, signature=signature)


def _api_origin() -> URL:
	# 前端跑在 6443 端口，但实际 API 走 5443；从 app 的 Window_ProjectConfig.SERVER_URL 得到。
	return URL("https://yyx.zy91.com:5443")


async def _fetch_study_report(client, share: ShareParams) -> StudyReport:
	params = {
		"accessionNumber": share.accession_number,
		"phoneNum": "",
		"patientsId": share.patients_id,
		"Expires": share.expires,
		"Signature": share.signature,
	}
	url = _api_origin().with_path(_STUDY_REPORT_PATH)
	async with client.get(str(url), params=params) as response:
		payload = await response.json(content_type=None)

	if not payload.get("success"):
		message = payload.get("message") or "微影服务端拒绝了分享链接。"
		raise ValueError(message)

	data = payload.get("data") or {}
	viewer_url = str(data.get("ViewerUrl") or "").strip()
	if not viewer_url:
		raise ValueError("微影接口没有返回影像查看器地址。")

	return StudyReport(
		patient_name=str(data.get("PatientsName") or "匿名").strip() or "匿名",
		patient_id=str(data.get("PatientsID") or "").strip(),
		patient_sex=str(data.get("PatientsSex") or "").strip(),
		study_date=str(data.get("StudiesDoneDate") or "").strip(),
		study_time=str(data.get("StudiesDoneDateTime") or "").strip(),
		study_modalities=str(data.get("StudiesModalities") or "").strip(),
		study_describe=str(data.get("StudiesExamineAlias") or "").strip(),
		hospital_name=str(data.get("HospitalName") or "").strip(),
		viewer_url=viewer_url,
	)


def _parse_kv_literal(text: str) -> dict[str, str]:
	"""WebView 内联 JS 用单引号字符串拼接 JSON，键值都在双引号里，直接抓 key:"value" 就行。"""
	return dict(_KV_RE.findall(text))


def _to_int(value, default=None):
	try:
		return int(str(value).strip())
	except (TypeError, ValueError):
		return default


def _rewrite_image_url(raw: str, origin: URL) -> str:
	"""WebView 返回的 imageURL 里写死成 http://localhost:1000，需要换成实际接口域名。"""
	if not raw:
		return raw
	host = raw.lower()
	if "localhost" in host or "127.0.0.1" in host:
		return _LOCALHOST_RE.sub(str(origin), raw, count=1)
	return raw


def _iter_viewer_definitions(html: str) -> Iterator[tuple[str, str, dict[str, str]]]:
	"""按出现顺序遍历内联 JS 中的 WV_Add* 调用，产出 (类别, 变量名, 字段字典)。"""
	# 先把所有 var <name> = '...' 收集起来，再按 WV_Add 调用顺序遍历，
	# 这样可以稳定还原“先 series 后属于它的 images”的层级关系。
	definitions = {name: raw for name, raw in _VAR_DEF_RE.findall(html)}
	for kind, var_name in _WV_CALL_RE.findall(html):
		raw = definitions.get(var_name)
		if raw is None:
			continue
		yield kind, var_name, _parse_kv_literal(raw)


def _parse_viewer_page(html: str, origin: URL) -> list[SeriesInfo]:
	series_list: list[SeriesInfo] = []
	current: SeriesInfo | None = None

	for kind, _, fields in _iter_viewer_definitions(html):
		if kind == "SeriesInfo":
			current = SeriesInfo(
				series_instance_uid=fields.get("serInstUID", ""),
				modality=fields.get("modality", ""),
				body_part=fields.get("bodyPart", ""),
				series_number=_to_int(fields.get("serNumber")),
				images=[],
			)
			series_list.append(current)
		elif kind == "ImageInfo":
			if current is None:
				# 出现没有归属序列的影像，按出现顺序兜底放进一个匿名序列。
				current = SeriesInfo("", "", "", None, [])
				series_list.append(current)
			image_url = _rewrite_image_url(fields.get("imageURL", ""), origin)
			if not image_url:
				continue
			current.images.append(ImageInfo(
				sop_instance_uid=fields.get("sopInstUID", ""),
				sop_class_uid=fields.get("sopClassUID", ""),
				image_url=image_url,
				instance_number=_to_int(fields.get("imageNumber"), default=len(current.images) + 1),
			))

	return [s for s in series_list if s.images]


def _study_save_dir(report: StudyReport):
	desc = report.study_describe or report.study_modalities or "云影像"
	when = report.study_time or report.study_date or report.patient_id or "study"
	return suggest_save_dir(report.patient_name, desc, when)


def _series_label(series: SeriesInfo) -> str:
	if series.body_part and series.modality:
		return f"{series.modality} {series.body_part}"
	return series.modality or series.body_part or (
		str(series.series_number) if series.series_number is not None else "Unnamed"
	)


async def run(share_url: str, *_):
	share = _parse_share_link(URL(share_url).human_repr())
	api_origin = _api_origin()

	async with new_http_client() as client:
		client.headers["Referer"] = "https://yyx.zy91.com:6443/"
		client.headers["Origin"] = "https://yyx.zy91.com:6443"

		report = await _fetch_study_report(client, share)
		print(f"下载微影患者云 DICOM：{report.hospital_name or 'yyx.zy91.com'}")
		print(f"患者 {report.patient_name}，{report.study_describe or report.study_modalities}，{report.study_time or report.study_date}")

		async with client.get(report.viewer_url) as response:
			html = await response.text(encoding="utf-8")

		series_list = _parse_viewer_page(html, api_origin)
		if not series_list:
			raise ValueError("影像查看器页面没有返回任何可下载的影像。")

		total_images = sum(len(s.images) for s in series_list)
		save_to = _study_save_dir(report)
		print(f"共 {len(series_list)} 个序列、{total_images} 张影像。")
		print(f"保存到: {save_to}\n")

		for series in series_list:
			label = _series_label(series)
			directory = SeriesDirectory(save_to, series.series_number, label, len(series.images))
			# 按 instance_number 升序，方便日后阅片。
			ordered = sorted(series.images, key=lambda image: (image.instance_number, image.sop_instance_uid))

			for i, image in enumerate(tqdm(ordered, desc=label, unit="张", file=sys.stdout)):
				await directory.download(client, i, "dcm", image.image_url, label=f"{label} 第 {i + 1} 张")

			directory.ensure_complete()
