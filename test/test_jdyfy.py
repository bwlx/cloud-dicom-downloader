import pytest
from yarl import URL

from crawlers.jdyfy import (
	_looks_like_login_page,
	_parse_login_free_error,
	_parse_login_free_form,
	_pick_login_free_study,
	_resolve_entry,
	authority_code_prompt,
	requires_authority_code,
	run,
)


def test_resolve_study_page_from_hidden_input():
	address = URL("https://example-hospital.invalid/Study/StudyView?id=share-123&v=0&f=0")
	html = '<input type="hidden" id="StudyId" name="StudyId" value="study-123" />'

	assert _resolve_entry(address, html) == (
		"https://example-hospital.invalid/Study/ViewImage?studyId=study-123",
		False,
	)


def test_resolve_direct_view_image_url():
	address = URL("https://example-hospital.invalid/Study/ViewImage?studyId=study-123")

	assert _resolve_entry(address) == (
		"https://example-hospital.invalid/Study/ViewImage?studyId=study-123",
		False,
	)


def test_resolve_direct_image_viewer_url():
	address = URL(
		"https://viewer.example-hospital.invalid/ImageViewer/StudyView?"
		"StudyId=1.2.3.4.5&hidebtns=287&mode=remote"
	)

	assert _resolve_entry(address) == (str(address), True)


def test_resolve_direct_image_viewer_url_with_return_url():
	address = URL(
		"https://viewer.example-hospital.invalid/ImageViewer/StudyView?"
		"StudyId=1.2.3.4.5&hidebtns=287&mode=remote"
		"&returnUrl=https%3A%2F%2Fexample-hospital.invalid%2FStudy%2FViewImage%3FstudyId%3Dshare-123"
	)

	assert _resolve_entry(address) == (str(address), True)


def test_resolve_return_url():
	address = URL(
		"https://viewer.example-hospital.invalid/ImageViewer/StudyView?"
		"returnUrl=https%3A%2F%2Fexample-hospital.invalid%2FStudy%2FViewImage%3FstudyId%3Dshare-123"
	)

	assert _resolve_entry(address) == (
		"https://example-hospital.invalid/Study/ViewImage?studyId=share-123",
		False,
	)


def test_detect_login_page():
	assert _looks_like_login_page("<html><head><title>登录</title></head><body>/Account/LogOn</body></html>")


def test_login_free_prompt():
	url = "https://example-hospital.invalid/Account/ViewListLoginFree/CT-ACCESSION-001?idType=accessionnumber"
	assert requires_authority_code(url)
	assert authority_code_prompt(url) == "手机号/身份证后四位"


def test_parse_login_free_form():
	html = """
	<input type="radio" id="authTypeMobile" name="AuthorityType" value="Mobile" checked=&quot;checked&quot; />
	<input type="hidden" id="AccountId" name="AccountId" value="account-123" />
	"""

	assert _parse_login_free_form(html) == ("account-123", "Mobile")


def test_parse_login_free_error():
	html = '<span class="fail-msg">验证失败</span><span class="fail-code">(004)</span>'
	assert _parse_login_free_error(html) == "验证失败 (004)"


def test_pick_login_free_study_prefers_ct_item():
	address = URL("https://example-hospital.invalid/Account/ViewListLoginFree/CT-ACCESSION-001?idType=accessionnumber")
	items = [
		{"Id": "study-sc", "AccessionNumber": "CT-ACCESSION-001", "ModalityName": "SC"},
		{"Id": "study-ct", "AccessionNumber": "CT-ACCESSION-001", "ModalityName": "CT"},
	]

	assert _pick_login_free_study(address, items)["Id"] == "study-ct"


def test_pick_login_free_study_raises_without_ct():
	address = URL("https://example-hospital.invalid/Account/ViewListLoginFree/CT-ACCESSION-001?idType=accessionnumber")
	items = [{"Id": "study-es", "AccessionNumber": "CT-ACCESSION-001", "ModalityName": "ES"}]

	with pytest.raises(ValueError, match="没有找到 CT"):
		_pick_login_free_study(address, items)


@pytest.mark.asyncio
async def test_study_view_requires_login_message(monkeypatch):
	class FakeResponse:
		async def text(self):
			return "<html><head><title>登录</title></head><body>/Account/LogOn</body></html>"

		async def __aenter__(self):
			return self

		async def __aexit__(self, *args):
			return False

	class FakeClient:
		def get(self, url):
			return FakeResponse()

		async def __aenter__(self):
			return self

		async def __aexit__(self, *args):
			return False

	monkeypatch.setattr("crawlers.jdyfy.new_http_client", lambda: FakeClient())

	with pytest.raises(ValueError, match="StudyView.*需要登录"):
		await run("https://example-hospital.invalid/Study/StudyView?id=share-123&v=0&f=0")
