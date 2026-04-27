import asyncio
import base64
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

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
	generate_uid,
)
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from tqdm import tqdm
from yarl import URL

from crawlers._browser import launch_browser
from crawlers._utils import IncompleteDownloadError, SeriesDirectory, suggest_save_dir

_SHARE_HOST = "zjyx.gjwlyy.com"
_VIEWER_HOST = "zjyxview.gjwlyy.com"
_SHARE_PATH = "/cloudfilmserver/cloudFilm/showShareReport.htm"
_VIEWER_PATH = "/e/viewer"
_VIEWER_READY_SCRIPT = """
() => {
	try {
		if (!window.VIEWER || typeof VIEWER.getViewerState !== "function") {
			return false;
		}
		const state = VIEWER.getViewerState();
		return !!state?.selectedImage?.instanceUID
			&& Array.isArray(state?.seriesData)
			&& state.seriesData.length > 0;
	} catch (error) {
		return false;
	}
}
"""
_VIEWER_STATE_SCRIPT = """
() => {
	try {
		return JSON.parse(JSON.stringify(VIEWER.getViewerState()));
	} catch (error) {
		return null;
	}
}
"""
_GET_CAPTURE_SCRIPT = """
({ key, idleMs }) => {
	return window.__cddPixelCapture?.getReady(key, idleMs) || null;
}
"""
_RELEASE_CAPTURE_SCRIPT = """
(key) => {
	window.__cddPixelCapture?.release(key);
}
"""
_THUMBNAIL_PANEL_SCRIPT = """
() => {
	const canvas = document.querySelector("#thumbnailListContainer");
	const pin = document.querySelector("button.pinBtn");
	if (!canvas) {
		return null;
	}
	const rect = canvas.getBoundingClientRect();
	const pinRect = pin ? pin.getBoundingClientRect() : null;
	return {
		x: rect.x,
		y: rect.y,
		width: rect.width,
		height: rect.height,
		visible: rect.height > 40 && rect.y < window.innerHeight - 40,
		pinX: pinRect ? pinRect.x + pinRect.width / 2 : null,
		pinY: pinRect ? pinRect.y + pinRect.height / 2 : null
	};
}
"""
_PIXEL_HOOK_INIT_SCRIPT = r"""
(function () {
	"use strict";

	function toBase64(u8) {
		var s = "";
		var chunk = 0x8000;
		for (var i = 0; i < u8.length; i += chunk) {
			s += String.fromCharCode.apply(null, u8.subarray(i, i + chunk));
		}
		return btoa(s);
	}

	function cloneSelected() {
		try {
			var image = window.VIEWER && VIEWER.getViewerState && VIEWER.getViewerState().selectedImage;
			if (!image || !image.instanceUID) {
				return null;
			}
			return {
				studyUID: String(image.studyUID || ""),
				seriesUID: String(image.seriesUID || ""),
				instanceUID: String(image.instanceUID || ""),
				viewerInstanceID: String(image.viewerInstanceID || image.instanceUID || ""),
				instanceNumber: Number(image.instanceNumber || 0),
				frameNumber: Number(image.frameNumber || 1)
			};
		} catch (_) {
			return null;
		}
	}

	window.__cddPixelCapture = {
		images: Object.create(null),
		sequence: 0,
		getKey: function (selected) {
			if (!selected) {
				return "";
			}
			return [
				selected.viewerInstanceID || selected.instanceUID || "",
				selected.instanceNumber,
				selected.frameNumber
			].join(":");
		},
		ensure: function (selected) {
			var key = this.getKey(selected);
			if (!key) {
				return null;
			}
			if (!this.images[key]) {
				this.images[key] = {
					key: key,
					selected: selected,
					tiles: [],
					meta: {},
					createdAt: Date.now(),
					updatedAt: 0
				};
			}
			return this.images[key];
		},
		setModalityLut: function (slope, intercept) {
			var selected = cloneSelected();
			var image = this.ensure(selected);
			if (!image) {
				return;
			}
			if (image.meta.rescaleSlope == null) {
				image.meta.rescaleSlope = Number(slope);
			}
			if (image.meta.rescaleIntercept == null) {
				image.meta.rescaleIntercept = Number(intercept);
			}
			image.updatedAt = Date.now();
		},
		setDisplayLut: function (args) {
			var selected = cloneSelected();
			var image = this.ensure(selected);
			if (!image) {
				return;
			}
			if (image.meta.bytesAllocated == null) {
				image.meta.bytesAllocated = Number(args[0] || 0);
			}
			if (image.meta.isSigned == null) {
				image.meta.isSigned = !!args[1];
			}
			if (image.meta.bitsStored == null) {
				image.meta.bitsStored = Number(args[2] || 0);
			}
			if (image.meta.windowCenter == null) {
				image.meta.windowCenter = Number(args[3] || 0);
			}
			if (image.meta.windowWidth == null) {
				image.meta.windowWidth = Number(args[4] || 0);
			}
			image.updatedAt = Date.now();
		},
		addTile: function (name, info, bytes) {
			var selected = cloneSelected();
			var image = this.ensure(selected);
			if (!image || !bytes || bytes.length === 0) {
				return;
			}
			image.tiles.push({
				order: this.sequence++,
				name: name,
				width: info.width,
				height: info.height,
				bytesPerSample: info.bytesPerSample,
				samplesPerPixel: info.samplesPerPixel,
				signed: info.signed,
				data: toBase64(bytes)
			});
			if (image.meta.bytesAllocated == null) {
				image.meta.bytesAllocated = info.bytesPerSample;
			}
			if (image.meta.isSigned == null) {
				image.meta.isSigned = !!info.signed;
			}
			image.updatedAt = Date.now();
		},
		getReady: function (key, idleMs) {
			var image = this.images[key];
			if (!image || image.tiles.length === 0 || !image.updatedAt) {
				return null;
			}
			if (Date.now() - image.updatedAt < idleMs) {
				return null;
			}
			return image;
		},
		release: function (key) {
			delete this.images[key];
		}
	};

	function copyBytes(ptr, length) {
		if (!window.Module || !Module.HEAPU8 || !ptr || !length) {
			return null;
		}
		var heap = Module.HEAPU8;
		var end = ptr + length;
		if (ptr < 0 || end > heap.length) {
			return null;
		}
		return new Uint8Array(heap.slice(ptr, end));
	}

	function captureHaar(name, args) {
		var ptr = 0;
		var width = 0;
		var height = 0;
		var region = null;
		var bytesPerSample = 2;
		var samplesPerPixel = 1;
		var signed = false;

		if (name === "inverseHaar16FromByteArrays") {
			signed = !!args[0];
			ptr = Number(args[1] || 0);
			width = Number(args[2] || 0);
			region = args[3];
			bytesPerSample = 2;
		} else if (name === "inverseHaar8FromByteArrays") {
			signed = !!args[0];
			ptr = Number(args[1] || 0);
			width = Number(args[2] || 0);
			region = args[3];
			bytesPerSample = 1;
		} else if (name === "inverseHaarFromByteArrays") {
			ptr = Number(args[0] || 0);
			width = Number(args[1] || 0);
			region = args[2];
			bytesPerSample = 2;
		} else if (name === "inverseHaarColorSeparatePlaneFromByteArrays") {
			ptr = Number(args[0] || 0);
			width = Number(args[3] || 0);
			height = Number(args[4] || 0);
			region = args[5];
			bytesPerSample = 1;
			samplesPerPixel = 3;
		}

		if (region && region.length >= 4) {
			width = Math.round(Number(region[2] || width));
			height = Math.round(Number(region[3] || height));
		}
		if (!ptr || width <= 0 || height <= 0) {
			return;
		}

		var length = width * height * bytesPerSample * samplesPerPixel;
		var bytes = copyBytes(ptr, length);
		window.__cddPixelCapture.addTile(name, {
			width: width,
			height: height,
			bytesPerSample: bytesPerSample,
			samplesPerPixel: samplesPerPixel,
			signed: signed
		}, bytes);
	}

	function wrapModuleFunction(name, handler) {
		if (!window.Module || typeof Module[name] !== "function" || Module[name].__cddWrapped) {
			return false;
		}
		var original = Module[name];
		Module[name] = function () {
			var result = original.apply(this, arguments);
			try {
				handler(Array.prototype.slice.call(arguments), result);
			} catch (_) {}
			return result;
		};
		Module[name].__cddWrapped = true;
		return true;
	}

	function installHooks() {
		wrapModuleFunction("inverseHaar16FromByteArrays", function (args) {
			captureHaar("inverseHaar16FromByteArrays", args);
		});
		wrapModuleFunction("inverseHaar8FromByteArrays", function (args) {
			captureHaar("inverseHaar8FromByteArrays", args);
		});
		wrapModuleFunction("inverseHaarFromByteArrays", function (args) {
			captureHaar("inverseHaarFromByteArrays", args);
		});
		wrapModuleFunction("inverseHaarColorSeparatePlaneFromByteArrays", function (args) {
			captureHaar("inverseHaarColorSeparatePlaneFromByteArrays", args);
		});
		wrapModuleFunction("createNewModalityLutFromRescale", function (args) {
			window.__cddPixelCapture.setModalityLut(args[0], args[1]);
		});
		wrapModuleFunction("createNewLutInMemoryHandle", function (args) {
			window.__cddPixelCapture.setDisplayLut(args);
		});
		wrapModuleFunction("createNewLutToGrayXXInMemoryHandle", function (args) {
			window.__cddPixelCapture.setDisplayLut(args);
		});
	}

	var timer = setInterval(installHooks, 20);
	window.addEventListener("unload", function () {
		clearInterval(timer);
	});
}());
"""

