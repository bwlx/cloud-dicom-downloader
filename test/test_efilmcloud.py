from yarl import URL

from crawlers.efilmcloud import (
	ShortLink,
	StudyBaseInfo,
	_extract_dicom_viewer_url,
	_image_sort_key,
	_parse_short_link,
	_parse_study_base_info,
	_parse_viewer_access,
	_series_number,
	_series_sort_key,
	_study_datetime,
)


def test_parse_short_link():
	address = URL("https://example.efilmcloud.com:8810/SHORT-001")
	assert _parse_short_link(address) == ShortLink(short_url="SHORT-001")


def test_parse_short_link_rejects_nested_path():
	try:
		_parse_short_link(URL("https://example.efilmcloud.com:8810/pages/reportmian/index"))
	except ValueError as exc:
		assert "富医睿影" in str(exc)
	else:
		raise AssertionError("expected ValueError")


def test_parse_viewer_access():
	viewer = _parse_viewer_access(
		"https://example.efilmcloud.com:8803/?"
		"token=viewer-token&webApiUrl=https%3A%2F%2Fexample.efilmcloud.com%3A8801"
		"&hID=1001&source=103",
		fallback_accession_number="ACC-001",
	)
	assert viewer.token == "viewer-token"
	assert viewer.web_api_url == "https://example.efilmcloud.com:8801"
	assert viewer.hospital_id == "1001"
	assert viewer.source == "103"
	assert viewer.accession_number == "ACC-001"


def test_parse_study_base_info():
	payload = {
		"code": 200,
		"data": {
			"token": "api-token",
			"studyBaseInfo": {
				"hospitalID": 1001,
				"ssystemID": 90,
				"patientID": "PAT-001",
				"accNum": "ACC-001",
				"studyKey": 2002,
			},
		},
	}
	assert _parse_study_base_info(payload) == (
		"api-token",
		StudyBaseInfo(
			hospital_id=1001,
			ssystem_id=90,
			patient_id="PAT-001",
			accession_number="ACC-001",
			study_key=2002,
		),
	)


def test_extract_dicom_viewer_url():
	payload = {
		"code": 200,
		"data": {
			"dicom": {
				"dicomMedicaldocumentInfos": [
					{"url": "https://example.efilmcloud.com:8803/?token=viewer-token"}
				]
			}
		},
	}
	assert _extract_dicom_viewer_url(payload) == "https://example.efilmcloud.com:8803/?token=viewer-token"


def test_extract_dicom_viewer_url_rejects_missing_dicom():
	try:
		_extract_dicom_viewer_url({"code": 200, "data": {"dicom": {"dicomMedicaldocumentInfos": []}}})
	except ValueError as exc:
		assert "DICOM" in str(exc)
	else:
		raise AssertionError("expected ValueError")


def test_sort_helpers():
	assert _series_sort_key({"seriesId": "12", "seriesDesc": "B", "seriesUid": "UID"})[0] == 12
	assert _series_number({"seriesId": "12"}) == 12
	assert _series_number({"seriesId": ""}) is None
	assert _image_sort_key({"instanceNumber": "9", "objestInstanceUid": "SOP"})[0] == 9


def test_study_datetime_fallbacks():
	assert _study_datetime({"studyDate": "2026-04-15"}) == "2026-04-15"
	assert _study_datetime({"series": [{"seriesTime": "2026-04-14"}], "accessionNumber": "ACC-001"}) == "2026-04-14"
	assert _study_datetime({"accessionNumber": "ACC-001"}) == "ACC-001"
	assert _study_datetime({}) == "study"
