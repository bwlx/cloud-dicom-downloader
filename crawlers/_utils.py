import asyncio
import math
import os
import re
import ssl
import sys
from base64 import b64encode
from hashlib import sha256
from io import TextIOWrapper
from pathlib import Path
from typing import Optional
from zipfile import ZipFile

import aiohttp
import certifi
from pydicom import Dataset
from pydicom.tag import Tag
from pydicom.valuerep import VR, STR_VR, INT_VR, FLOAT_VR
from tqdm import tqdm

from runtime_config import DOWNLOAD_ROOT_ENV

# 这儿的请求头也就意思一下，真要处理请求特征反爬还得使用自动化浏览器。
_HEADERS = {
	"Accept-Language": "zh,zh-CN;q=0.7,en;q=0.3",
	"Accept": "*/*",
	"Upgrade-Insecure-Requests": "1",
	"User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/143.0",
}
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_DOWNLOAD_RETRY_ATTEMPTS = 3
_DOWNLOAD_CHUNK_SIZE = 16384


# noinspection PyTypeChecker
async def _dump_response_check(response: aiohttp.ClientResponse):
	"""
	检查响应码，如果大于等于 400 则转储该响应的数据到一个压缩包，并抛出异常。
	"""
	if response.ok:
		return

	with ZipFile('dump.zip', 'w') as pack:
		a, b = response.version

		with TextIOWrapper(pack.open("request.headers", "w")) as fp:
			fp.write(f"{response.method} {response.url.path_qs} HTTP{a}/{b}")
			for k, v in response.request_info.headers.items():
				fp.write(f"\r\n{k}: {v}")

		with pack.open("response.headers", "w") as fp:
			a = f"HTTP{a}/{b} {response.status} {response.reason}"
			fp.write(a.encode())

			for k, v in response.raw_headers:
				fp.write(b"\r\n" + k + b": " + v)

		with pack.open("response.body", "w") as fp:
			async for chunk in response.content.iter_chunked(16384):
				fp.write(chunk)

	print("响应已转储到 dump.zip", file=sys.stderr)

	response.raise_for_status()  # 继续 aiohttp 内置的处理，让调用端保持一致。


def new_http_client(*args, **kwargs):
	headers = kwargs.get("headers")
	kwargs.setdefault("raise_for_status", _dump_response_check)
	if headers:
		kwargs["headers"] = _HEADERS | headers
	else:
		kwargs["headers"] = _HEADERS
	
	# 使用 quote_cookie=False 避免对包含特殊字符的 cookie 值进行引号处理
	kwargs.setdefault("cookie_jar", aiohttp.CookieJar(quote_cookie=False))
	kwargs.setdefault("connector", aiohttp.TCPConnector(ssl=_SSL_CONTEXT))

	return aiohttp.ClientSession(*args, **kwargs)


class IncompleteDownloadError(Exception):
	pass


def _is_retriable_download_error(exc: Exception):
	if isinstance(exc, (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, asyncio.TimeoutError, ssl.SSLError, IncompleteDownloadError)):
		return True
	if isinstance(exc, aiohttp.ClientResponseError):
		return exc.status == 408 or exc.status == 429 or exc.status >= 500
	return False


async def retry_async(action, *, label: str, attempts: int = _DOWNLOAD_RETRY_ATTEMPTS):
	for attempt in range(1, attempts + 1):
		try:
			return await action()
		except Exception as exc:
			if attempt >= attempts or not _is_retriable_download_error(exc):
				raise
			wait_seconds = min(2 ** (attempt - 1), 4)
			print(f"{label}失败，正在重试（第 {attempt + 1} 次，共 {attempts} 次）：{exc}", file=sys.stderr)
			await asyncio.sleep(wait_seconds)


def _validate_download_size(label: str, actual_size: int, expected_size: Optional[int]):
	if expected_size is not None and actual_size != expected_size:
		raise IncompleteDownloadError(
			f"{label} 下载不完整，预期 {expected_size} 字节，实际 {actual_size} 字节。"
		)


def write_bytes_atomic(path: Path, data: bytes):
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_name(path.name + ".part")
	temp.unlink(missing_ok=True)

	try:
		temp.write_bytes(data)
		temp.replace(path)
	except Exception:
		temp.unlink(missing_ok=True)
		raise

	return path