_PATIENT_NAME_SUFFIX = re.compile(r"\s*:.*$")
_UID_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
_THUMBNAIL_FIRST_CENTER_X = 63
_THUMBNAIL_ITEM_PITCH = 125
_THUMBNAIL_CENTER_Y = 55


@dataclass(slots=True)
class ShareLink:
	url: str
	is_viewer: bool


@dataclass(slots=True)
class StudyInfo:
	patient_name: str
	patient_id: str
	patient_sex: str
	study_uid: str
	accession_number: str
	description: str
	modality: str
	patient_birthdate: str
	series_uids: list[str]


@dataclass(slots=True)
class SelectedImage:
	study_uid: str
	series_uid: str
	instance_uid: str
	viewer_instance_id: str
	instance_number: int
	frame_number: int


@dataclass(slots=True)
class PixelTile:
	order: int
	width: int
	height: int
	bytes_per_sample: int
	samples_per_pixel: int
	signed: bool
	data: bytes


@dataclass(slots=True)
class PixelFrame:
	rows: int
	columns: int
	bytes_allocated: int
	bits_stored: int
	pixel_representation: int
	samples_per_pixel: int
	pixel_data: bytes
	rescale_slope: float | None = None
	rescale_intercept: float | None = None
	window_center: float | None = None
	window_width: float | None = None
	photometric_interpretation: str = "MONOCHROME2"


