import asyncio
import base64
import gzip
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl

from playwright.async_api import Page, async_playwright
from pydicom import Dataset
from pydicom.dataset import FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, PYDICOM_IMPLEMENTATION_UID
from tqdm import tqdm
from yarl import URL

from crawlers._browser import launch_browser
from crawlers._utils import SeriesDirectory, make_unique_dir, new_http_client, suggest_save_dir

_HOST = "ge.jstumor.jszlyy.com.cn"
_CIF_LOGIN_PATH = "/CIF/user/loginAccCode"
_API_BASE_PATH = "/cloudFilmDataLogicApi"

_ZFP_HOOK_SCRIPT = r"""
(() => {
	if (window.__cloudDicomZfpHookInstalled) return;
	window.__cloudDicomZfpHookInstalled = true;

	const NativeWebSocket = window.WebSocket;
	const hook = window.__cloudDicomZfpHook = {
		sockets: [],
		studySocket: null,
		pending: new Map(),
	};

	function toBase64(buffer) {
		const bytes = new Uint8Array(buffer);
		let output = "";
		const chunkSize = 0x8000;
		for (let i = 0; i < bytes.length; i += chunkSize) {
			output += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
		}
		return btoa(output);
	}

	function resolveBinary(buffer) {
		for (const [token, pending] of hook.pending) {
			if (pending.phase !== "pixel") continue;
			if (pending.expectedBytes && buffer.byteLength !== pending.expectedBytes) continue;
			hook.pending.delete(token);
			pending.resolve({
				headerText: pending.headerText,
				dataB64: toBase64(buffer),
			});
			return;
		}
	}

	function handleMessage(event) {
		const data = event.data;
		if (typeof data === "string") {
			if (data.startsWith("CMDGETOBJ ")) {
				const token = data.split(" ")[1];
				const pending = hook.pending.get(token);
				if (pending) pending.phase = "header";
				return;
			}
			if (data.startsWith("{")) {
				for (const pending of hook.pending.values()) {
					if (pending.phase !== "header") continue;
					pending.headerText = data;
					try {
						const header = JSON.parse(data);
						pending.expectedBytes = Number(header.Rows) * Number(header.Columns)
							* Number(header.SamplesPerPixel || 1) * Math.ceil(Number(header.BitsAllocated || 8) / 8);
					} catch {
						pending.expectedBytes = 0;
					}
					pending.phase = "pixel";
					break;
				}
			}
			return;
		}

		if (data instanceof Blob) {
			data.arrayBuffer().then(resolveBinary);
		} else {
			resolveBinary(data);
		}
	}

	function PatchedWebSocket(...args) {
		const ws = new NativeWebSocket(...args);
		const nativeSend = ws.send.bind(ws);
		ws.send = (data) => {
			if (typeof data === "string" && data.includes("CMDGETOPTIMIZEDSTUDY")) {
				hook.studySocket = ws;
			}
			return nativeSend(data);
		};
		ws.addEventListener("message", handleMessage);
		hook.sockets.push(ws);
		return ws;
	}

	PatchedWebSocket.prototype = NativeWebSocket.prototype;
	Object.setPrototypeOf(PatchedWebSocket, NativeWebSocket);
	window.WebSocket = PatchedWebSocket;

	window.__cloudDicomZfpReady = () => (
		!!hook.studySocket && hook.studySocket.readyState === NativeWebSocket.OPEN
	);

	window.__cloudDicomZfpGetImage = (token) => new Promise((resolve, reject) => {
		const ws = hook.studySocket || hook.sockets.find((item) => item.readyState === NativeWebSocket.OPEN);
		if (!ws) {
			reject(new Error("影像 WebSocket 尚未连接。"));
			return;
		}

		const timer = setTimeout(() => {
			hook.pending.delete(token);
			reject(new Error(`影像响应超时：${token}`));
		}, 45000);

		hook.pending.set(token, {
			phase: "command",
			resolve: (value) => {
				clearTimeout(timer);
				resolve(value);
			},
			reject,
		});

		ws.send(JSON.stringify({
			CommandName: "CMDGETOBJ",
			Token: token,
			Options: {
				OutputFormat: "IT_RAW",
				QualityLevel: 97,
				ElevateRequest: false,
				MaxResolution: 0,
			},
		}));
	});
})();
"""


@dataclass(slots=True)
class CifLink:
	url_param: str


@dataclass(slots=True)
class CifAccess:
	url_param: str
	access_code: str
	patient_id: str
	order_id: str
	exam_id: str


@dataclass(slots=True)
class ZfpImageEntry:
	series: dict
	sop: dict
	frame_index: int
	token: str


