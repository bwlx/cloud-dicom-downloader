import base64
import json

import pytest
from aiohttp import web
from Cryptodome.Cipher import AES
from yarl import URL

from crawlers.wegopoly import (
	_AES_IV,
	_AES_KEY,
	ShareAccess,
	_decrypt_query,
	_download_dicom,
	_download_prefix,
	_image_url,
	_parse_share_link,
)
from crawlers._utils import new_http_client


def _encrypt_query(payload: dict) -> str:
	cipher = AES.new(_AES_KEY, AES.MODE_CTR, nonce=b"", initial_value=_AES_IV)
	raw = cipher.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
	return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_decrypt_query():
	token = _encrypt_query({"hid": 124, "studyIndex": "STUDY-001", "acc": "ACCESS-TOKEN"})
	assert _decrypt_query(token) == {
		"hid": 124,
		"studyIndex": "STUDY-001",
		"acc": "ACCESS-TOKEN",
	}


def test_parse_share_link_with_encrypted_query():
	token = _encrypt_query({"hid": 124, "studyIndex": "STUDY-001", "acc": "ACCESS-TOKEN"})
	assert _parse_share_link(URL(f"https://cfsaas.wegopoly.com/image/?q={token}")) == ShareAccess(
		hid="124",
		study_index="STUDY-001",
		verify_token="ACCESS-TOKEN",
	)


def test_parse_share_link_rejects_missing_params():
	with pytest.raises(ValueError, match="hid / studyIndex / acc"):
		_parse_share_link(URL("https://cfsaas.wegopoly.com/image/?hid=124"))


def test_download_prefix_matches_frontend_join_rule():
	assert _download_prefix({
		"serverhost": "https://whimg.obs.wegopoly.com/dcm-bucket",
		"dicomfile": "dicomfile",
		"relativeDir": "Hospital124/MR/202605/29/STUDY-001",
	}) == "https://whimg.obs.wegopoly.com/dcm-bucket/dicomfile/Hospital124/MR/202605/29/STUDY-001"


def test_image_url_uses_image_id_under_prefix():
	assert _image_url("https://example.invalid/base", {"imageId": "1.2.3.dcm"}) == "https://example.invalid/base/1.2.3.dcm"


async def test_download_dicom_rejects_404(tmp_path):
	async def handler(_):
		return web.Response(status=404, text="not found")

	app = web.Application()
	app.router.add_get("/missing.dcm", handler)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]

	client = new_http_client(raise_for_status=False)
	try:
		with pytest.raises(ValueError, match="HTTP 404"):
			await _download_dicom(client, tmp_path / "missing.dcm", f"http://127.0.0.1:{port}/missing.dcm", label="测试影像")
		assert not (tmp_path / "missing.dcm").exists()
	finally:
		await client.close()
		await runner.cleanup()


async def test_download_dicom_rejects_html(tmp_path):
	async def handler(_):
		return web.Response(body=b"<!doctype html><html></html>")

	app = web.Application()
	app.router.add_get("/index.html", handler)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]

	client = new_http_client(raise_for_status=False)
	try:
		with pytest.raises(ValueError, match="不是有效 DICOM"):
			await _download_dicom(client, tmp_path / "bad.dcm", f"http://127.0.0.1:{port}/index.html", label="测试影像")
		assert not (tmp_path / "bad.dcm").exists()
	finally:
		await client.close()
		await runner.cleanup()