@dataclass(slots=True)
class ShareMetadata:
	patient_name: str = ""
	patient_id: str = ""
	patient_sex: str = ""
	patient_birthdate: str = ""
	accession_number: str = ""
	study_uid: str = ""
	description: str = ""
	modality: str = ""


@dataclass
class ShareMetadataCapture:
	data: ShareMetadata = field(default_factory=ShareMetadata)

	async def on_response(self, response) -> None:
		if not response.url.endswith("/cloudFilm/queryShareReport.htm"):
			return
		try:
			payload = await response.json()
			data = payload.get("data") or {}
		except Exception:
			return

		self.data = ShareMetadata(
			patient_name=str(data.get("hPatientName") or "").strip(),
			patient_id=str(data.get("hPatientId") or "").strip(),
			patient_sex=str(data.get("hPatientSex") or "").strip(),
			patient_birthdate=_format_dicom_date(str(data.get("hPatientDob") or "")),
			accession_number=str(data.get("hAccessionNumber") or "").strip(),
			study_uid=str(data.get("hStudiesInstUId") or "").strip(),
			description=str(data.get("hServiceExaminealias") or "").strip(),
			modality=str(data.get("serviceModalities") or "").split(",")[0].strip(),
		)


def _parse_share_link(address: URL) -> ShareLink:
	host = address.host or ""
	if host == _SHARE_HOST and address.path == _SHARE_PATH and address.query.get("key"):
		return ShareLink(url=str(address), is_viewer=False)
	if host == _VIEWER_HOST and address.path == _VIEWER_PATH and address.query.get("CLOAccessKeyID") and address.query.get("arg"):
		return ShareLink(url=str(address), is_viewer=True)
	raise ValueError("当前链接不是受支持的浙江大学医学院附属第二医院云影像分享链接。")