def _parse_cif_link(address: URL) -> CifLink:
	if address.host != _HOST or address.path != _CIF_LOGIN_PATH:
		raise ValueError("当前链接不是受支持的 CIF 云影像分享链接。")

	url_param = str(address.query.get("urlParam") or "").strip()
	if not url_param:
		raise ValueError("CIF 分享链接缺少 urlParam 参数。")

	return CifLink(url_param=url_param)


def requires_authority_code(url: str) -> bool:
	try:
		_parse_cif_link(URL(url))
		return True
	except ValueError:
		return False


def authority_code_prompt(url: str) -> str | None:
	if requires_authority_code(url):
		return "访问码"
	return None


def _api_url(address: URL, path: str) -> str:
	return str(address.origin().with_path(_API_BASE_PATH + path))


def _parse_cif_access(url_param: str, payload: dict) -> CifAccess:
	if payload.get("status") != 200 or not payload.get("data"):
		raise ValueError(str(payload.get("msg") or "CIF 链接解析失败。"))

	params = dict(parse_qsl(str(payload["data"]), keep_blank_values=True))
	try:
		return CifAccess(
			url_param=url_param,
			access_code=str(params["accessCode"]),
			patient_id=str(params["patientId"]),
			order_id=str(params["orderId"]),
			exam_id=str(params["examId"]),
		)
	except KeyError as exc:
		raise ValueError("CIF 链接解析结果缺少必要下载参数。") from exc


async def _decrypt_link(client, address: URL, link: CifLink) -> CifAccess:
	async with client.post(_api_url(address, "/filmInfo/getAESDecrypt"), data={
		"decryptContent": link.url_param,
	}) as response:
		payload = await response.json(content_type=None)
	return _parse_cif_access(link.url_param, payload)


async def _login_with_access_code(client, address: URL, access: CifAccess, authority_code: str) -> dict[str, str]:
	async with client.post(_api_url(address, "/user/accCodeLogin"), data={
		"accessCode": authority_code,
		"urlAccessCode": access.access_code,
		"patientId": access.patient_id,
		"orderId": access.order_id,
		"examId": access.exam_id,
	}) as response:
		payload = await response.json(content_type=None)
		if payload.get("status") != 200:
			raise ValueError(str(payload.get("msg") or "访问码验证失败。"))

		auth = response.headers.get("authorization")
		user = response.headers.get("user")
		if not auth or not user:
			raise ValueError("访问码验证成功，但站点没有返回可用的访问令牌。")
		return {"authorization": auth, "user": user}


async def _load_report_info(client, address: URL, access: CifAccess, headers: dict[str, str]) -> dict:
	async with client.post(_api_url(address, "/filmInfo/getReportInfo"), headers=headers, data={
		"patientId": access.patient_id,
		"orderId": access.order_id,
		"examId": access.exam_id,
		"visitorDbkey": "0",
	}) as response:
		payload = await response.json(content_type=None)

	if payload.get("status") != 200 or not isinstance(payload.get("data"), dict):
		raise ValueError(str(payload.get("msg") or "站点没有返回可用的检查信息。"))

	if not payload["data"].get("zfpUrl"):
		raise ValueError("CIF 检查信息缺少 ZFP 影像查看器地址。")
	return payload["data"]


def _fragment_params(url: str) -> dict[str, str]:
	address = URL(url)
	return dict(parse_qsl(address.fragment, keep_blank_values=True))


def _person_name(value) -> str:
	if isinstance(value, dict):
		for key in ("PersonNameString", "PersonNameToString"):
			text = str(value.get(key) or "").strip()
			if text:
				return text
		for key in ("Ideographic", "SingleByte", "Phonetic"):
			part = value.get(key)
			if isinstance(part, dict):
				family = str(part.get("Family") or "").strip()
				given = str(part.get("Given") or "").strip()
				if family or given:
					return "^".join(item for item in (family, given) if item)
	return str(value or "").strip()


def _date_value(value) -> str:
	text = str(value or "").strip()
	if not text:
		return ""
	match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
	if match:
		return "".join(match.groups())
	return re.sub(r"\D", "", text)[:8]


def _time_value(value) -> str:
	text = str(value or "").strip()
	if not text:
		return ""
	match = re.match(r"(\d{2}):?(\d{2}):?(\d{2})(\.\d+)?", text)
	if match:
		return "".join(match.groups(default=""))
	return re.sub(r"[^\d.]", "", text)


def _int_or_none(value):
	if value in (None, ""):
		return None
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return None


def _float_text(value):
	if value in (None, ""):
		return None
	return str(value)


def _set_if_present(ds: Dataset, attr: str, value):
	if value not in (None, ""):
		setattr(ds, attr, value)


