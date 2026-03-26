from io import BytesIO

from pydicom import dcmread
from pydicom.uid import CTImageStorage
from yarl import URL

from crawlers.wlycloud import (
	ShareLink,
	ViewerImage,
	ViewerSeries,
	ViewerStudy,
	_build_dicom,
	_format_dicom_time,
	_parse_share_link,
	_resolve_viewer_url,
)


def test_parse_share_link_from_fragment_query():
	link = _parse_share_link(URL("https://cinv.wlycloud.com/#/?uid=share-uid-001&facId=1001&time=1700000000000"))
	assert link == ShareLink(uid="share-uid-001")


def test_resolve_viewer_url_from_report_payload():
	payload = {
		"code": 0,
		"val": {
			"imgPath": "http://rend.wlycloud.com/api/preDispRender?studyid=study-001&sign=test-sign",
		},
	}
	address = URL("https://cinv.wlycloud.com/#/?uid=share-uid-001")
	assert _resolve_viewer_url(payload, address) == payload["val"]["imgPath"]


def test_build_dicom_from_viewer_metadata():
	study = ViewerStudy(
		uid="1.2.826.0.1.3680043.8.498.1001",
		patient_name="TEST PATIENT",
		patient_sex="M",
		patient_age="030Y",
		patient_id="P001",
		patient_birthday="1990-01-02",
		study_date="2026-03-26",
		study_time="12:34:56",
		accession_number="ACC-001",
		description="Chest CT",
		facility_id=2945,
		base_url="http://cloud-film.oss-cn-beijing.aliyuncs.com/h5Cache/demo/",
		series=[],
	)
	series = ViewerSeries(
		uid="1.2.826.0.1.3680043.8.498.2001",
		modality="CT",
		number=2,
		description="5mm LUNG",
		body_part="CHEST",
		date="2026-03-26",
		time="12:34:56",
		images=[],
	)
	image = ViewerImage(
		uid="1.2.826.0.1.3680043.8.498.3001",
		instance_number=1,
		frame_count=1,
		format="raw",
		frame_urls=["http://cloud-film.oss-cn-beijing.aliyuncs.com/h5Cache/demo/frame-00000"],
		rows=2,
		columns=2,
		bits_allocated=16,
		bits_stored=16,
		samples_per_pixel=1,
		pixel_representation=1,
		image_position=(1.0, 2.0, 3.0),
		image_orientation=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
		pixel_spacing=(0.8, 0.8),
		slice_location=3.0,
		slice_thickness=5.0,
		window_center=40.0,
		window_width=400.0,
		rescale_slope=1.0,
		rescale_intercept=-1024.0,
		invert=False,
		acquisition_time="12:35:01",
		expected_frame_size=8,
	)
	pixel_data = b"\x00\x00\x01\x00\x02\x00\x03\x00"
	ds = _build_dicom(study, series, image, pixel_data)
	buffer = BytesIO()
	ds.save_as(buffer, enforce_file_format=True)
	buffer.seek(0)
	loaded = dcmread(buffer)

	assert loaded.SOPClassUID == CTImageStorage
	assert loaded.PatientName == "TEST PATIENT"
	assert loaded.Rows == 2
	assert loaded.Columns == 2
	assert loaded.PixelData == pixel_data
	assert loaded.SeriesDescription == "5mm LUNG"
	assert loaded.StudyDate == "20260326"


def test_invalid_dicom_time_is_dropped():
	assert _format_dicom_time("08:21:80") == ""