def _with_single_series_layout(url: str) -> str:
	address = URL(url)
	query = dict(address.query)
	query["format"] = "1upSeriesBox"
	return str(address.update_query(query))


def _clean_patient_name(name: str) -> str:
	text = str(name or "").strip()
	if not text:
		return "匿名"
	text = _PATIENT_NAME_SUFFIX.sub("", text).strip()
	return text or "匿名"


def _coerce_int(value, default=0):
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _coerce_float(value) -> float | None:
	if value in (None, ""):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _format_dicom_date(text: str) -> str:
	return re.sub(r"\D+", "", str(text or ""))[:8]


def _valid_uid(uid: str) -> bool:
	return bool(uid and len(uid) <= 64 and _UID_RE.fullmatch(uid))


def _stable_uid(seed: str) -> str:
	return generate_uid(entropy_srcs=[seed])[:64]


def _dicom_uid(uid: str, fallback_seed: str) -> str:
	return uid if _valid_uid(uid) else _stable_uid(fallback_seed)


def _viewer_state_to_study_info(viewer_state: dict, metadata: ShareMetadata | None = None) -> StudyInfo:
	metadata = metadata or ShareMetadata()
	patient = viewer_state.get("patient") or {}
	studies = viewer_state.get("studies") or []
	series_data = viewer_state.get("seriesData") or []
	if not studies or not series_data:
		raise ValueError("影像查看器没有返回任何检查或序列信息。")

	study = studies[0]
	series_uids = []
	for series in series_data:
		uid = str(series.get("seriesUID") or "").strip()
		if uid and uid not in series_uids:
			series_uids.append(uid)

	if not series_uids:
		raise ValueError("影像查看器没有返回可用的序列标识。")

	return StudyInfo(
		patient_name=_clean_patient_name(metadata.patient_name or patient.get("name")),
		patient_id=metadata.patient_id or str(patient.get("patientID") or "").strip(),
		patient_sex=metadata.patient_sex or str(patient.get("gender") or "").strip(),
		study_uid=metadata.study_uid or str(study.get("uid") or "").strip(),
		accession_number=metadata.accession_number or str(study.get("accessionNumber") or "").strip(),
		description=metadata.description or str(study.get("description") or "").strip() or "云影像",
		modality=(metadata.modality or "CT").upper(),
		patient_birthdate=metadata.patient_birthdate,
		series_uids=series_uids,
	)


def _selected_image(viewer_state: dict) -> SelectedImage:
	selected = viewer_state.get("selectedImage") or {}
	study_uid = str(selected.get("studyUID") or "").strip()
	series_uid = str(selected.get("seriesUID") or "").strip()
	instance_uid = str(selected.get("instanceUID") or "").strip()
	viewer_instance_id = str(selected.get("viewerInstanceID") or instance_uid).strip()
	if not series_uid or not instance_uid:
		raise ValueError("影像查看器没有返回当前图像。")

	return SelectedImage(
		study_uid=study_uid,
		series_uid=series_uid,
		instance_uid=instance_uid,
		viewer_instance_id=viewer_instance_id,
		instance_number=_coerce_int(selected.get("instanceNumber"), 0),
		frame_number=_coerce_int(selected.get("frameNumber"), 1),
	)


def _capture_key(selected: SelectedImage) -> str:
	return f"{selected.viewer_instance_id or selected.instance_uid}:{selected.instance_number}:{selected.frame_number}"


def _thumbnail_click_point(panel: dict, series_index: int) -> tuple[float, float] | None:
	x = float(panel.get("x", 0)) + _THUMBNAIL_FIRST_CENTER_X + _THUMBNAIL_ITEM_PITCH * series_index
	y = float(panel.get("y", 0)) + _THUMBNAIL_CENTER_Y
	right = float(panel.get("x", 0)) + float(panel.get("width", 0))
	bottom = float(panel.get("y", 0)) + float(panel.get("height", 0))
	if x >= right - 20 or y >= bottom - 10:
		return None
	return x, y


