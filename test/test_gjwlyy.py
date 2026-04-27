import base64

from pydicom.uid import CTImageStorage
from yarl import URL

from crawlers.gjwlyy import (
	StudyInfo,
	SelectedImage,
	_assemble_tiles,
	_build_dicom,
	_clean_patient_name,
	_parse_share_link,
	_thumbnail_click_point,
	_with_single_series_layout,
)


def _tile(order: int, values: list[int], *, width=2, height=2):
	data = b"".join(value.to_bytes(2, "little") for value in values)
	return {
		"order": order,
		"width": width,
		"height": height,
		"bytesPerSample": 2,
		"samplesPerPixel": 1,
		"signed": False,
		"data": base64.b64encode(data).decode("ascii"),
	}


def test_parse_share_link():
	share = _parse_share_link(URL("http://zjyx.gjwlyy.com/cloudfilmserver/cloudFilm/showShareReport.htm?key=share-key-001"))
	assert share.is_viewer is False

	viewer = _parse_share_link(URL("https://zjyxview.gjwlyy.com/e/viewer?CLOAccessKeyID=access-key-001&arg=viewer-arg-001"))
	assert viewer.is_viewer is True


def test_clean_patient_name():
	assert _clean_patient_name("TEST PATIENT :SITE:12345") == "TEST PATIENT"
	assert _clean_patient_name("  ") == "匿名"


def test_with_single_series_layout():
	url = _with_single_series_layout("https://zjyxview.gjwlyy.com/e/viewer?CLOAccessKeyID=ak&arg=viewer-arg&format=4upSeriesBox")

	assert URL(url).query["CLOAccessKeyID"] == "ak"
	assert URL(url).query["arg"] == "viewer-arg"
	assert URL(url).query["format"] == "1upSeriesBox"


def test_thumbnail_click_point():
	panel = {"x": 0, "y": 821, "width": 1280, "height": 131}

	assert _thumbnail_click_point(panel, 0) == (63, 876)
	assert _thumbnail_click_point(panel, 5) == (688, 876)
	assert _thumbnail_click_point({"x": 0, "y": 821, "width": 200, "height": 131}, 2) is None


def test_assemble_tiles_row_major():
	frame = _assemble_tiles({
		"meta": {
			"bytesAllocated": 2,
			"bitsStored": 12,
			"isSigned": False,
			"rescaleSlope": 1,
			"rescaleIntercept": -1024,
			"windowCenter": 50,
			"windowWidth": 350,
		},
		"tiles": [
			_tile(0, [1, 2, 3, 4]),
			_tile(1, [5, 6, 7, 8]),
			_tile(2, [9, 10, 11, 12]),
			_tile(3, [13, 14, 15, 16]),
		],
	})

	assert frame.rows == 4
	assert frame.columns == 4
	assert frame.bits_stored == 12
	assert frame.rescale_intercept == -1024
	assert frame.pixel_data == b"".join(
		value.to_bytes(2, "little")
		for value in [1, 2, 5, 6, 3, 4, 7, 8, 9, 10, 13, 14, 11, 12, 15, 16]
	)


def test_assemble_tiles_uses_largest_resolution_group():
	frame = _assemble_tiles({
		"meta": {},
		"tiles": [
			_tile(0, [1, 2, 3, 4]),
			_tile(1, [5, 6, 7, 8]),
			_tile(2, [99], width=1, height=1),
		],
	})

	assert frame.rows == 2
	assert frame.columns == 4
	assert frame.pixel_data == b"".join(value.to_bytes(2, "little") for value in [1, 2, 5, 6, 3, 4, 7, 8])


def test_build_dicom_from_pixel_frame():
	study = StudyInfo(
		patient_name="Test Patient",
		patient_id="PID001",
		patient_sex="M",
		study_uid="1.2.826.0.1.3680043.10.1",
		accession_number="ACC001",
		description="CT Chest",
		modality="CT",
		patient_birthdate="19700101",
		series_uids=["1.2.826.0.1.3680043.10.2"],
	)
	selected = SelectedImage(
		study_uid=study.study_uid,
		series_uid=study.series_uids[0],
		instance_uid="1.2.826.0.1.3680043.10.3",
		viewer_instance_id="viewer-instance-001",
		instance_number=0,
		frame_number=1,
	)
	frame = _assemble_tiles({
		"meta": {
			"bytesAllocated": 2,
			"bitsStored": 12,
			"isSigned": False,
			"rescaleSlope": 1,
			"rescaleIntercept": -1024,
		},
		"tiles": [_tile(0, [1, 2, 3, 4])],
	})

	ds = _build_dicom(study, selected, frame, series_index=0, image_index=0)
	assert ds.SOPClassUID == CTImageStorage
	assert ds.Modality == "CT"
	assert ds.Rows == 2
	assert ds.Columns == 2
	assert ds.BitsStored == 12
	assert ds.RescaleIntercept == -1024
	assert ds.PixelData == frame.pixel_data