def _image_entries(study: dict) -> list[ZfpImageEntry]:
	entries: list[ZfpImageEntry] = []
	for series in study.get("Series") or []:
		if int(series.get("ImageCount") or 0) <= 0:
			continue

		for sop in series.get("Sops") or []:
			sop_uid = str(sop.get("SopInstanceUid") or "").strip()
			if not sop_uid:
				continue
			frame_count = max(int(sop.get("NumberOfFrames") or 1), 1)
			for frame_index in range(frame_count):
				entries.append(ZfpImageEntry(
					series=series,
					sop=sop,
					frame_index=frame_index,
					token=f"{sop_uid}#{frame_index}",
				))
	return entries


def _series_number(series: dict) -> int | None:
	return _int_or_none(series.get("SeriesNumber"))


def _study_save_dir(study: dict) -> Path:
	name = _person_name(study.get("PatientName")) or "匿名"
	description = str(study.get("StudyDescription") or study.get("AccessionNumber") or "云影像").strip() or "云影像"
	time_key = _date_value(study.get("StudyDate")) or str(study.get("StudyInstanceUid") or "study")
	return make_unique_dir(suggest_save_dir(name, description, time_key))


def _token_sort_key(entry: ZfpImageEntry):
	return (
		_series_number(entry.series) or 0,
		_int_or_none(entry.sop.get("ImageNumber")) or 0,
		entry.frame_index,
		entry.token,
	)


async def _wait_zfp_metadata(page: Page, zfp_url: str) -> dict:
	done = asyncio.get_running_loop().create_future()

	def on_ws(ws):
		state = {"expect_metadata": False}

		def on_sent(payload):
			if isinstance(payload, str) and "CMDGETOPTIMIZEDSTUDY" in payload:
				state["expect_metadata"] = True

		def on_received(payload):
			if done.done() or not state["expect_metadata"]:
				return
			if isinstance(payload, bytes) and payload.startswith(b"\x1f\x8b"):
				done.set_result(json.loads(gzip.decompress(payload)))

		ws.on("framesent", on_sent)
		ws.on("framereceived", on_received)

	page.on("websocket", on_ws)
	await page.goto(zfp_url, wait_until="commit", timeout=60000)
	return await asyncio.wait_for(done, timeout=120)


async def _download_zfp_image(page: Page, entry: ZfpImageEntry) -> tuple[dict, bytes]:
	result = await page.evaluate("token => window.__cloudDicomZfpGetImage(token)", entry.token)
	header = json.loads(result["headerText"])
	data = base64.b64decode(result["dataB64"])
	return header, data


def _write_dicom(study: dict, series: dict, sop: dict, header: dict, pixel_data: bytes, filename: Path):
	ds = Dataset()
	ds.file_meta = FileMetaDataset()
	ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
	ds.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
	ds.file_meta.MediaStorageSOPClassUID = str(sop.get("SopClassUid") or header.get("SopClassUid") or CTImageStorage)
	ds.file_meta.MediaStorageSOPInstanceUID = str(sop.get("SopInstanceUid") or header.get("SopInstanceUid"))

	ds.SpecificCharacterSet = "ISO_IR 192"
	ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
	ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
	ds.StudyInstanceUID = str(study.get("StudyInstanceUid") or sop.get("StudyInstanceUid") or "")
	ds.SeriesInstanceUID = str(series.get("SeriesInstanceUid") or "")
	ds.Modality = str(series.get("SeriesModality") or study.get("Modality") or "OT")
	ds.PatientName = _person_name(study.get("PatientName")) or "匿名"
	ds.PatientID = str(study.get("PatientId") or "")

	_set_if_present(ds, "PatientSex", study.get("PatientSex"))
	_set_if_present(ds, "PatientBirthDate", _date_value(study.get("PatientBirthDate")))
	_set_if_present(ds, "AccessionNumber", study.get("AccessionNumber"))
	_set_if_present(ds, "StudyDate", _date_value(study.get("StudyDate") or header.get("ImageDate")))
	_set_if_present(ds, "StudyTime", _time_value(study.get("StudyTime") or header.get("ImageTime")))
	_set_if_present(ds, "StudyDescription", study.get("StudyDescription"))
	_set_if_present(ds, "SeriesNumber", _series_number(series))
	_set_if_present(ds, "SeriesDescription", series.get("SeriesDescription"))
	_set_if_present(ds, "InstanceNumber", _int_or_none(header.get("ImageNumber") or sop.get("ImageNumber")))
	_set_if_present(ds, "FrameOfReferenceUID", header.get("FrameOfReferenceUid"))
	_set_if_present(ds, "ImageType", header.get("ImageType"))
	_set_if_present(ds, "ImagePositionPatient", header.get("ImagePosition"))
	_set_if_present(ds, "ImageOrientationPatient", header.get("ImageOrientation"))
	_set_if_present(ds, "PixelSpacing", header.get("PixelSpacing"))
	_set_if_present(ds, "SliceThickness", _float_text(header.get("SliceThickness")))
	_set_if_present(ds, "SpacingBetweenSlices", _float_text(header.get("SliceSpacing")))
	_set_if_present(ds, "SliceLocation", _float_text(header.get("SliceLocation")))
	_set_if_present(ds, "KVP", _float_text(header.get("Kvp")))
	_set_if_present(ds, "XRayTubeCurrent", _int_or_none(header.get("XrayTubeCurrent")))
	_set_if_present(ds, "ExposureTime", _float_text(header.get("ExposureTime")))
	_set_if_present(ds, "ReconstructionDiameter", _float_text(header.get("ReconstructionDiameter")))
	_set_if_present(ds, "WindowCenter", header.get("WindowCenter"))
	_set_if_present(ds, "WindowWidth", header.get("WindowWidth"))
	_set_if_present(ds, "RescaleIntercept", _float_text(header.get("RescaleIntercept")))
	_set_if_present(ds, "RescaleSlope", _float_text(header.get("RescaleSlope")))
	_set_if_present(ds, "RescaleType", header.get("RescaleType"))

	ds.Rows = int(header["Rows"])
	ds.Columns = int(header["Columns"])
	ds.SamplesPerPixel = int(header.get("SamplesPerPixel") or 1)
	ds.PhotometricInterpretation = str(header.get("PhotometricInterpretation") or "MONOCHROME2")
	ds.BitsAllocated = int(header.get("BitsAllocated") or 16)
	ds.BitsStored = int(header.get("BitsStored") or ds.BitsAllocated)
	ds.HighBit = int(header.get("HighBit") or (ds.BitsStored - 1))
	ds.PixelRepresentation = int(header.get("PixelRepresentation") or 0)
	ds.PixelData = pixel_data

	filename.parent.mkdir(parents=True, exist_ok=True)
	temp = filename.with_name(filename.name + ".part")
	temp.unlink(missing_ok=True)
	try:
		ds.save_as(temp, enforce_file_format=True)
		temp.replace(filename)
	except Exception:
		temp.unlink(missing_ok=True)
		raise


