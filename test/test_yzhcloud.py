import pytest
from yarl import URL

from crawlers.yzhcloud import (
	_is_pocketfilm_url,
	_parse_patient_name,
	_parse_viewer_link,
	_resolve_direct,
)


def test_is_pocketfilm_url_matches_qrcode_landing():
	url = URL("https://example-hospital.invalid/pocketfilm/index.php?m=home&c=index&a=itemdetails_qrcode&token=abc")
	assert _is_pocketfilm_url(url) is True


def test_is_pocketfilm_url_matches_plain_itemdetails():
	url = URL("https://example-hospital.invalid/pocketfilm/index.php?a=itemdetails&d=encoded")
	assert _is_pocketfilm_url(url) is True


def test_is_pocketfilm_url_rejects_other_paths():
	url = URL("https://example-hospital.invalid/?study_instance_uid=STUDY-001&org_id=719")
	assert _is_pocketfilm_url(url) is False


def test_is_pocketfilm_url_rejects_unknown_action():
	url = URL("https://example-hospital.invalid/pocketfilm/index.php?a=login")
	assert _is_pocketfilm_url(url) is False


def test_resolve_direct_extracts_study_and_org():
	access = _resolve_direct(URL("https://example-hospital.invalid/?study_instance_uid=STUDY-001&org_id=719"))
	assert access.study_uid == "STUDY-001"
	assert access.org_id == "719"
	assert access.patient_name_hint is None
	assert "w_viewer_2" in access.api_path


def test_resolve_direct_rejects_missing_params():
	with pytest.raises(ValueError, match="医众"):
		_resolve_direct(URL("https://example-hospital.invalid/?study_instance_uid=STUDY-001"))


def test_parse_viewer_link_decodes_html_entities_and_relative_paths():
	html = '<a class="button" href="/dicom_2020/?&amp;org_id=719&amp;f=dz&amp;ak=TOKEN-001">影像浏览</a>'
	link = _parse_viewer_link(html, URL("https://example-hospital.invalid"))
	assert str(link).startswith("https://example-hospital.invalid/dicom_2020/")
	assert link.query["org_id"] == "719"
	assert link.query["ak"] == "TOKEN-001"


def test_parse_viewer_link_accepts_absolute_url():
	html = '<a href="https://example-hospital.invalid/dicom_2020/?&org_id=719&f=dz&ak=TOKEN">go</a>'
	link = _parse_viewer_link(html, URL("https://example-hospital.invalid"))
	assert link.host == "example-hospital.invalid"
	assert link.query["org_id"] == "719"


def test_parse_viewer_link_raises_when_missing():
	with pytest.raises(ValueError, match="查看器"):
		_parse_viewer_link("<html><body>no link here</body></html>", URL("https://example-hospital.invalid"))


def test_parse_patient_name_extracts_name_from_title():
	assert _parse_patient_name("<title>张三/男/68岁的影像</title>") == "张三"
	assert _parse_patient_name("<title>李四 王/女/30岁的影像</title>") == "李四 王"


def test_parse_patient_name_returns_none_when_no_match():
	assert _parse_patient_name("<title>无内容</title>") is None
	assert _parse_patient_name("") is None
