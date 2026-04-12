import base64
import json

from yarl import URL

from crawlers.neusoft import (
	ShareLink,
	StudySummary,
	_decode_download_url,
	_decode_jwt_payload,
	_parse_profile_link,
	_parse_share_link,
	_save_dir,
	_summarize_download_error,
	_unsupported_message,
)


def _jwt(payload: dict) -> str:
	body = json.dumps(payload, separators=(",", ":")).encode()
	encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
	return f"header.{encoded}.signature"


def test_parse_short_share_link():
	address = URL("http://202.100.221.200:6087/short/short-001")
	assert _parse_share_link(address) == ShareLink(
		checkserialnum="",
		sign="",
		profile_url=str(address),
	)


def test_parse_profile_link():
	token = _jwt({"ext": {"checkserialnum": "CHECK-001"}, "exp": 1900000000})
	address = URL(f"http://202.100.221.200:6087/M-Viewer/#/profile/CHECK-001?sign={token}")
	assert _parse_profile_link(address) == ShareLink(
		checkserialnum="CHECK-001",
		sign=token,
		profile_url=str(address),
	)


def test_decode_jwt_payload():
	token = _jwt({"ext": {"checkserialnum": "CHECK-002"}, "exp": 1900000001})
	assert _decode_jwt_payload(token)["ext"]["checkserialnum"] == "CHECK-002"


def test_decode_download_url():
	value = base64.b64encode(b"http://example.invalid/files/study.zip").decode()
	assert _decode_download_url(value) == "http://example.invalid/files/study.zip"


def test_save_dir_uses_study_summary():
	summary = StudySummary(
		patient_name="Test Patient",
		check_item="Chest CT",
		study_date="2026-03-18 17:14:47.0",
		device_type="CT",
	)
	assert "Test Patient-Chest CT-20260318171447.0" == _save_dir(summary).name


def test_unsupported_message_mentions_site_download_flag():
	message = _unsupported_message({"download": False}, "HTTP 500")
	assert "没有开放可用的影像下载能力" in message
	assert "服务器错误" in message


def test_summarize_database_error():
	detail = "HTTP 500: ORA-00942: 表或视图不存在 WEBRIS_DICOM_DOWNLOAD_RECORD"
	assert "数据库表缺失" in _summarize_download_error(detail)