def _series_save_dir(study: StudyInfo, series_index: int, size: int):
	return SeriesDirectory(
		suggest_save_dir(study.patient_name, study.description, study.accession_number or study.study_uid),
		series_index + 1,
		f"序列 {series_index + 1}",
		size,
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
	}.get((modality or "").upper(), SecondaryCaptureImageStorage)


def _decode_tiles(capture: dict) -> list[PixelTile]:
	tiles = []
	for tile in capture.get("tiles") or []:
		try:
			width = int(tile.get("width") or 0)
			height = int(tile.get("height") or 0)
			bytes_per_sample = int(tile.get("bytesPerSample") or 0)
			samples_per_pixel = int(tile.get("samplesPerPixel") or 1)
			data = base64.b64decode(tile.get("data") or "")
		except Exception:
			continue
		if width <= 0 or height <= 0 or bytes_per_sample <= 0 or not data:
			continue
		tiles.append(PixelTile(
			order=int(tile.get("order") or 0),
			width=width,
			height=height,
			bytes_per_sample=bytes_per_sample,
			samples_per_pixel=samples_per_pixel,
			signed=bool(tile.get("signed")),
			data=data,
		))
	return sorted(tiles, key=lambda item: item.order)


def _largest_tile_group(tiles: list[PixelTile]) -> list[PixelTile]:
	if not tiles:
		raise ValueError("未截获到解码后的像素 tile。")
	max_area = max(tile.width * tile.height for tile in tiles)
	group = [tile for tile in tiles if tile.width * tile.height == max_area]
	if not group:
		raise ValueError("未找到完整分辨率的像素 tile。")

	first = group[0]
	group = [
		tile
		for tile in group
		if tile.bytes_per_sample == first.bytes_per_sample
		and tile.samples_per_pixel == first.samples_per_pixel
		and tile.signed == first.signed
	]
	return group


def _infer_grid(tile_count: int) -> tuple[int, int]:
	if tile_count <= 0:
		raise ValueError("像素 tile 数量为空。")
	root = int(math.sqrt(tile_count))
	if root * root == tile_count:
		return root, root
	columns = math.ceil(math.sqrt(tile_count))
	rows = math.ceil(tile_count / columns)
	return rows, columns


def _assemble_tiles(capture: dict) -> PixelFrame:
	tiles = _largest_tile_group(_decode_tiles(capture))
	first = tiles[0]
	rows_in_grid, columns_in_grid = _infer_grid(len(tiles))
	rows = rows_in_grid * first.height
	columns = columns_in_grid * first.width
	bytes_per_pixel = first.bytes_per_sample * first.samples_per_pixel
	row_stride = columns * bytes_per_pixel
	output = bytearray(rows * row_stride)

	for index, tile in enumerate(tiles):
		tile_len = tile.width * tile.height * bytes_per_pixel
		if len(tile.data) < tile_len:
			raise ValueError("截获到的像素 tile 不完整。")
		tile_row = index // columns_in_grid
		tile_column = index % columns_in_grid
		x = tile_column * first.width
		y = tile_row * first.height
		for source_row in range(tile.height):
			source_start = source_row * tile.width * bytes_per_pixel
			source_end = source_start + tile.width * bytes_per_pixel
			target_start = (y + source_row) * row_stride + x * bytes_per_pixel
			output[target_start:target_start + tile.width * bytes_per_pixel] = tile.data[source_start:source_end]

	meta = capture.get("meta") or {}
	bytes_allocated = _coerce_int(meta.get("bytesAllocated"), first.bytes_per_sample)
	if bytes_allocated not in {1, 2, 4}:
		bytes_allocated = first.bytes_per_sample
	bits_stored = _coerce_int(meta.get("bitsStored"), bytes_allocated * 8)
	if bits_stored <= 0:
		bits_stored = bytes_allocated * 8

	pixel_data = bytes(output)
	if len(pixel_data) % 2:
		pixel_data += b"\x00"

	return PixelFrame(
		rows=rows,
		columns=columns,
		bytes_allocated=bytes_allocated,
		bits_stored=bits_stored,
		pixel_representation=1 if bool(meta.get("isSigned", first.signed)) else 0,
		samples_per_pixel=first.samples_per_pixel,
		pixel_data=pixel_data,
		rescale_slope=_coerce_float(meta.get("rescaleSlope")),
		rescale_intercept=_coerce_float(meta.get("rescaleIntercept")),
		window_center=_coerce_float(meta.get("windowCenter")),
		window_width=_coerce_float(meta.get("windowWidth")),
		photometric_interpretation="RGB" if first.samples_per_pixel > 1 else "MONOCHROME2",
	)


