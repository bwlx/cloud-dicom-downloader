from pathlib import Path

import pytest
import ssl
from aiohttp import web, ClientConnectionError, ClientResponseError
from pytest import mark

# noinspection PyProtectedMember
from crawlers._utils import _SSL_CONTEXT, IncompleteDownloadError, download_to_path, pathify, new_http_client, make_unique_dir, retry_async, SeriesDirectory, write_bytes_atomic


@mark.parametrize('text, expected', [
	['|*?"', "｜＊？'"],
	[' p/a\\t/h ', 'p／a＼t／h'],
	['Size > 5', 'Size ＞ 5', ],
	['Size < 5', 'Size ＜ 5', ],
	['Recon 2: 5mm', 'Recon 2： 5mm'],
])
def test_pathify(text, expected):
	assert pathify(text) == expected


async def hello(_):
	return web.Response(status=500, text='Hello, world')


async def test_response_dumping():
	app = web.Application()
	app.router.add_get('/', hello)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, '127.0.0.1', 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]

	client = new_http_client()
	try:
		await client.get(f'http://127.0.0.1:{port}')
		pytest.fail()
	except ClientResponseError:
		assert Path("dump.zip").exists()
	finally:
		await runner.cleanup()
		Path("dump.zip").unlink(missing_ok=True)


async def test_new_http_client_uses_certifi_ssl_context():
	client = new_http_client()
	try:
		assert isinstance(client._connector._ssl, ssl.SSLContext)
		assert client._connector._ssl is _SSL_CONTEXT
	finally:
		await client.close()


async def test_retry_async_retries_connection_errors():
	attempts = 0

	async def flaky():
		nonlocal attempts
		attempts += 1
		if attempts == 1:
			raise ClientConnectionError("boom")
		return "ok"

	assert await retry_async(flaky, label="测试下载", attempts=2) == "ok"
	assert attempts == 2


async def test_download_to_path_writes_atomically(tmp_path):
	async def handler(_):
		return web.Response(body=b"DICM")

	app = web.Application()
	app.router.add_get("/", handler)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]
	target = tmp_path / "file.dcm"

	client = new_http_client()
	try:
		await download_to_path(client, target, f"http://127.0.0.1:{port}/", label="测试文件")
		assert target.read_bytes() == b"DICM"
		assert not target.with_name("file.dcm.part").exists()
	finally:
		await client.close()
		await runner.cleanup()


async def test_download_to_path_resume_redownloads_partial_file(tmp_path):
	body = b"DICM-DATA"
	seen_ranges = []

	async def handler(request):
		seen_ranges.append(request.headers.get("Range"))
		return web.Response(body=body)

	app = web.Application()
	app.router.add_get("/", handler)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]
	target = tmp_path / "file.dcm"
	target.with_name("file.dcm.part").write_bytes(body[:4])

	client = new_http_client()
	try:
		await download_to_path(client, target, f"http://127.0.0.1:{port}/", label="测试文件", resume=True)
		assert target.read_bytes() == body
		assert seen_ranges == [None]
		assert not target.with_name("file.dcm.part").exists()
	finally:
		await client.close()
		await runner.cleanup()


def test_write_bytes_atomic(tmp_path):
	target = tmp_path / "test.dcm"
	write_bytes_atomic(target, b"DICM")
	assert target.read_bytes() == b"DICM"
	assert not target.with_name("test.dcm.part").exists()


def test_series_directory_ensure_complete_accepts_skipped_items(tmp_path):
	study_dir = tmp_path / "study"
	directory = SeriesDirectory(study_dir, 1, "序列", 2, unique=False)
	directory.write_bytes(0, "dcm", b"1")
	directory.skip(1)
	directory.ensure_complete()


def test_series_directory_ensure_complete_raises_when_missing(tmp_path):
	study_dir = tmp_path / "study"
	directory = SeriesDirectory(study_dir, 1, "序列", 2, unique=False)
	directory.write_bytes(0, "dcm", b"1")

	with pytest.raises(IncompleteDownloadError):
		directory.ensure_complete()


async def test_series_directory_resume_reuses_existing_files(tmp_path):
	requests = 0

	async def handler(_):
		nonlocal requests
		requests += 1
		return web.Response(body=b"fresh")

	app = web.Application()
	app.router.add_get("/", handler)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]

	directory = SeriesDirectory(tmp_path / "study", 1, "序列", 1, resume=True)
	existing_path = directory.get(0, "dcm")
	existing_path.write_bytes(b"cached")

	client = new_http_client()
	try:
		result = await directory.download(client, 0, "dcm", f"http://127.0.0.1:{port}/", label="测试序列")
		assert result == existing_path
		assert existing_path.read_bytes() == b"cached"
		assert requests == 0
		assert existing_path.parent == tmp_path / "study" / "[1] 序列"
	finally:
		await client.close()
		await runner.cleanup()


def test_make_unique_dir():
	path = Path("download/__test_dir")
	created = make_unique_dir(path)
	try:
		assert created.is_dir()
		assert created == path
	finally:
		created.rmdir()


def test_make_unique_dir_2():
	already_exists = Path("download/__test_dir (1)")
	already_exists.mkdir(parents=True)
	try:
		created = make_unique_dir(already_exists)

		assert created.is_dir()
		assert created == Path("download/__test_dir (2)")
		created.rmdir()
	finally:
		already_exists.rmdir()


def test_make_unique_dir_3():
	already_exists = Path("download/5.0 x 5.0")
	already_exists.mkdir(parents=True)
	try:
		created = make_unique_dir(already_exists)

		assert created.is_dir()
		assert created == Path("download/5.0 x 5.0 (1)")
		created.rmdir()
	finally:
		already_exists.rmdir()
