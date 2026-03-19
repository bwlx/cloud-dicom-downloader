import codecs
import locale


def _is_cjk(char: str) -> bool:
	code = ord(char)
	return (
		0x3400 <= code <= 0x4DBF
		or 0x4E00 <= code <= 0x9FFF
		or 0xF900 <= code <= 0xFAFF
	)


def _decode_score(text: str, priority: int) -> tuple[int, int, int, int, int]:
	replacement_count = text.count("\ufffd")
	nul_count = text.count("\x00")
	control_count = sum(1 for char in text if ord(char) < 32 and char not in "\r\n\t")
	cjk_count = sum(1 for char in text if _is_cjk(char))
	return replacement_count, nul_count, control_count, priority, -cjk_count


def decode_process_output(raw: bytes) -> str:
	if not raw:
		return ""

	candidates = []
	for encoding in (
		"utf-8",
		"utf-8-sig",
		locale.getpreferredencoding(False),
		"gb18030",
		"cp936",
	):
		if encoding and encoding not in candidates:
			candidates.append(encoding)

	best_text = raw.decode("utf-8", errors="replace")
	best_score = _decode_score(best_text, 0)

	for priority, encoding in enumerate(candidates):
		text = raw.decode(encoding, errors="replace")
		score = _decode_score(text, priority)
		if score < best_score:
			best_text = text
			best_score = score

	return best_text


class ProcessOutputBuffer:
	def __init__(self):
		self._raw_buffer = bytearray()
		self._text_buffer = ""
		self._encoding: str | None = None
		self._decoder = None

	def feed(self, raw: bytes) -> str:
		if not raw:
			return ""

		if self._encoding is None:
			self._raw_buffer.extend(raw)
			if not any(byte in {0x0A, 0x0D} for byte in self._raw_buffer) and len(self._raw_buffer) < 24:
				return ""

			self._encoding = self._detect_encoding(bytes(self._raw_buffer))
			self._decoder = codecs.getincrementaldecoder(self._encoding)(errors="replace")
			self._text_buffer += self._decoder.decode(bytes(self._raw_buffer), final=False)
			self._raw_buffer.clear()
			return self._drain_complete_lines()

		self._text_buffer += self._decoder.decode(raw, final=False)
		return self._drain_complete_lines()

	def flush(self) -> str:
		if self._encoding is None:
			if not self._raw_buffer:
				return ""
			text = decode_process_output(bytes(self._raw_buffer))
			self._raw_buffer.clear()
			return text

		self._text_buffer += self._decoder.decode(b"", final=True)
		text = self._text_buffer
		self._text_buffer = ""
		return text

	def _detect_encoding(self, raw: bytes) -> str:
		if not raw:
			return "utf-8"

		candidates = []
		for encoding in ("utf-8", "utf-8-sig", locale.getpreferredencoding(False), "gb18030", "cp936"):
			if encoding and encoding not in candidates:
				candidates.append(encoding)

		best_encoding = candidates[0]
		best_text = raw.decode(best_encoding, errors="replace")
		best_score = _decode_score(best_text, 0)

		for priority, encoding in enumerate(candidates):
			text = raw.decode(encoding, errors="replace")
			score = _decode_score(text, priority)
			if score < best_score:
				best_encoding = encoding
				best_score = score

		return best_encoding

	def _drain_complete_lines(self) -> str:
		lines = self._text_buffer.splitlines(keepends=True)
		if not lines:
			return ""

		if lines[-1].endswith(("\r", "\n")):
			self._text_buffer = ""
			return "".join(lines)

		self._text_buffer = lines[-1]
		return "".join(lines[:-1])