def _build_dicom(study: StudyInfo, selected: SelectedImage, frame: PixelFrame, *, series_index: int, image_index: int) -> Dataset:
	ds = Dataset()
	ds.file_meta = FileMetaDataset()

	study_uid = _dicom_uid(selected.study_uid or study.study_uid, f"{study.study_uid}:study")
	series_uid = _dicom_uid(selected.series_uid, f"{study_uid}:{series_index}:series")
	instance_uid_seed = f"{series_uid}:{selected.instance_uid}:{selected.instance_number}:{selected.frame_number}:{image_index}:instance"
	if selected.frame_number > 1:
		instance_uid = _stable_uid(instance_uid_seed)
	else:
		instance_uid = _dicom_uid(selected.instance_uid, instance_uid_seed)
	modality = (study.modality or "CT").upper()

	ds.SOPClassUID = _sop_class_uid(modality)
	ds.SOPInstanceUID = instance_uid
	ds.StudyInstanceUID = study_uid
	ds.SeriesInstanceUID = series_uid
	ds.Modality = modality
	ds.ImageType = ["DERIVED", "PRIMARY"]
	ds.SpecificCharacterSet = "ISO_IR 192"
	ds.PatientName = study.patient_name
	if study.patient_id:
		ds.PatientID = study.patient_id
	if study.patient_sex:
		ds.PatientSex = study.patient_sex
	if study.patient_birthdate:
		ds.PatientBirthDate = study.patient_birthdate
	if study.accession_number:
		ds.AccessionNumber = study.accession_number
	if study.description:
		ds.StudyDescription = study.description
		ds.SeriesDescription = study.description
	ds.SeriesNumber = series_index + 1
	ds.InstanceNumber = image_index + 1
	ds.Rows = frame.rows
	ds.Columns = frame.columns
	ds.SamplesPerPixel = frame.samples_per_pixel
	ds.PhotometricInterpretation = frame.photometric_interpretation
	if frame.samples_per_pixel > 1:
		ds.PlanarConfiguration = 0
	ds.BitsAllocated = frame.bytes_allocated * 8
	ds.BitsStored = frame.bits_stored
	ds.HighBit = max(frame.bits_stored - 1, 0)
	ds.PixelRepresentation = frame.pixel_representation
	if frame.window_center is not None:
		ds.WindowCenter = frame.window_center
	if frame.window_width is not None:
		ds.WindowWidth = frame.window_width
	if frame.rescale_slope is not None:
		ds.RescaleSlope = frame.rescale_slope
	if frame.rescale_intercept is not None:
		ds.RescaleIntercept = frame.rescale_intercept
	ds.PixelData = frame.pixel_data

	ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
	ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
	ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
	ds.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
	return ds


def _write_dicom_file(study: StudyInfo, selected: SelectedImage, frame: PixelFrame, filename: Path, *, series_index: int, image_index: int):
	ds = _build_dicom(study, selected, frame, series_index=series_index, image_index=image_index)
	filename.parent.mkdir(parents=True, exist_ok=True)
	temp = filename.with_name(filename.name + ".part")
	temp.unlink(missing_ok=True)
	try:
		ds.save_as(temp, enforce_file_format=True)
		temp.replace(filename)
	except Exception:
		temp.unlink(missing_ok=True)
		raise


async def _viewer_state(page: Page):
	for _ in range(40):
		state = await page.evaluate(_VIEWER_STATE_SCRIPT)
		if state:
			return state
		await page.wait_for_timeout(250)
	raise ValueError("影像查看器没有返回可用的状态信息。")