async def _download_zfp_study(zfp_url: str):
	async with async_playwright() as driver:
		browser = await launch_browser(driver, headless=True)
		try:
			async with await browser.new_context() as context:
				page = await context.new_page()
				await page.add_init_script(_ZFP_HOOK_SCRIPT)
				study = await _wait_zfp_metadata(page, zfp_url)
				await page.wait_for_function("window.__cloudDicomZfpReady && window.__cloudDicomZfpReady()", timeout=60000)

				entries = sorted(_image_entries(study), key=_token_sort_key)
				if not entries:
					raise ValueError("ZFP 查看器没有返回可下载的影像序列。")

				save_to = _study_save_dir(study)
				patient = _person_name(study.get("PatientName")) or "匿名"
				print(f"下载 {patient} 的 ZFP DICOM，共 {len(entries)} 张。")
				print(f"保存到: {save_to}")

				progress = tqdm(entries, unit="张", file=sys.stdout)
				directories: dict[str, SeriesDirectory] = {}
				for index, entry in enumerate(progress):
					series_uid = str(entry.series.get("SeriesInstanceUid") or index)
					desc = str(entry.series.get("SeriesDescription") or entry.series.get("SeriesModality") or "Unnamed").strip() or "Unnamed"
					directory = directories.get(series_uid)
					if directory is None:
						size = sum(1 for item in entries if item.series is entry.series)
						directory = SeriesDirectory(save_to, _series_number(entry.series), desc, size)
						directories[series_uid] = directory

					progress.set_description(desc)
					header, pixel_data = await _download_zfp_image(page, entry)
					local_index = sum(1 for item in entries[:index] if item.series is entry.series)
					path = directory.get(local_index, "dcm")
					_write_dicom(study, entry.series, entry.sop, header, pixel_data, path)
					directory.mark_complete(local_index)

				for directory in directories.values():
					directory.ensure_complete()

				print(f"下载完成，保存位置 {save_to}")
		finally:
			await browser.close()


async def _resolve_zfp_url(address: URL, authority_code: str) -> str:
	link = _parse_cif_link(address)
	async with new_http_client() as client:
		access = await _decrypt_link(client, address, link)
		headers = await _login_with_access_code(client, address, access, authority_code)
		report = await _load_report_info(client, address, access, headers)
	return str(report["zfpUrl"])


async def run(url: str, authority_code: str | None = None, *_):
	if not authority_code:
		raise ValueError("该链接需要填写访问码。")

	address = URL(url)
	zfp_url = await _resolve_zfp_url(address, authority_code)
	await _download_zfp_study(zfp_url)
