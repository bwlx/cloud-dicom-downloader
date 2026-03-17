import pytest

from desktop_core import resolve_crawler_module, url_requires_password, url_supports_raw


@pytest.mark.parametrize(
	("url", "module_name"),
	[
		("https://foo.medicalimagecloud.com/t/abc", "crawlers.hinacom"),
		("https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b", "crawlers.cq12320"),
		("https://app.ftimage.cn/dimage/index.html?x=1", "crawlers.ftimage"),
		("https://ss.mtywcloud.com/ICCWebClient/Image/Viewer?x=1", "crawlers.mtywcloud"),
	],
)
def test_resolve_crawler_module(url, module_name):
	assert resolve_crawler_module(url).__name__ == module_name


def test_password_requirement():
	assert url_requires_password("https://foo.medicalimagecloud.com/t/abc")
	assert not url_requires_password("https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b")


def test_raw_support():
	assert url_supports_raw("https://foo.medicalimagecloud.com/t/abc")
	assert url_supports_raw("https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b")
	assert not url_supports_raw("https://ss.mtywcloud.com/ICCWebClient/Image/Viewer?x=1")