async def _open_viewer(page: Page, share: ShareLink):
	await page.context.add_init_script(_PIXEL_HOOK_INIT_SCRIPT)
	await page.goto(_with_single_series_layout(share.url) if share.is_viewer else share.url, wait_until="domcontentloaded", timeout=120000)
	await page.wait_for_timeout(5000)

	if share.is_viewer:
		viewer = page
	else:
		await page.get_by_text("影像浏览").click(timeout=30000)
		await page.wait_for_timeout(5000)
		if page.url.startswith(f"https://{_VIEWER_HOST}{_VIEWER_PATH}") or page.url.startswith(f"http://{_VIEWER_HOST}{_VIEWER_PATH}"):
			viewer = page
		elif len(page.context.pages) >= 2:
			viewer = page.context.pages[-1]
		else:
			raise ValueError("分享页没有打开影像查看器。")

	single_layout_url = _with_single_series_layout(viewer.url)
	if viewer.url != single_layout_url:
		await viewer.goto(single_layout_url, wait_until="domcontentloaded", timeout=120000)

	await viewer.wait_for_load_state("domcontentloaded", timeout=120000)
	await viewer.wait_for_function(_VIEWER_READY_SCRIPT, timeout=120000)
	await viewer.wait_for_timeout(3000)
	return viewer


async def _wait_for_selected_change(page: Page, previous: SelectedImage, *, timeout_ms=15000):
	await page.wait_for_function(
		"""
		(previous) => {
			const [previousSeriesUID, previousInstanceUID, previousInstanceNumber, previousFrameNumber] = previous;
			const state = window.VIEWER?.getViewerState?.();
			const image = state?.selectedImage;
			if (!image) {
				return false;
			}
			return image.seriesUID !== previousSeriesUID
				|| image.instanceUID !== previousInstanceUID
				|| Number(image.instanceNumber || 0) !== previousInstanceNumber
				|| Number(image.frameNumber || 1) !== previousFrameNumber;
		}
		""",
		arg=(previous.series_uid, previous.instance_uid, previous.instance_number, previous.frame_number),
		timeout=timeout_ms,
	)
	return _selected_image(await _viewer_state(page))


async def _wait_for_selected_series(page: Page, series_uid: str, *, timeout_ms=15000):
	await page.wait_for_function(
		"""
		(seriesUID) => {
			const image = window.VIEWER?.getViewerState?.()?.selectedImage;
			return image?.seriesUID === seriesUID;
		}
		""",
		arg=series_uid,
		timeout=timeout_ms,
	)
	return _selected_image(await _viewer_state(page))


async def _show_thumbnail_panel(page: Page) -> dict:
	for _ in range(3):
		panel = await page.evaluate(_THUMBNAIL_PANEL_SCRIPT)
		if panel and panel.get("visible"):
			return panel
		if panel and panel.get("pinX") is not None and panel.get("pinY") is not None:
			await page.mouse.click(panel["pinX"], panel["pinY"])
		else:
			await page.locator("button.pinBtn").click(timeout=5000)
		await page.wait_for_timeout(1000)
	panel = await page.evaluate(_THUMBNAIL_PANEL_SCRIPT)
	if panel and panel.get("visible"):
		return panel
	raise ValueError("影像查看器没有显示序列缩略图栏，无法切换序列。")


async def _select_series_by_thumbnail(page: Page, series_uid: str, series_index: int) -> SelectedImage:
	current = _selected_image(await _viewer_state(page))
	if current.series_uid == series_uid:
		return current

	panel = await _show_thumbnail_panel(page)
	point = _thumbnail_click_point(panel, series_index)
	if point is None:
		raise ValueError(f"序列 {series_index + 1} 的缩略图不在可见区域内，无法自动切换。")

	await page.mouse.click(*point)
	return await _wait_for_selected_series(page, series_uid)


async def _press_until_series(page: Page, series_uid: str, *, attempts: int):
	for _ in range(attempts):
		current = _selected_image(await _viewer_state(page))
		if current.series_uid == series_uid:
			return current
		await page.keyboard.press("ArrowRight")
		try:
			await _wait_for_selected_change(page, current, timeout_ms=5000)
		except PlaywrightTimeoutError:
			break

	current = _selected_image(await _viewer_state(page))
	if current.series_uid == series_uid:
		return current
	raise ValueError(f"无法切换到目标序列：{series_uid}")


