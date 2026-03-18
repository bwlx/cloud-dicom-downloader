import json

import pytest
from yarl import URL

from crawlers.medapi import (
	_decode_api_data,
	_decrypt_text,
	_encrypt_text,
	_extract_share_sid,
	_parse_short_url_payload,
)


def test_extract_share_sid_from_short_url():
	address = URL("https://example-medapi.invalid:9443/s/share-sid-123")
	assert _extract_share_sid(address) == "share-sid-123"


def test_extract_share_sid_from_share_index_url():
	address = URL("https://example-medapi.invalid/sharevisit/mobile/digitalimage/index?sid=share-sid-123")
	assert _extract_share_sid(address) == "share-sid-123"


def test_extract_share_sid_from_redirect_url():
	address = URL("https://example-medapi.invalid/redirect/index.html?sid=share-sid-123")
	assert _extract_share_sid(address) == "share-sid-123"


def test_extract_share_sid_rejects_unknown_url():
	address = URL("https://example-medapi.invalid/unsupported/path")
	with pytest.raises(ValueError, match="不是受支持的数字影像分享链接"):
		_extract_share_sid(address)


def test_parse_short_url_payload_reads_observation_id_from_extras():
	payload = {
		"data": {
			"hash_id": "share-sid-123",
			"extras": json.dumps({"ObservationId": 1234567890}),
		}
	}

	info = _parse_short_url_payload(payload)

	assert info.share_sid == "share-sid-123"
	assert info.observation_id == "1234567890"


def test_parse_short_url_payload_raises_without_observation_id():
	payload = {
		"data": {
			"hash_id": "share-sid-123",
			"extras": "{}",
		}
	}

	with pytest.raises(ValueError, match="没有拿到检查标识"):
		_parse_short_url_payload(payload)


def test_encrypt_decrypt_roundtrip():
	text = (
		"Pacs=10.0.0.1@104@AE&AccessionNumber=ACC-001&PatientID=PID-001&"
		"StudyInstanceUID=&BusinessId=obs-001&Anonymous=true"
	)

	assert _decrypt_text(_encrypt_text(text)) == text


def test_decode_api_data_supports_encrypted_payload():
	payload = {
		"Code": 10,
		"Data": _encrypt_text(json.dumps([{"StudyInstanceUID": "study-001"}])),
	}

	assert _decode_api_data(payload) == [{"StudyInstanceUID": "study-001"}]
