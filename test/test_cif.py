from yarl import URL
from pydicom import dcmread

from crawlers.cif import (
	CifAccess,
	CifLink,
	_image_entries,
	_parse_cif_access,
	_parse_cif_link,
	_person_name,
	_write_dicom,
	authority_code_prompt,
	requires_authority_code,
)


def test_parse_cif_link():
	address = URL("http://ge.jstumor.jszlyy.com.cn:8080/CIF/user/loginAccCode?urlParam=URL-PARAM-001")
	assert _parse_cif_link(address) == CifLink(url_param="URL-PARAM-001")
	assert requires_authority_code(str(address))
	assert authority_code_prompt(str(address)) == "访问码"


def test_parse_cif_access():
	payload = {
		"status": 200,
		"data": "accessCode=1234&patientId=PAT-001&examId=EXAM-001&orderId=ORDER-001",
	}
	assert _parse_cif_access("URL-PARAM-001", payload) == CifAccess(
		url_param="URL-PARAM-001",
		access_code="1234",
		patient_id="PAT-001",
		order_id="ORDER-001",
		exam_id="EXAM-001",
	)


def test_person_name_prefers_dicom_person_string():
	assert _person_name({"PersonNameString": "ZHANG SAN=张三=ZHANG SAN"}) == "ZHANG SAN=张三=ZHANG SAN"
	assert _person_name({"Ideographic": {"Family": "张三"}}) == "张三"


def test_image_entries_expand_multiframe():
	study = {
		"Series": [
			{
				"ImageCount": 1,
				"Sops": [
					{"SopInstanceUid": "1.2.3", "NumberOfFrames": 2},
				],
			}
		]
	}
	assert [entry.token for entry in _image_entries(study)] == ["1.2.3#0", "1.2.3#1"]


def test_write_dicom_from_zfp_raw_pixels(tmp_path):
	study = {
		"StudyInstanceUid": "1.2.826.0.1.3680043.8.498.1",
		"PatientName": {"PersonNameString": "ZHANG SAN=张三=ZHANG SAN"},
		"PatientId": "PAT-001",
		"PatientSex": "M",
		"PatientBirthDate": "1990-01-02T00:00:00",
		"AccessionNumber": "ACC-001",
		"StudyDate": "2026-05-21T10:00:00",
		"StudyTime": "100000.000",
		"StudyDescription": "胸部CT",
	}
	series = {
		"SeriesInstanceUid": "1.2.826.0.1.3680043.8.498.2",
		"SeriesModality": "CT",
		"SeriesNumber": "3",
		"SeriesDescription": "AXIAL",
	}
	sop = {
		"SopClassUid": "1.2.840.10008.5.1.4.1.1.2",
		"SopInstanceUid": "1.2.826.0.1.3680043.8.498.3",
		"ImageNumber": "7",
	}
	header = {
		"Rows": "2",
		"Columns": "2",
		"SamplesPerPixel": "1",
		"PhotometricInterpretation": "MONOCHROME2",
		"BitsAllocated": "16",
		"BitsStored": "12",
		"HighBit": "11",
		"PixelRepresentation": "0",
		"RescaleIntercept": "-1024",
		"RescaleSlope": "1",
		"PixelSpacing": "0.5\\0.5",
		"ImageOrientation": "1\\0\\0\\0\\1\\0",
		"ImagePosition": "0\\0\\0",
		"ImageNumber": "7",
	}
	target = tmp_path / "image.dcm"
	_write_dicom(study, series, sop, header, b"\x01\x00\x02\x00\x03\x00\x04\x00", target)

	ds = dcmread(target)
	assert ds.PatientName == "ZHANG SAN=张三=ZHANG SAN"
	assert ds.Rows == 2
	assert ds.Columns == 2
	assert ds.InstanceNumber == 7
	assert ds.PixelData == b"\x01\x00\x02\x00\x03\x00\x04\x00"
