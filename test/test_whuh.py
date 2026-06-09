import base64
import json
from datetime import datetime, timedelta

import pytest
from Cryptodome.Cipher import AES, DES
from Cryptodome.Util.Padding import pad
from yarl import URL

from crawlers import whuh


def _encrypt_aes_hex(text: str) -> str:
	encrypted = AES.new(whuh._AES_KEY, AES.MODE_CBC, iv=whuh._AES_IV).encrypt(pad(text.encode("utf-8"), AES.block_size))
	return encrypted.hex().upper()


def _encrypt_des_response(payload: dict) -> str:
	plain = json.dumps(payload, ensure_ascii=False).encode("utf-8")
	encrypted = DES.new(whuh._DES_KEY, DES.MODE_CBC, iv=whuh._DES_IV).encrypt(pad(plain, DES.block_size))
	return base64.b64encode(encrypted).decode("ascii")


def test_parse_share_link_hash_query():
	address = URL("https://xhbi.whuh.com/index.html#/reportView?h=HOSPITAL-001&e=EXAM-001&p=PATIENT-001&r=REPORT-001&t=1&key=KEY-001")

	share = whuh._parse_share_link(address)

	assert share.query["h"] == "HOSPITAL-001"
	assert share.query["e"] == "EXAM-001"
	assert "KEY-001" not in share.redacted_url


def test_parse_share_link_rejects_missing_identity():
	with pytest.raises(ValueError, match="缺少检查号"):
		whuh._parse_share_link(URL("https://xhbi.whuh.com/index.html#/reportView?h=HOSPITAL-001&t=1&key=KEY-001"))


def test_aes_hex_decrypt_roundtrip():
	value = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

	assert whuh._decrypt_aes_hex(_encrypt_aes_hex(value)) == value


def test_des_response_decrypt_roundtrip():
	payload = {"Success": True, "Result": {"Message": "完成"}}

	assert whuh._decrypt_des_response(_encrypt_des_response(payload)) == payload


def test_third_visit_params_maps_short_keys_and_redacts_url():
	share = whuh.ShareLink(
		query={
			"h": "HOSPITAL-001",
			"e": "EXAM-001",
			"p": "PATIENT-001",
			"r": "REPORT-001",
			"t": "1",
			"key": "KEY-001",
			"isShare": "SHARE-FLAG",
			"dateTime": "EXPIRES",
			"id": "REPORT-ID",
		},
		redacted_url="https://xhbi.whuh.com/index.html#/reportView?key=<redacted>",
	)

	payload = whuh._third_visit_params(share)

	assert payload["HospitalNumber"] == "HOSPITAL-001"
	assert {"Key": "ExaminationID", "Value": "EXAM-001"} in payload["VistParms"]
	assert {"Key": "PatientID", "Value": "PATIENT-001"} in payload["VistParms"]
	assert {"Key": "ReportID", "Value": "REPORT-001"} in payload["VistParms"]
	assert {"Key": "VistType", "Value": "1"} in payload["VistParms"]
	assert {"Key": "key", "Value": "KEY-001"} in payload["VistParms"]
	assert {"Key": "Url", "Value": share.redacted_url} in payload["VistParms"]
	assert all(item["Key"] != "dateTime" for item in payload["VistParms"])