def write_text_atomic(path: Path, text: str, encoding="utf-8"):
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_name(path.name + ".part")
	temp.unlink(missing_ok=True)

	try:
		temp.write_text(text, encoding=encoding)
		temp.replace(path)
	except Exception:
		temp.unlink(missing_ok=True)
		raise

	return path


async def download_bytes(client: aiohttp.ClientSession, url, *, headers=None, params=None, label: str | None = None):
	text = label or str(url)

	async def _once():
		async with client.get(url, headers=headers, params=params) as response:
			body = await response.read()
			_validate_download_size(text, len(body), response.content_length)
			return body

	return await retry_async(_once, label=text)


async def download_to_path(client: aiohttp.ClientSession, path: Path, url, *, headers=None, params=None, label: str | None = None):
	path = Path(path)
	text = label or str(path)

	async def _once():
		path.parent.mkdir(parents=True, exist_ok=True)
		temp = path.with_name(path.name + ".part")
		temp.unlink(missing_ok=True)
		size = 0

		try:
			async with client.get(url, headers=headers, params=params) as response:
				with temp.open("wb") as fp:
					async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
						fp.write(chunk)
						size += len(chunk)
				_validate_download_size(text, size, response.content_length)
				temp.replace(path)
		except Exception:
			temp.unlink(missing_ok=True)
			raise

		return path

	return await retry_async(_once, label=text)


def tqdme(*args, **kwargs):
	"""
	enumerate + tqdm，顺便设置了一些参数的默认值。
	"""
	kwargs.setdefault("file", sys.stdout)
	kwargs.setdefault("unit", "张")
	return enumerate(tqdm(*args, **kwargs))


def pkcs7_unpad(data: bytes):
	return data[:-data[-1]]


def pkcs7_pad(data: bytes):
	size = 16 - len(data) % 16
	return data + size.to_bytes(1) * size


_illegal_path_chars = re.compile(r'[<>:"/\\?*|]')


def _to_full_width(match: re.Match[str]):
	# 一堆 if 用时 2.54s，跟从 dict 取用时 2.23s 差不多。
	char = match[0]
	if char == ":": return "："
	if char == "*": return "＊"
	if char == "?": return "？"
	if char == '"': return "'"
	if char == '|': return "｜"
	if char == '<': return "＜"
	if char == '>': return "＞"
	if char == "/": return "／"
	if char == "\\": return "＼"


def pathify(text: str):
	"""
	为了易读，使用影像的显示名作为目录名，但它可以有任意字符，而某些是文件名不允许的。
	这里把非法符号替换为 Unicode 的宽字符，虽然有点别扭但并不损失易读性。
	"""
	return _illegal_path_chars.sub(_to_full_width, text.strip())


TIME_SEPS = re.compile(r"[-: ]")


def suggest_save_dir(patient: str, desc: str, datetime: str):
	"""
	统一的函数用来确定影像的保存位置，名字一律为：[患者]-[检查]-[时间]
	患者姓名可能有星号代替，所以也需要 `pathify` 一下。

	:param patient: 患者名字
	:param desc: 检查项目
	:param datetime: 检查时间，尽量包含从年份到秒
	"""
	patient, desc = pathify(patient), pathify(desc)
	datetime = TIME_SEPS.sub("", datetime)
	return get_download_root() / f"{patient}-{desc}-{datetime}"


def get_download_root():
	value = os.environ.get(DOWNLOAD_ROOT_ENV)
	if value:
		return Path(value).expanduser()
	return Path("download")


_filename_serial_re = re.compile(r"^(.+?) \((\d+)\)$")


def make_unique_dir(path: Path):
	"""
	创建一个新的文件夹，如果指定的名字已存在则在后面添加数字使其唯一。
	实际中发现一些序列的名字相同，使用此方法可确保不覆盖。

	:param path: 原始路径
	:return: 新建的文件夹的路径，可能不等于原始路径
	"""
	try:
		path.mkdir(parents=True, exist_ok=False)
		return path
	except OSError:
		if not path.is_dir():
			raise
		matches = _filename_serial_re.match(path.name)
		if matches:
			n = int(matches.group(2)) + 1
			alt = f"{matches.group(1)} ({n})"
		else:
			alt = f"{path.name} (1)"

		return make_unique_dir(path.parent / alt)


