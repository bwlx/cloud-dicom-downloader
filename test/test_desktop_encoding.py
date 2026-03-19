from desktop_encoding import ProcessOutputBuffer, decode_process_output


def test_decode_process_output_prefers_utf8_for_chinese_text():
	text = "已将影像包下载到:C:/Users/Test/Downloads/CT影像.zip\n"

	assert decode_process_output(text.encode("utf-8")) == text


def test_decode_process_output_falls_back_to_gb18030():
	text = "已将影像包下载到:C:/Users/Test/Downloads/CT影像.zip\n"

	assert decode_process_output(text.encode("gb18030")) == text


def test_process_output_buffer_handles_split_utf8_chunks():
	buffer = ProcessOutputBuffer()
	text = "保存到: C:/Users/Test/Downloads/影像目录\n"
	raw = text.encode("utf-8")

	first = buffer.feed(raw[:7])
	second = buffer.feed(raw[7:])

	assert first == ""
	assert second == text


def test_process_output_buffer_flushes_tail_without_newline():
	buffer = ProcessOutputBuffer()
	text = "错误: 站点没有返回可识别的 XML"
	raw = text.encode("gb18030")

	assert buffer.feed(raw[:5]) == ""
	assert buffer.feed(raw[5:]) == ""
	assert buffer.flush() == text
