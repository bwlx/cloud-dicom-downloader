from yarl import URL
from urllib.parse import parse_qsl
from pathlib import Path

import aiohttp
import json
from crawlers._utils import download_to_path, new_http_client, suggest_save_dir


def _extract_share_params(share_url: str) -> dict[str, str]:
	address = URL(share_url)
	if address.query:
		return dict(address.query)

	fragment = address.fragment
	if "?" in fragment:
		query_string = fragment.split("?", 1)[1]
		return dict(parse_qsl(query_string, keep_blank_values=True))

	raise ValueError("分享链接缺少查询参数，请检查链接是否完整")


async def run(share_url: str): 
	share_params = _extract_share_params(share_url)
	prefix = "image_down${"
	async with new_http_client("http://cloud.wehzsy.com:9005/") as client:
		#地址类似于http://cloud.wehzsy.com:9003/PC/#/share_report?tel=<TEL>&pid=<PATIENT_ID>&rid=<RESULT_ID>&download=1&forward=1&Expires=<EXPIRES>&Signature=<SIGNATURE>
		#拼接成这个http://cloud.wehzsy.com:9005/api/OAuth/User/StudyReport?resultsIndex=<RESULT_ID>&phoneNum=<TEL>&patientsId=<PATIENT_ID>&download=1&forward=1&Expires=<EXPIRES>&Signature=<SIGNATURE>
		params = {
			"resultsIndex": share_params["rid"],
			"phoneNum": share_params["tel"],
			"patientsId": share_params["pid"],
			"download": share_params["download"],
			"forward": share_params["forward"],
			"Expires": share_params["Expires"],
			"Signature": share_params["Signature"],
		}
		try:
			async with client.get("/api/OAuth/User/StudyReport", params=params) as response:
				body = await response.json()
				if body["success"] != True:
					raise Exception(body["message"])
				study_uid,accession_num,clinical = body["data"]["StudiesInstUID"], body["data"]["AccessionNumber"],body["data"]
		except Exception as e:
			if "401" in str(e):
				raise ValueError(
					"分享链接无下载权限（401 Unauthorized）。\n\n"
					"【重要】医院公众号分享影像时，分享者需要：\n"
					"1. 在公众号分享页面勾选『允许下载』选项\n"
					"2. 分享时效请拉满到1年\n\n"
					"其他原因：\n"
					"- 如已勾选，检查链接是否已过期\n"
					"- 从医院重新获取最新的分享链接\n"
					"- 确保链接完整且来自官方分享渠道"
				)
			raise

		#构造消息image_down${'AccessionNumber':'<ACCESSION_NUMBER>','StudyInstUid':'<STUDY_INSTANCE_UID>','FileType':'Viewer'}
		#请求ws://cloud.wehzsy.com:9004/ImageDown
		#等待消息返回，直到ws://cloud.wehzsy.com:9004/ImageDown发来image_down${"success":true,"data":"http://cloud.wehzsy.com:9005//DICOMZIP/<ACCESSION_NUMBER>_Viewer.zip","message":null}
		payload = json.dumps({
			"AccessionNumber": accession_num,
			"StudyInstUid": study_uid,
			"FileType": "Viewer",
		}, ensure_ascii=False, separators=(",", ":"))
		message = f"image_down${payload}"
		#地址不一样，不能复用client了，得新建一个。
		#等待返回，不要停止，收到别的消息继续等，直到收到以image_down${开头的消息为止，解析出里面的链接。
		async with aiohttp.ClientSession() as ws_client:
			async with ws_client.ws_connect("ws://cloud.wehzsy.com:9004/ImageDown") as ws:
				await ws.send_str(message)
				async for msg in ws:
					if msg.type == aiohttp.WSMsgType.TEXT and msg.data.startswith(prefix):
						payload = msg.data[len(prefix):].strip()
						if not payload.startswith("{"):
							payload = "{" + payload
						if not payload.endswith("}"):
							payload = payload + "}"
						result = json.loads(payload)
						if result["success"]:
							zip_url = result["data"]
							break
						else:
							raise Exception(result["message"])

 
		study_dir = suggest_save_dir(clinical["PatientsName"], clinical["StudiesExamineAlias"], clinical["StudiesDoneDateTime"])
		study_dir.mkdir(parents=True, exist_ok=True)
		file_name = URL(zip_url).path.rsplit("/", 1)[-1] or f"{accession_num}_Viewer.zip"
		save_to = Path(study_dir) / file_name
		print(f"下载杭州市第一人民医院电子胶片到：{save_to}")
		await download_to_path(client, save_to, zip_url, label=file_name)
