import base64
from urllib.parse import urlencode

import pytest

from crawlers.kayicloud import _extract_params, _image_url


# 虚构的测试用 URL，不含真实患者数据或域名。
_FAKE_SERVER_ADDR_RAW = (
	"public@http://wfe.example-kayi.invalid/api/v1/imageQuery"
	"?StudyInstanceUID=&x-moon-ak=testkey001"
	"&x-moon-expires=1800&Cache=false"
	"&HospitalCode=TESTHOSP001"
	"&AccessionNo=TEST202500001"
	"&x-moon-timestamp=1000000000"
	"&x-moon-sign=fakesign001"
	"&Proxy=4&System="
)
_FAKE_SERVER_ADDR = base64.b64encode(_FAKE_SERVER_ADDR_RAW.encode()).decode()

_FAKE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dGVzdA.dGVzdA"

_FAKE_URL = (
	"https://dicomviewer.test-hospital.example-kayi.invalid/"
	"?HospitalCode=TESTHOSP001"
	"&AccessionNo=TEST202500001"
	"&StudyInstanceUID="
	f"&serverAddr={_FAKE_SERVER_ADDR}"
	"&dataid="
	f"&Token={_FAKE_TOKEN}"
	"&Anony=2"
)


def test_extract_params_query_string():
	params = _extract_params(_FAKE_URL)
	assert params["HospitalCode"] == "TESTHOSP001"
	assert params["AccessionNo"] == "TEST202500001"
	assert params["Token"] == _FAKE_TOKEN
	assert params["serverAddr"] == _FAKE_SERVER_ADDR


def test_extract_params_fragment():
	# 有些页面把参数放在 fragment（#?xxx=yyy）里。
	fragment_url = (
		"https://dicomviewer.test-hospital.example-kayi.invalid/"
		f"#?HospitalCode=TESTHOSP001&AccessionNo=TEST202500001&Token={_FAKE_TOKEN}"
	)
	params = _extract_params(fragment_url)
	assert params["HospitalCode"] == "TESTHOSP001"
	assert params["AccessionNo"] == "TEST202500001"


def test_extract_params_missing_raises():
	with pytest.raises(ValueError, match="查询参数"):
		_extract_params("https://dicomviewer.test-hospital.example-kayi.invalid/")


def test_image_url_without_image_key():
	study = {
		"HospitalCode": "TESTHOSP001",
		"AccessionNumber": "TEST202500001",
		"StudyInstanceUID": "1.2.3.4.5",
		"Token": "study-token-001",
	}
	raw_path = "/dicom/study/series/instance"
	expected_key = base64.b64encode(raw_path.encode()).decode()
	url = _image_url("https://dicomviewer.example-kayi.invalid", study, raw_path, 0)
	assert "api/v2/image" in url
	assert f"ImageKey={expected_key}" in url
	assert "Index=0" in url
	assert "Token=study-token-001" in url


def test_image_url_with_image_key_relative():
	study = {
		"HospitalCode": "TESTHOSP001",
		"AccessionNumber": "TEST202500001",
		"StudyInstanceUID": "1.2.3.4.5",
		"Token": "study-token-001",
	}
	# 相对路径已含 ImageKey（少见情况）。
	relative_url = "api/v2/image?HospitalCode=TESTHOSP001&ImageKey=encodedpath"
	url = _image_url("https://dicomviewer.example-kayi.invalid", study, relative_url, 2)
	assert url.endswith("&Index=2")
	assert url.startswith("https://dicomviewer.example-kayi.invalid/")


def test_image_url_full_url():
	study = {
		"HospitalCode": "TESTHOSP001",
		"AccessionNumber": "TEST202500001",
		"StudyInstanceUID": "1.2.3.4.5",
		"Token": "study-token-001",
	}
	# 服务端直接返回完整 URL（实际情况），不应再拼接 origin。
	full_url = (
		"https://dicomviewer.example-kayi.invalid/api/v2/image"
		"?HospitalCode=TESTHOSP001&AccessionNo=TEST202500001"
		"&ImageKey=encodedpath&s3Url=true"
	)
	url = _image_url("https://dicomviewer.example-kayi.invalid", study, full_url, 0)
	assert url == full_url + "&Index=0"
	# 不能出现重复的域名前缀。
	assert url.count("https://dicomviewer.example-kayi.invalid") == 1
