import pytest
from yarl import URL

from crawlers.zy91 import (
	_iter_viewer_definitions,
	_parse_kv_literal,
	_parse_share_link,
	_parse_viewer_page,
	_rewrite_image_url,
)


def test_parse_share_link_extracts_hash_query():
	share = _parse_share_link(
		"https://yyx.zy91.com:6443/PC/#/share_report?tel=&pid=patient-001&sid=accession-001&Expires=1700000000&Signature=signature-001"
	)

	assert share.patients_id == "patient-001"
	assert share.accession_number == "accession-001"
	assert share.expires == "1700000000"
	assert share.signature == "signature-001"


def test_parse_share_link_rejects_other_host():
	with pytest.raises(ValueError, match="不是受支持"):
		_parse_share_link("https://example-hospital.invalid/PC/#/share_report?pid=p&sid=s&Expires=1&Signature=x")


def test_parse_share_link_requires_all_params():
	with pytest.raises(ValueError, match="缺少必要"):
		_parse_share_link("https://yyx.zy91.com:6443/PC/#/share_report?pid=patient-001")


def test_rewrite_image_url_replaces_localhost_origin():
	url = _rewrite_image_url(
		"http://localhost:1000/api/Wado/ImageReader?FN=ABCDEF",
		URL("https://yyx.zy91.com:5443"),
	)
	assert url == "https://yyx.zy91.com:5443/api/Wado/ImageReader?FN=ABCDEF"


def test_rewrite_image_url_replaces_loopback_ip():
	url = _rewrite_image_url(
		"http://127.0.0.1:1000/api/Wado/ImageReader?FN=DEADBEEF",
		URL("https://yyx.zy91.com:5443"),
	)
	assert url == "https://yyx.zy91.com:5443/api/Wado/ImageReader?FN=DEADBEEF"


def test_rewrite_image_url_keeps_external_origin():
	original = "https://images.example-hospital.invalid/api/Wado/ImageReader?FN=ABC"
	assert _rewrite_image_url(original, URL("https://yyx.zy91.com:5443")) == original


def test_parse_kv_literal_extracts_key_value_pairs():
	raw = "'{ \"sopInstUID\":\"uid-001\", \"imageURL\":\"http://localhost/x\", \"rows\":\"512\" }'"
	fields = _parse_kv_literal(raw)
	assert fields["sopInstUID"] == "uid-001"
	assert fields["imageURL"] == "http://localhost/x"
	assert fields["rows"] == "512"


def _series_literal(name: str, uid: str, modality: str, body: str, number: str):
	return (
		f"var {name} = '{{ \"patIndex\":' + studyObj.patIndex + ', \"studyIndex\":' + studyObj.studyIndex + "
		f"', \"serInstUID\":\"{uid}\", \"modality\":\"{modality}\", \"bodyPart\":\"{body}\", "
		f"\"serNumber\":\"{number}\" }}';\n"
	)


def _image_literal(name: str, sop_uid: str, image_url: str, instance_number: int):
	return (
		f"var {name} = '{{ \"patIndex\":' + serObj0.patIndex + ', \"studyIndex\":' + serObj0.studyIndex + "
		f"', \"serIndex\":' + serObj0.serIndex + ', \"sopInstUID\":\"{sop_uid}\", "
		f"\"sopClassUID\":\"1.2.840.10008.5.1.4.1.1.4\", \"imageURL\":\"{image_url}\", "
		f"\"imageNumber\":\"{instance_number}\", \"rows\":\"512\", \"cols\":\"512\" }}';\n"
	)


def test_iter_viewer_definitions_preserves_call_order():
	html = (
		"var patientInfo = '{ \"patName\":\"Anon\" }';\n"
		+ _series_literal("serInfo0", "series-uid-1", "MR", "PELVIS", "201")
		+ _image_literal("imgInfo00", "image-uid-1", "http://localhost:1000/api/Wado/ImageReader?FN=AAA", 1)
		+ "WV_AddPatientInfo(patientInfo);\n"
		"WV_AddSeriesInfo(serInfo0);\n"
		"WV_AddImageInfo(imgInfo00);\n"
	)

	order = [(kind, name) for kind, name, _ in _iter_viewer_definitions(html)]
	assert order == [
		("PatientInfo", "patientInfo"),
		("SeriesInfo", "serInfo0"),
		("ImageInfo", "imgInfo00"),
	]


def test_parse_viewer_page_groups_images_into_series():
	html = (
		_series_literal("serInfo0", "series-uid-1", "MR", "PELVIS", "201")
		+ _series_literal("serInfo1", "series-uid-2", "MR", "PELVIS", "301")
		+ _image_literal("imgInfo00", "image-uid-1", "http://localhost:1000/api/Wado/ImageReader?FN=AAA", 2)
		+ _image_literal("imgInfo01", "image-uid-2", "http://localhost:1000/api/Wado/ImageReader?FN=BBB", 1)
		+ _image_literal("imgInfo10", "image-uid-3", "http://localhost:1000/api/Wado/ImageReader?FN=CCC", 1)
		+ "WV_AddSeriesInfo(serInfo0);\n"
		"WV_AddImageInfo(imgInfo00);\n"
		"WV_AddImageInfo(imgInfo01);\n"
		"WV_AddSeriesInfo(serInfo1);\n"
		"WV_AddImageInfo(imgInfo10);\n"
	)

	series = _parse_viewer_page(html, URL("https://yyx.zy91.com:5443"))

	assert [s.series_instance_uid for s in series] == ["series-uid-1", "series-uid-2"]
	assert series[0].series_number == 201
	assert series[1].series_number == 301
	assert [(i.sop_instance_uid, i.image_url) for i in series[0].images] == [
		("image-uid-1", "https://yyx.zy91.com:5443/api/Wado/ImageReader?FN=AAA"),
		("image-uid-2", "https://yyx.zy91.com:5443/api/Wado/ImageReader?FN=BBB"),
	]
	assert series[1].images[0].sop_instance_uid == "image-uid-3"


def test_parse_viewer_page_skips_images_with_blank_url():
	html = (
		_series_literal("serInfo0", "series-uid-1", "MR", "PELVIS", "201")
		+ _image_literal("imgInfo00", "image-uid-1", "", 1)
		+ _image_literal("imgInfo01", "image-uid-2", "http://localhost:1000/api/Wado/ImageReader?FN=BBB", 2)
		+ "WV_AddSeriesInfo(serInfo0);\n"
		"WV_AddImageInfo(imgInfo00);\n"
		"WV_AddImageInfo(imgInfo01);\n"
	)

	series = _parse_viewer_page(html, URL("https://yyx.zy91.com:5443"))
	assert len(series) == 1
	assert [i.sop_instance_uid for i in series[0].images] == ["image-uid-2"]
