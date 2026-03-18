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
	detector = cv2.QRCodeDetector()

	variants = [image]
	gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
	variants.append(gray)
	variants.append(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
	variants.append(cv2.GaussianBlur(gray, (3, 3), 0))
	variants.append(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
	variants.append(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2))

	results = []
	for variant in variants:
		results.extend(_decode_variant(detector, variant))
		if results:
			return _ordered_unique(results)

	return []
