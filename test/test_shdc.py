from crawlers.shdc import _get_save_dir, _repair_payload, _repair_text


def test_repair_text_fixes_utf8_mojibake():
	assert _repair_text("è\x83¸é\x83¨ï¼\x88å¢\x9eå¼ºï¼\x89") == "胸部（增强）"


def test_repair_payload_repairs_nested_strings():
	payload = {
		"study": {
			"description": "è\x83¸é\x83¨ï¼\x88å¢\x9eå¼ºï¼\x89",
			"series": [{"description": "è\x82ºçª\x97"}],
		}
	}
	repaired = _repair_payload(payload)
	assert repaired["study"]["description"] == "胸部（增强）"
	assert repaired["study"]["series"][0]["description"] == "肺窗"


def test_get_save_dir_with_repaired_payload_is_gbk_encodable():
	study = {
		"study_datetime": None,
		"study_date": "2026-03-21",
		"study_time": "13:32:25",
		"description": _repair_text("è\x83¸é\x83¨ï¼\x88å¢\x9eå¼ºï¼\x89"),
		"modality_type": "CT",
		"patient": {"name": "LI JIAN WEI"},
	}
	save_dir = _get_save_dir(study)
	assert "胸部（增强）" in str(save_dir)
	str(save_dir).encode("gbk")
