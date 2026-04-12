import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterator

from yarl import URL

from crawlers import cq12320, fssalon, ftimage, hinacom, jdyfy, medapi, mtywcloud, neusoft, radonline, rjh, shdc, sugh, szjudianyun, wlycloud, ydyy, yzhcloud, zscloud, wehzsy
from runtime_config import DOWNLOAD_ROOT_ENV


@dataclass(slots=True)
class DownloadRequest:
	url: str
	password: str | None = None
	raw: bool = False
	output_dir: str | None = None


def resolve_crawler_module(url: str) -> ModuleType:
	host = URL(url).host or ""

	if host.endswith(".medicalimagecloud.com"):
		return hinacom
	if host == "mdmis.cq12320.cn":
		return cq12320
	if host == "qr.szjudianyun.com":
		return szjudianyun
	if host == "ylyyx.shdc.org.cn":
		return shdc
	if host == "efilm.fs-salon.cn":
		return fssalon
	if host == "zscloud.zs-hospital.sh.cn":
		return zscloud
	if host in {"app.ftimage.cn", "yyx.ftimage.cn"}:
		return ftimage
	if host == "m.yzhcloud.com":
		return yzhcloud
	if host == "ss.mtywcloud.com":
		return mtywcloud
	if host == "work.sugh.net":
		return sugh
	if host in {"cloudpacs.jdyfy.com", "cyemis.bjcyh.mobi"}:
		return jdyfy
	if host == "medapi.dsrmyy.cn":
		return medapi
	if host == "cloud.wehzsy.com":
		return wehzsy
	if host == "pacs.ydyy.cn":
		return ydyy
	if host == "202.100.221.200":
		return neusoft
	if host in {"cinv.wlycloud.com", "rend.wlycloud.com"}:
		return wlycloud
	if host == "film.radonline.cn":
		return radonline
	if host == "lk-pacsview.rjh.com.cn":
		return rjh

	raise ValueError("不支持的网站，详情见 README.md")


def url_requires_password(url: str) -> bool:
	host = URL(url).host or ""
	return host.endswith(".medicalimagecloud.com") or jdyfy.requires_authority_code(url) or ydyy.requires_authority_code(url)


def url_password_prompt(url: str) -> str | None:
	host = URL(url).host or ""
	if host.endswith(".medicalimagecloud.com"):
		return "访问密码"
	return jdyfy.authority_code_prompt(url) or ydyy.authority_code_prompt(url)


def url_supports_raw(url: str) -> bool:
	host = URL(url).host or ""
	return host.endswith(".medicalimagecloud.com") or host == "mdmis.cq12320.cn"


@contextmanager
def configured_output_dir(output_dir: str | None) -> Iterator[None]:
	if not output_dir:
		yield
		return

	previous = os.environ.get(DOWNLOAD_ROOT_ENV)
	os.environ[DOWNLOAD_ROOT_ENV] = str(Path(output_dir).expanduser())
	try:
		yield
	finally:
		if previous is None:
			os.environ.pop(DOWNLOAD_ROOT_ENV, None)
		else:
			os.environ[DOWNLOAD_ROOT_ENV] = previous


async def run_download_request(request: DownloadRequest):
	module_ = resolve_crawler_module(request.url)
	args = [request.url]

	if url_requires_password(request.url):
		if not request.password:
			raise ValueError(f"该链接需要填写{url_password_prompt(request.url) or '访问凭证'}。")
		args.append(request.password)

	if request.raw and url_supports_raw(request.url):
		args.append("--raw")

	with configured_output_dir(request.output_dir):
		await module_.run(*args)
