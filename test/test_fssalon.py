from yarl import URL

from crawlers.fssalon import (
	ShareLink,
	StudyInfo,
	_normalized_datetime,
	_parse_report_detail,
	_parse_share_link,
	_series_identity,
	_wado_params,
)


def test_parse_report_share_link():
	address = URL("https://efilm.fs-salon.cn/index?barcode=BARCODE-001&hospitalcode=HOSPITAL-001")
	assert _parse_share_link(address) == ShareLink(
		report_no="BARCODE-001",
		hospital_code="HOSPITAL-001",
	)


def test_parse_cloudfilm_share_link():
	address = URL("https://efilm.fs-salon.cn/cloudFilm?collectfilmid=0&showAi=false&hospitalCode=HOSPITAL-002&reportid=REPORT-002")
	assert _parse_share_link(address) == ShareLink(
		report_no="REPORT-002",
		hospital_code="HOSPITAL-002",
	)


def test_parse_report_detail():
	payload = {
		"statusCode": 200,
		"message": None,
		"result": {
			"hasCloudFilm": True,
			"imServerUrl": "http://example.invalid/Wado",
			"studyUId": "study-uid-001",
			"orgCode": "hospital-001",
			"departCode": "3",
			"name": "张三",
			"checkPart": "胸部CT增强",
			"checkDate": "2026-04-12",
			"reportNo": "REPORT-001",
		},
	}
	assert _parse_report_detail(payload) == StudyInfo(
		patient_name="张三",
		study_label="胸部CT增强",
		datetime_key="20260412",
		wado_url="http://example.invalid/Wado",
		study_uid="study-uid-001",
		hospital_code="hospital-001",
		depart_code="3",
	)


def test_parse_report_detail_rejects_missing_cloudfilm():
	payload = {"statusCode": 200, "result": {"hasCloudFilm": False}, "message": None}
	try:
		_parse_report_detail(payload)
	except ValueError as exc:
		assert "原始影像" in str(exc)
	else:
		raise AssertionError("expected ValueError")


def test_series_identity_prefers_uuid():
	series = {"uuid": "UUID-001", "uid": "UID-001"}
	assert _series_identity(series) == "UUID-001"
	assert _series_identity({"uid": "UID-002"}) == "UID-002"


def test_wado_params_for_dicom():
	study = StudyInfo(
		patient_name="张三",
		study_label="检查",
		datetime_key="20260412",
		wado_url="http://example.invalid/Wado",
		study_uid="study-uid-001",
		hospital_code="hospital-001",
		depart_code="5",
	)
	assert _wado_params(study) == {
		"hospID": "hospital-001",
		"hospid": "hospital-001",
		"hospId": "hospital-001",
		"hospitalid": "hospital-001",
		"imageType": "1",
		"isDcm": "1",
		"studyUID": "study-uid-001",
		"studyuid": "study-uid-001",
		"departCode": "5",
		"hasDesensitize": "0",
		"isInternal": "0",
		"isKeyImage": "0",
	}


def test_normalized_datetime_falls_back_to_report_number():
	detail = {"checkDate": "", "studyDate": None, "reportTime": "", "studyUId": "", "reportNo": "REPORT-001"}
	assert _normalized_datetime(detail) == "001"