async def _goto_home(page: Page):
	current = _selected_image(await _viewer_state(page))
	await page.keyboard.press("Home")
	try:
		await _wait_for_selected_change(page, current, timeout_ms=8000)
	except PlaywrightTimeoutError:
		pass
	return _selected_image(await _viewer_state(page))


async def _goto_end(page: Page):
	current = _selected_image(await _viewer_state(page))
	await page.keyboard.press("End")
	try:
		await _wait_for_selected_change(page, current, timeout_ms=10000)
	except PlaywrightTimeoutError:
		pass
	return _selected_image(await _viewer_state(page))


async def _wait_for_pixel_capture(page: Page, selected: SelectedImage, *, timeout_ms: int = 60000) -> dict:
	key = _capture_key(selected)
	loop = asyncio.get_running_loop()
	deadline = loop.time() + timeout_ms / 1000.0
	while True:
		capture = await page.evaluate(_GET_CAPTURE_SCRIPT, {"key": key, "idleMs": 900})
		if capture:
			return capture
		if loop.time() >= deadline:
			raise ValueError(f"等待网页解码像素超时（UID: …{selected.instance_uid[-20:]}）")
		await page.wait_for_timeout(200)


async def _release_pixel_capture(page: Page, selected: SelectedImage):
	await page.evaluate(_RELEASE_CAPTURE_SCRIPT, _capture_key(selected))


async def _download_series(page: Page, *, study: StudyInfo, series_uid: str, series_index: int):
	try:
		await _select_series_by_thumbnail(page, series_uid, series_index)
	except ValueError:
		current = _selected_image(await _viewer_state(page))
		if current.series_uid != series_uid:
			await _press_until_series(page, series_uid, attempts=len(study.series_uids) + 1)

	last = await _goto_end(page)
	last_instance_number = last.instance_number
	first = await _goto_home(page)
	image_count = last_instance_number - first.instance_number + 1
	if image_count <= 0:
		raise ValueError(f"序列 {series_index + 1} 没有返回可导出的图像。")

	directory = _series_save_dir(study, series_index, image_count)
	progress = tqdm(range(image_count), desc=f"序列 {series_index + 1}", unit="张", file=sys.stdout)
	try:
		for loop_index in progress:
			selected = _selected_image(await _viewer_state(page))
			capture = await _wait_for_pixel_capture(page, selected)
			frame = _assemble_tiles(capture)
			filename = directory.get(loop_index, "dcm")
			_write_dicom_file(study, selected, frame, filename, series_index=series_index, image_index=loop_index)
			directory.mark_complete(loop_index)
			await _release_pixel_capture(page, selected)

			if loop_index + 1 < image_count:
				await page.keyboard.press("ArrowDown")
				await _wait_for_selected_change(page, selected)
	finally:
		progress.close()

	directory.ensure_complete()


async def run(url: str, *_):
	share = _parse_share_link(URL(url))
	metadata = ShareMetadataCapture()
	async with async_playwright() as driver:
		browser = await launch_browser(driver, headless=True)
		try:
			async with await browser.new_context(viewport={"width": 1280, "height": 1000}) as context:
				context.on("response", metadata.on_response)
				entry = await context.new_page()
				viewer = await _open_viewer(entry, share)
				viewer_state = await _viewer_state(viewer)
				study = _viewer_state_to_study_info(viewer_state, metadata.data)
				save_to = suggest_save_dir(
					study.patient_name,
					study.description,
					study.accession_number or study.study_uid,
				)

				print("该站点通过网页 JS Hook 获取解码后的完整像素，并重建为 DICOM 文件。")
				print(f"下载 {study.patient_name} 的 {study.modality or 'OT'} DICOM，共 {len(study.series_uids)} 个序列。")
				print(f"保存到: {save_to}\n")

				for series_index, series_uid in enumerate(study.series_uids):
					await _download_series(viewer, study=study, series_uid=series_uid, series_index=series_index)
		except PlaywrightTimeoutError as exc:
			raise ValueError("影像查看器响应超时，站点可能暂时不可用。") from exc
		except IncompleteDownloadError:
			raise
		finally:
			await browser.close()
