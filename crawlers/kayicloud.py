"""
下载卡易云影像（无极云影像）DICOM 文件，支持域名 *.kayicloud.com 的 DICOM 查看器页面。
产品：浙江卡易智慧医疗科技有限公司 HIDOS AI_PACS。

URL 格式：
  https://dicomviewer.{xxx}.kayicloud.com/?HospitalCode={}&AccessionNo={}&StudyInstanceUID={}
      &serverAddr={base64}&dataid={}&Token={JWT}&Anony={}

serverAddr 解码格式：
  public@{api_url}?StudyInstanceUID=...&x-moon-ak=...&x-moon-expires=...&x-moon-timestamp=...
      &x-moon-sign=...&AccessionNo=...&HospitalCode=...&Proxy=...

API 流程：
  1. GET /api/v2/token?HospitalCode=&AccessionNo=&StudyInstanceUID=&serverAddr=
     Header: Authorization: {JWT}
     → {"token": "..."}

  2. GET /api/v2/imageQuery?HospitalCode=&AccessionNo=&StudyInstanceUID=&serverAddr=&DataID=
     Header: Authorization: {token}, VisitorId: {uuid}
     → [{HospitalCode, AccessionNumber, StudyInstanceUID, Token, SeriesList, ...}]

  3. GET /api/v2/image?HospitalCode=&AccessionNo=&StudyInstanceUID=&ImageKey={base64(FilePath)}&Token=&Index=0
     Header: Authorization: {token}, VisitorId: {uuid}
     → DICOM bytes
"""
import base64
import uuid
from urllib.parse import parse_qsl

from yarl import URL

from crawlers._utils import SeriesDirectory, new_http_client, suggest_save_dir, tqdme


def _extract_params(share_url: str) -> dict[str, str]:
	"""从分享 URL 提取查询参数，支持 query string 和 fragment。"""
	address = URL(share_url)
	if address.query:
		return dict(address.query)
	fragment = address.fragment
	if "?" in fragment:
		return dict(parse_qsl(fragment.split("?", 1)[1], keep_blank_values=True))
	raise ValueError("分享链接缺少查询参数，请检查链接是否完整")


def _image_url(viewer_origin: str, study: dict, file_path: str, index: int) -> str:
	"""根据 FilePath 构造 DICOM 文件下载 URL。"""
	# FilePath 已经是完整 URL（服务端直接返回带 ImageKey 的完整地址）。
	if file_path.startswith(("http://", "https://")):
		return f"{file_path}&Index={index}"

	# FilePath 是相对路径且已含 ImageKey，直接拼 origin 和 Index。
	if "ImageKey" in file_path:
		return f"{viewer_origin}/{file_path.lstrip('/')}&Index={index}"

	# FilePath 是原始存储路径，需 base64 编码后作为 ImageKey 构造 URL。
	image_key = base64.b64encode(file_path.encode()).decode()
	return (
		f"{viewer_origin}/api/v2/image"
		f"?HospitalCode={study['HospitalCode']}"
		f"&AccessionNo={study['AccessionNumber']}"
		f"&StudyInstanceUID={study['StudyInstanceUID']}"
		f"&ImageKey={image_key}"
		f"&Token={study.get('Token', '')}"
		f"&Index={index}"
	)


