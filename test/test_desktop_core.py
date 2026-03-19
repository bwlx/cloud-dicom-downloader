import pytest

from desktop_core import resolve_crawler_module, url_password_prompt, url_requires_password, url_supports_raw


@pytest.mark.parametrize(
	("url", "module_name"),
	[
		("https://foo.medicalimagecloud.com/t/abc", "crawlers.hinacom"),
		("https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b", "crawlers.cq12320"),
		("https://app.ftimage.cn/dimage/index.html?x=1", "crawlers.ftimage"),
		("https://ss.mtywcloud.com/ICCWebClient/Image/Viewer?x=1", "crawlers.mtywcloud"),
		("http://medapi.dsrmyy.cn:9088/s/share-sid-123", "crawlers.medapi"),
		(
			"https://pacs.ydyy.cn:8860/M-Viewer/#/phone-visible/BUSS-001?hideQrcode=1&forward=phone-visible&shortUrl=short-001&idType=3&sign=jwt-token",
			"crawlers.ydyy",
		),
	],
)
def test_resolve_crawler_module(url, module_name):
	assert resolve_crawler_module(url).__name__ == module_name


def test_password_requirement():
	assert url_requires_password("https://foo.medicalimagecloud.com/t/abc")
	assert url_requires_password("https://example-hospital.invalid/Account/ViewListLoginFree/CT-ACCESSION-001?idType=accessionnumber")
	assert url_requires_password("https://example-hospital.invalid/r/CT-ACCESSION-001/accessionnumber")
	assert url_requires_password(
		"https://pacs.ydyy.cn:8860/M-Viewer/#/phone-visible/BUSS-001?hideQrcode=1&forward=phone-visible&shortUrl=short-001&idType=3&sign=jwt-token"
	)
	assert url_requires_password("https://pacs.ydyy.cn:8860/M-Viewer/shortserver/short-001")
	assert not url_requires_password("https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b")


def test_password_prompt():
	assert url_password_prompt("https://foo.medicalimagecloud.com/t/abc") == "访问密码"
	assert url_password_prompt("https://example-hospital.invalid/Account/ViewListLoginFree/CT-ACCESSION-001?idType=accessionnumber") == "手机号/身份证后四位"
	assert url_password_prompt("https://example-hospital.invalid/r/CT-ACCESSION-001/accessionnumber") == "手机号/身份证后四位"
	assert url_password_prompt(
		"https://pacs.ydyy.cn:8860/M-Viewer/#/phone-visible/BUSS-001?hideQrcode=1&forward=phone-visible&shortUrl=short-001&idType=3&sign=jwt-token"
	) == "身份证后四位"
	assert url_password_prompt("https://pacs.ydyy.cn:8860/M-Viewer/shortserver/short-001") == "身份证后四位"
	assert url_password_prompt("https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b") is None


def test_raw_support():
	assert url_supports_raw("https://foo.medicalimagecloud.com/t/abc")
	assert url_supports_raw("https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b")
	assert not url_supports_raw("https://ss.mtywcloud.com/ICCWebClient/Image/Viewer?x=1")
