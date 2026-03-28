from pathlib import Path
from zipfile import ZipFile

import pytest
from yarl import URL

from crawlers._utils import IncompleteDownloadError
from crawlers.radonline import _extract_series_archive, _parse_share_link, _parse_study_info


def test_parse_share_link_supports_share_page():
	link = _parse_share_link(
		URL("https://film.radonline.cn/web/fore-end/index.html#/check-detail-share?shareId=share-id-001&xeguId=20260101010101&unitId=unit-001")
	)
	assert link.is_viewer is False


def test_parse_share_link_supports_viewer_page():
	link = _parse_share_link(
		URL("https://film.radonline.cn/webImageSyn/activeImage.html?mergeParameters=encrypted-payload#/")
	)
	assert link.is_viewer is True


def test_parse_share_link_rejects_unknown_url():
	with pytest.raises(ValueError, match="锐达云影像"):
		_parse_share_link(URL("https://film.radonline.cn/web/fore-end/index.html#/report?shareId=share-id-001"))


def test_parse_study_info():
	study = _parse_study_info({
		"patientName": "匿名",
		"studyTime": "2026/03/27",
		"studyId": "CT-001",
		"xeguId": "20260101010101",
		"description": "胸部CT平扫",
		"modality": "CT",
		"series": [
			{"index": 0, "number": "201", "description": "Lung 5mm", "totalImages": 71},
			{"index": 1, "number": "", "description": "", "totalImages": 1},
		],
	})
	assert study.patient_name == "匿名"
	assert study.xegu_id == "20260101010101"
	assert study.series[0].number == 201
	assert study.series[1].number is None


def test_extract_series_archive_strips_pk_suffix(tmp_path: Path):
	archive_path = tmp_path / "series.zip"
	with ZipFile(archive_path, "w") as archive:
		archive.writestr("A.dcm.pk", b"\x00" * 128 + b"DICM" + b"123")
		archive.writestr("B.dcm.pk", b"\x00" * 128 + b"DICM" + b"456")

	target_dir = tmp_path / "series"
	_extract_series_archive(archive_path, target_dir, expected_files=2)

	assert sorted(path.name for path in target_dir.iterdir()) == ["A.dcm", "B.dcm"]


def test_extract_series_archive_checks_file_count(tmp_path: Path):
	archive_path = tmp_path / "series.zip"
	with ZipFile(archive_path, "w") as archive:
		archive.writestr("only-one.dcm.pk", b"\x00" * 128 + b"DICM")

	with pytest.raises(IncompleteDownloadError, match="下载不完整"):
		_extract_series_archive(archive_path, tmp_path / "series", expected_files=2)