async def run(share_url: str):
	params = _extract_params(share_url)

	hospital_code = params.get("HospitalCode", "")
	accession_no = params.get("AccessionNo", "")
	study_uid = params.get("StudyInstanceUID", "")
	server_addr = params.get("serverAddr", "")
	data_id = params.get("dataid", "")
	jwt_token = params.get("Token", "")

	# 如果分享 URL 没有 HospitalCode，从 serverAddr 解码提取。
	if not hospital_code and server_addr:
		decoded = base64.b64decode(server_addr + "==").decode("utf-8")
		api_url = decoded.split("@", 1)[-1]
		api_params = dict(parse_qsl(api_url.split("?", 1)[1], keep_blank_values=True))
		hospital_code = api_params.get("HospitalCode", hospital_code)
		if not accession_no:
			accession_no = api_params.get("AccessionNo", "")

	address = URL(share_url)
	viewer_origin = str(address.origin())
	visitor_id = str(uuid.uuid4())

	base_headers = {
		"VisitorId": visitor_id,
		"Authorization": jwt_token,
		"Referer": share_url,
	}

	async with new_http_client(viewer_origin) as client:
		# 1. 用 JWT 换取正式访问 token。
		token_params = {
			"HospitalCode": hospital_code,
			"AccessionNo": accession_no,
			"StudyInstanceUID": study_uid,
			"serverAddr": server_addr,
		}
		async with client.get("/api/v2/token", params=token_params, headers=base_headers) as resp:
			token_body = await resp.json(content_type=None)

		# 响应可能是 {"token": "..."} 或 {"code": 0, "data": {"token": "..."}} 等多种格式。
		if isinstance(token_body, dict):
			auth_token = (
				token_body.get("token")
				or (token_body.get("data") or {}).get("token")
				or jwt_token
			)
		else:
			auth_token = jwt_token
		auth_headers = {
			"VisitorId": visitor_id,
			"Authorization": auth_token,
		}

		# 2. 查询影像列表。
		query_params = {
			"HospitalCode": hospital_code,
			"AccessionNo": accession_no,
			"StudyInstanceUID": study_uid,
			"serverAddr": server_addr,
			"DataID": data_id,
		}
		async with client.get("/api/v2/imageQuery", params=query_params, headers=auth_headers) as resp:
			body = await resp.json(content_type=None)

		# 响应可能是裸列表，也可能是 {"code": 0, "data": [...]} 包装结构。
		if isinstance(body, dict):
			code = body.get("code")
			msg = body.get("msg") or body.get("message") or ""
			if code not in {0, None, "0", "200", 200}:
				raise ValueError(f"影像查询失败（code={code}）：{msg}")
			studies = body.get("data") or []
		else:
			studies = body or []

		if not studies:
			raise ValueError("未查询到影像数据，检查链接是否已失效。")

		study = studies[0]
		series_list = study.get("SeriesList") or []

		patient_name = (
			study.get("PatientsName")
			or study.get("PatientName")
			or study.get("PatientsID")
			or study.get("AccessionNumber")
			or accession_no
		)
		description = (
			study.get("StudyDescription")
			or study.get("Modality")
			or study.get("BodyPartExamined")
			or ""
		)
		study_datetime = (
			study.get("StudyDateTime")
			or study.get("StudyDate")
			or ""
		)

		study_dir = suggest_save_dir(patient_name, description, study_datetime)
		print(f"下载卡易云影像到：{study_dir}")

		# 图像下载时 Authorization header 可能需要用 study.Token（imageQuery 返回值），
		# 也可能用 auth_token，先用 study.Token 作为 URL 参数（已内嵌在 URL 里）。
		study_token = study.get("Token") or auth_token
		download_study = dict(study)
		download_study.setdefault("Token", study_token)

		image_headers = {
			"VisitorId": visitor_id,
			"Authorization": auth_token,
		}

		for series in series_list:
			images = series.get("ImageList") or []
			number = series.get("SeriesNumber") or 0
			desc = series.get("SeriesDescription") or f"Series{number}"
			expected = series.get("ImageCount") or len(images)
			dir_ = SeriesDirectory(study_dir, number, desc, expected)

			for i, image in tqdme(images, desc=desc):
				file_path = image.get("FilePath", "")
				num_frames = int(image.get("NumberOfFrames") or 1)
				# 多帧图像每帧独立下载（Index 从 0 开始）。
				for frame_idx in range(num_frames):
					img_url = _image_url(viewer_origin, download_study, file_path, frame_idx)
					frame_i = i * num_frames + frame_idx
					await dir_.download(
						client,
						frame_i,
						"dcm",
						img_url,
						headers=image_headers,
						label=f"{desc} 第 {frame_i + 1} 张",
					)

			dir_.ensure_complete()