class SeriesDirectory:
	"""
	封装了创建序列文件夹，以及生成影像文件名的操作，并做了一些特殊处理：

	- 目录名格式为：序号_描述，如有缺则单用一个，都没就叫 Unnamed。
	- 防止序列目录名重复，自动添加编号后缀。
	- 直到获取文件名准备写入时才创建目录，避免空文件夹。
	- 映像文件名填充 0 前缀，确保列出文件操作能返回正确的顺序。

	影像文件的序号从 1 开始，符合一般人的习惯，其他地方仍然以 0 为起点。
	"""

	def __init__(self, study_dir: Path, number: Optional[int], desc: str, size: int, unique=True):
		if desc and number is None:
			self._suggested = study_dir / pathify(desc)
		elif desc:
			self._suggested = study_dir / F"[{number}] {pathify(desc)}"
		elif number is None:
			self._suggested = study_dir / "Unnamed"
		else:
			self._suggested = study_dir / str(number)

		self._unique = unique
		self._size = size
		self._path = None
		self._width = int(math.log10(size)) + 2
		self._completed = set()
		self._skipped = set()

	def make_dir(self):
		if self._unique:
			self._path = make_unique_dir(self._suggested)
		else:
			self._path = self._suggested
			self._path.mkdir(parents=True, exist_ok=True)

	def get(self, index: int, extension: str) -> Path:
		"""
		获取指定次序图片的文件名，并自动创建父目录。

		之所以使用该方法，是因为文件系统遍历目录的顺序是不确定的，最常见的情况就是按照字符顺序，
		以至于出现 "2.dcm" > "12.dcm"，而该方法会在前面填 "0" 来避免该情况。

		:param index: 图像的次序
		:param extension: 文件扩展名
		:return: 文件路径，一般接下来就是写入文件。
		"""
		if not self._path:
			self.make_dir()
		base = f"{index + 1}.{extension}"
		width = self._width + len(extension)
		return self._path / base.zfill(width)

	def write_bytes(self, index: int, extension: str, data: bytes):
		path = self.get(index, extension)
		write_bytes_atomic(path, data)
		self.mark_complete(index)
		return path

	def write_text(self, index: int, extension: str, text: str, encoding="utf-8"):
		path = self.get(index, extension)
		write_text_atomic(path, text, encoding=encoding)
		self.mark_complete(index)
		return path

	async def download(self, client: aiohttp.ClientSession, index: int, extension: str, url, *, headers=None, params=None, label: str | None = None):
		path = self.get(index, extension)
		await download_to_path(client, path, url, headers=headers, params=params, label=label)
		self.mark_complete(index)
		return path

	def mark_complete(self, index: int):
		self._completed.add(index)

	def skip(self, index: int):
		self._skipped.add(index)

	def ensure_complete(self):
		missing = [i + 1 for i in range(self._size) if i not in self._completed and i not in self._skipped]
		if not missing:
			return

		preview = ", ".join(str(i) for i in missing[:8])
		if len(missing) > 8:
			preview += " ..."
		target = self._path or self._suggested
		raise IncompleteDownloadError(f"{target} 下载不完整，缺少 {len(missing)} 张：{preview}")


def parse_dcm_value(value: str, vr: str):
	"""
	在 pydicom 里没找到自动转换的功能，得自己处理下类型。
	https://stackoverflow.com/a/77661160/7065321
	"""
	if vr == VR.AT:
		return Tag(value)

	if vr in STR_VR:
		cast_fn = str
	elif vr in INT_VR or vr == "US or SS":
		cast_fn = int
	elif vr in FLOAT_VR:
		cast_fn = float
	else:
		raise NotImplementedError("Unsupported VR: " + vr)

	parts = value.split("\\")
	if len(parts) == 1:
		return cast_fn(value)
	return [cast_fn(x) for x in parts]


def suggest_series_name(ds: Dataset):
	"""
	从实例的标签中获取序列名，不一定存在所以有时也得考虑从外层获取。
	"""
	if ds.SeriesDescription:
		return ds.SeriesDescription
	if ds.SeriesNumber is not None:
		return str(ds.SeriesNumber)
	if ds.SeriesInstanceUID:
		h = sha256(ds.SeriesInstanceUID)
		h = h.digest()
		return b64encode(h)[:20].decode()
