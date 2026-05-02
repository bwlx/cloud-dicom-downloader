import re
from pathlib import Path

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _ordered_unique(items: list[str]) -> list[str]:
	seen = set()
	result = []
	for item in items:
		if item not in seen:
			seen.add(item)
			result.append(item)
	return result


def extract_candidate_urls(text: str) -> list[str]:
	return _ordered_unique(match.group(0).rstrip(".,);]。；，】）") for match in _URL_RE.finditer(text))


def pick_share_url(payloads: list[str]) -> str | None:
	candidates = []
	for payload in payloads:
		candidates.extend(extract_candidate_urls(payload))
		if payload.startswith(("http://", "https://")):
			candidates.append(payload.strip())

	candidates = _ordered_unique(candidates)
	if not candidates:
		return None

	from desktop_core import resolve_crawler_module

	for candidate in candidates:
		try:
			resolve_crawler_module(candidate)
		except Exception:
			continue
		return candidate

	return candidates[0]


def _load_image(path: Path):
	try:
		import cv2
		import numpy
	except ImportError as exc:
		raise RuntimeError("未安装图片扫码依赖，请执行 pip install -r requirements-desktop.txt") from exc

	data = numpy.fromfile(path, dtype=numpy.uint8)
	image = cv2.imdecode(data, cv2.IMREAD_COLOR)
	if image is None:
		raise ValueError("无法读取图片文件。")
	return cv2, image


def _decode_variant(detector, image) -> list[str]:
	results = []
	try:
		ok, decoded, _, _ = detector.detectAndDecodeMulti(image)
	except Exception:
		ok, decoded = False, []

	if ok and decoded:
		results.extend(item.strip() for item in decoded if item and item.strip())

	try:
		single, _, _ = detector.detectAndDecode(image)
	except Exception:
		single = ""

	if single:
		results.append(single.strip())

	return _ordered_unique(results)


def decode_qr_image(image_path: str | Path) -> list[str]:
	path = Path(image_path)
	cv2, image = _load_image(path)
	gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

	# --- 优先用 zxing-cpp，识别率远高于 cv2 内置 QRCodeDetector ---
	try:
		import zxingcpp

		def _zxing_read(img):
			results = []
			for binarizer in (zxingcpp.LocalAverage, zxingcpp.GlobalHistogram):
				barcodes = zxingcpp.read_barcodes(
					img,
					formats=zxingcpp.QRCode,
					try_rotate=True,
					try_downscale=True,
					try_invert=True,
					binarizer=binarizer,
				)
				for b in barcodes:
					text = b.text.strip()
					if text:
						results.append(text)
				if results:
					return _ordered_unique(results)
			return results

		# 原图 → 灰度 → 2x 放大灰度 → 自适应阈值（适合深色背景截图）
		upscaled = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
		adaptive_large = cv2.adaptiveThreshold(
			gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 71, 5
		)
		adaptive_large_up = cv2.resize(adaptive_large, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)

		for variant in (image, gray, upscaled, adaptive_large, adaptive_large_up):
			found = _zxing_read(variant)
			if found:
				return found
	except ImportError:
		pass

	# --- 回退：cv2 内置 QRCodeDetector ---
	detector = cv2.QRCodeDetector()
	upscaled = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
	_, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
	_, otsu_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
	adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)
	adaptive_inv = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 2)
	adaptive_large = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 71, 5)
	adaptive_large_inv = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 71, 5)

	variants = [
		image, gray, upscaled,
		cv2.GaussianBlur(gray, (3, 3), 0),
		otsu, otsu_inv, adaptive, adaptive_inv, adaptive_large, adaptive_large_inv,
		cv2.resize(otsu, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST),
		cv2.resize(adaptive, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST),
	]

	results = []
	for variant in variants:
		results.extend(_decode_variant(detector, variant))
		if results:
			return _ordered_unique(results)

	# 尝试使用 WeChatQRCode 检测器（识别率更高，需要额外模型文件）。
	try:
		wechat = cv2.wechat_qrcode_WeChatQRCode()
		for variant in [image, gray, upscaled, adaptive_large, adaptive_large_inv]:
			texts, _ = wechat.detectAndDecode(variant)
			if texts:
				results.extend(t.strip() for t in texts if t and t.strip())
			if results:
				return _ordered_unique(results)
	except Exception:
		pass

	return _ordered_unique(results)
