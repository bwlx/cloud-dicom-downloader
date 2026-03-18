from desktop_qr import extract_candidate_urls, pick_share_url


def test_extract_candidate_urls():
	text = "请打开 https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b。"
	assert extract_candidate_urls(text) == [
		"https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=a&content=b"
	]


def test_pick_share_url_prefers_supported_url():
	payloads = [
		"hello world",
		"https://example.com/foo",
		"https://app.ftimage.cn/dimage/index.html?accessionNumber=1&hsCode=2&date=3",
	]
	assert pick_share_url(payloads) == "https://app.ftimage.cn/dimage/index.html?accessionNumber=1&hsCode=2&date=3"


def test_pick_share_url_falls_back_to_first_url():
	payloads = ["prefix https://example.com/a suffix", "https://example.org/b"]
	assert pick_share_url(payloads) == "https://example.com/a"
