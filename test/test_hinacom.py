import pytest

from crawlers.hinacom import _parse_viewer_vars


def test_parse_viewer_vars():
	html = """
	<script>
	var STUDY_ID = "study-1";
	var ACCESSION_NUMBER = "acc-2";
	var STUDY_EXAM_UID = "exam-3";
	var LOAD_IMAGE_CACHE_KEY = "cache-4";
	</script>
	"""

	assert _parse_viewer_vars(html) == ("study-1", "acc-2", "exam-3", "cache-4")


def test_parse_viewer_vars_raise_readable_message_for_login_page():
	html = "<html><head><title>登录</title></head><body>/Account/LogOn</body></html>"

	with pytest.raises(ValueError, match="需要登录"):
		_parse_viewer_vars(html)
