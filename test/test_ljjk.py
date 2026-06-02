from io import BytesIO
from zipfile import ZipFile

import pytest
from yarl import URL

from crawlers.ljjk import ShareLink, _extract_dicom_from_zip, _parse_share_link, _parse_study


def test_parse_share_link():
	address = URL(
		"https://mic.ljjk.org.cn/NeuView/mobile/?r=123#"
		"TOKEN-001_SIGN_SIGN-001_OPEN_undefined&bType=2d&title=TITLE"
	)
	assert _parse_share_link(address) == ShareLink(
		token="TOKEN-001_SIGN_SIGN-001_OPEN_undefined",
		b_type="2d",
	)


def test_parse_study_reads_pk_images_and_instance_numbers():
	payload = {
		"code": "2000",
		"data": {
			"patientname": "匿名",
			"modality": "MR",
			"checkserialnum": "TOKEN-001",
			"series": [
				{
					"seriesnumber": 3,
					"seriesuniqueid": "SERIES-001",
					"image": [
						"PK:https://storage.invalid/study/hash-001?sign=1",
						"PK:https://storage.invalid/study/hash-002?sign=2",
					],
					"images": (
						'[{"fileHash":"hash-001","instanceNumber":2},'
						'{"fileHash":"hash-002","instanceNumber":1}]'
					),
				}
			],
		},
	}
	study = _parse_study(payload)
	assert study.patient_name == "匿名"
	assert study.series[0].number == 3
	assert [image.instance_number for image in study.series[0].images] == [2, 1]
	assert study.series[0].images[0].url == "https://storage.invalid/study/hash-001?sign=1"


def test_extract_dicom_from_zip():
	dicom = b"\0" * 128 + b"DICM" + b"DICOM-DATA"
	buffer = BytesIO()
	with ZipFile(buffer, "w") as archive:
		archive.writestr("image", dicom)

	assert _extract_dicom_from_zip(buffer.getvalue()) == dicom


def test_extract_dicom_from_zip_rejects_missing_dicom():
	buffer = BytesIO()
	with ZipFile(buffer, "w") as archive:
		archive.writestr("image", b"not dicom")

	with pytest.raises(ValueError, match="没有 DICOM"):
		_extract_dicom_from_zip(buffer.getvalue())
