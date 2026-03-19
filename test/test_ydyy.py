import pytest
from yarl import URL

from crawlers.ydyy import (
	_build_public_storage_url,
	_parse_share_link,
	_parse_study_xml,
	requires_authority_code,
)


def test_parse_share_link_from_phone_visible_url():
	address = URL(
		"https://example-ydyy.invalid:8860/M-Viewer/#/phone-visible/BUSS-001"
		"?hideQrcode=1&forward=phone-visible&shortUrl=short-001&idType=3&sign=jwt-token"
	)

	link = _parse_share_link(address)

	assert link.buss_id == "BUSS-001"
	assert link.requires_authority_code is True
	assert link.short_url == "short-001"
	assert link.id_type == "3"
	assert link.sign == "jwt-token"


def test_parse_share_link_from_direct_viewer_url():
	address = URL("https://example-ydyy.invalid:8860/M-Viewer/m/2D?tenantId=default&userId=super&checkserialnum=BUSS-002")

	link = _parse_share_link(address)

	assert link.buss_id == "BUSS-002"
	assert link.requires_authority_code is False


def test_parse_share_link_from_shortserver_url():
	address = URL("https://example-ydyy.invalid:8860/M-Viewer/shortserver/short-002")

	link = _parse_share_link(address)

	assert link.short_server_id == "short-002"
	assert link.short_url == "short-002"
	assert link.requires_authority_code is True


def test_parse_share_link_from_redirect_url():
	address = URL(
		"https://example-ydyy.invalid:8860/M-Viewer/#/redirect/BUSS-003"
		"?forward=phone-visible&shortUrl=short-003&idType=3&sign=jwt-token"
	)

	link = _parse_share_link(address)

	assert link.buss_id == "BUSS-003"
	assert link.requires_authority_code is True
	assert link.short_url == "short-003"


def test_requires_authority_code_for_phone_visible_url():
	url = (
		"https://example-ydyy.invalid:8860/M-Viewer/#/phone-visible/BUSS-003"
		"?hideQrcode=1&forward=phone-visible&shortUrl=short-003&idType=3&sign=jwt-token"
	)

	assert requires_authority_code(url)


def test_requires_authority_code_for_shortserver_url():
	assert requires_authority_code("https://example-ydyy.invalid:8860/M-Viewer/shortserver/short-004")


def test_build_public_storage_url_rewrites_internal_vna_url():
	address = URL("https://example-ydyy.invalid:8860/M-Viewer/m/2D?checkserialnum=BUSS-004")
	httpurl0 = "http://10.0.0.1:8866/vnaHttp/wado/wado.action?"

	assert _build_public_storage_url(address, httpurl0) == (
		"https://example-ydyy.invalid:8860/M-Viewer/m/vnaHttp/wado/wado.action?"
	)


def test_parse_study_xml_builds_image_urls():
	address = URL("https://example-ydyy.invalid:8860/M-Viewer/m/2D?checkserialnum=BUSS-005")
	xml_text = """
	<patient>
	  <study checkserialnum="BUSS-005" patientname="张三" patientsex="男" patientage="45"
	    patientageunit="岁" devicetypename="CT" studytime="2026-01-14 20:12:22" seriescount="1">
	    <series seriesnumber="1001" seriesdescription="薄层" imagecount="2">
	      <storage httpurl0="http://10.0.0.1:8866/vnaHttp/wado/wado.action?">
	        <im index="0" num="1" type="dicom">studyUID=STUDY-1&amp;seriesUID=SERIES-1&amp;objectUID=IMAGE-1</im>
	        <im index="1" num="2" type="dicom">studyUID=STUDY-1&amp;seriesUID=SERIES-1&amp;objectUID=IMAGE-2</im>
	      </storage>
	    </series>
	  </study>
	</patient>
	""".strip()

	study = _parse_study_xml(xml_text, address)

	assert study.patient_name == "张三"
	assert study.modality == "CT"
	assert len(study.series) == 1
	assert study.series[0].series_number == 1001
	assert study.series[0].description == "薄层"
	assert [image.instance_number for image in study.series[0].images] == [1, 2]
	assert study.series[0].images[0].url == (
		"https://example-ydyy.invalid:8860/M-Viewer/m/vnaHttp/wado/wado.action?"
		"studyUID=STUDY-1&seriesUID=SERIES-1&objectUID=IMAGE-1"
	)


def test_parse_share_link_rejects_unknown_url():
	address = URL("https://example-ydyy.invalid:8860/M-Viewer/#/unsupported/BUSS-006")

	with pytest.raises(ValueError, match="不是受支持的云影像分享链接"):
		_parse_share_link(address)
