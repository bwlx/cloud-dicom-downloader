import argparse
import asyncio
import platform
import re
import tempfile
import sys
from pathlib import Path

APP_NAME = "Cloud DICOM Downloader"


def _qt_import_search_roots() -> list[Path]:
	if getattr(sys, "frozen", False):
		exe_dir = Path(sys.executable).resolve().parent
		roots = [exe_dir, exe_dir / "_internal"]
	else:
		base_dir = Path(__file__).resolve().parent
		roots = [base_dir, base_dir / "_internal"]

	if hasattr(sys, "_MEIPASS"):
		meipass = Path(sys._MEIPASS)
		roots.extend([meipass, meipass / "_internal"])

	unique = []
	for root in roots:
		if root not in unique:
			unique.append(root)
	return unique


def _write_qt_import_diagnostics(exc: ImportError) -> Path:
	log_path = Path(tempfile.gettempdir()) / "cloud-dicom-downloader-qt-import.log"
	relative_candidates = (
		"PySide6/QtCore.pyd",
		"PySide6/Qt6Core.dll",
		"PySide6/shiboken6.abi3.dll",
		"vcruntime140.dll",
		"vcruntime140_1.dll",
		"msvcp140.dll",
		"concrt140.dll",
	)

	lines = [
		f"error={exc}",
		f"platform={platform.platform()}",
		f"release={platform.release()}",
		f"version={platform.version()}",
		f"machine={platform.machine()}",
		f"python_executable={sys.executable}",
		f"frozen={getattr(sys, 'frozen', False)}",
	]

	for root in _qt_import_search_roots():
		lines.append(f"search_root={root}")
		for relative in relative_candidates:
			candidate = root / relative
			lines.append(f"exists[{candidate}]={candidate.exists()}")

	log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
	return log_path


try:
	from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, QTimer, QUrl
	from PySide6.QtGui import QDesktopServices, QFont, QTextCursor
	from PySide6.QtWidgets import (
		QApplication,
		QCheckBox,
		QDialog,
		QDialogButtonBox,
		QFileDialog,
		QGridLayout,
		QGroupBox,
		QHBoxLayout,
		QLabel,
		QLineEdit,
		QListWidget,
		QMainWindow,
		QMessageBox,
		QPlainTextEdit,
		QProgressBar,
		QPushButton,
		QVBoxLayout,
		QWidget,
	)
except ImportError as exc:
	if sys.platform == "win32":
		log_path = _write_qt_import_diagnostics(exc)
		try:
			import ctypes

			message = (
				"Qt 运行时加载失败。\n\n"
				f"{exc}\n\n"
				f"诊断日志已写入：\n{log_path}\n\n"
				"常见原因：\n"
				"1. 目标系统版本过旧。\n"
				"2. Qt 或 VC++ 运行库 DLL 缺失。"
			)
			ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
		except Exception:
			pass
		raise SystemExit(1)
	raise

from desktop_core import DownloadRequest, run_download_request, url_password_prompt, url_requires_password, url_supports_raw
from desktop_encoding import ProcessOutputBuffer
from desktop_qr import decode_qr_image, pick_share_url
from crawlers import jdyfy
from runtime_config import DOWNLOAD_ROOT_ENV

BASE_DIR = Path(__file__).resolve().parent
_SAVE_PATTERNS = (
	re.compile(r"保存到[:：]\s*(.+)"),
	re.compile(r"下载完成，保存位置\s*(.+)"),
	re.compile(r"下载.+?到[:：]\s*(.+)"),
)


def build_worker_arguments(request: DownloadRequest) -> list[str]:
	args = ["--worker", request.url]

	if request.password:
		args.extend(["--password", request.password])
	if request.raw:
		args.append("--raw")
	if request.output_dir:
		args.extend(["--output", request.output_dir])

	return args


def default_output_dir() -> str:
	downloads_dir = Path.home() / "Downloads" / "cloud-dicom-downloader"
	return str(downloads_dir)


def worker_entry(argv: list[str]) -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--worker", action="store_true")
	parser.add_argument("--password")
	parser.add_argument("--raw", action="store_true")
	parser.add_argument("--output")
	parser.add_argument("url")
	args = parser.parse_args(argv)

	request = DownloadRequest(
		url=args.url,
		password=args.password,
		raw=args.raw,
		output_dir=args.output,
	)

	try:
		asyncio.run(run_download_request(request))
	except Exception as exc:
		print(f"错误: {exc}", file=sys.stderr)
		return 1

	return 0


def format_study_option(study: dict) -> str:
	parts = [
		str(study.get("AccessionNumber") or "").strip(),
		str(study.get("ExamDate") or "").strip(),
		str(study.get("BodyPart") or "").strip(),
		str(study.get("StudyDescription") or "").strip(),
	]
	return " | ".join(part for part in parts if part)


def is_suffix_code_prompt(prompt: str | None) -> bool:
	return bool(prompt and "后四位" in prompt)


class StudySelectionDialog(QDialog):
	def __init__(self, studies: list[dict], parent=None):
		super().__init__(parent)
		self.studies = studies
		self.setWindowTitle("选择 CT 检查")
		self.resize(760, 320)

		layout = QVBoxLayout(self)
		layout.setContentsMargins(16, 16, 16, 16)
		layout.setSpacing(12)

		label = QLabel("发现多个 CT 检查，请选择需要下载的项目。")
		label.setWordWrap(True)

		self.list_widget = QListWidget()
		for study in studies:
			self.list_widget.addItem(format_study_option(study))
		self.list_widget.setCurrentRow(0)
		self.list_widget.itemDoubleClicked.connect(lambda *_: self.accept())

		buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
		buttons.accepted.connect(self.accept)
		buttons.rejected.connect(self.reject)

		layout.addWidget(label)
		layout.addWidget(self.list_widget, 1)
		layout.addWidget(buttons)

	def selected_study(self) -> dict | None:
		row = self.list_widget.currentRow()
		if row < 0:
			return None
		return self.studies[row]


class MainWindow(QMainWindow):
	def __init__(self):
		super().__init__()
		self.process: QProcess | None = None
		self.current_output_path: str | None = None
		self.stdout_buffer = ProcessOutputBuffer()
		self.stderr_buffer = ProcessOutputBuffer()
		self.settings = QSettings("codex", "cloud-dicom-downloader")

		self.setWindowTitle(APP_NAME)
		self.resize(980, 720)
		self.setMinimumSize(820, 620)
		self._build_ui()
		self._apply_style()
		self._restore_settings()
		self._update_url_state()

	def _build_ui(self):
		root = QWidget(self)
		layout = QVBoxLayout(root)
		layout.setContentsMargins(24, 24, 24, 24)
		layout.setSpacing(16)

		header = QLabel("医疗云影像下载器")
		header.setObjectName("HeroTitle")
		header.setFont(QFont("PingFang SC", 22, QFont.Weight.DemiBold))

		subtitle = QLabel("纯本地运行，输入报告链接后直接下载 DICOM 到本机目录。")
		subtitle.setObjectName("HeroSubtitle")
		subtitle.setWordWrap(True)

		form_box = QGroupBox("下载任务")
		form_layout = QGridLayout(form_box)
		form_layout.setHorizontalSpacing(12)
		form_layout.setVerticalSpacing(12)

		self.url_edit = QLineEdit()
		self.url_edit.setPlaceholderText("粘贴报告链接")
		self.url_edit.textChanged.connect(self._update_url_state)

		scan_button = QPushButton("选择图片扫码或直接输入链接")
		scan_button.clicked.connect(self._scan_qr_from_image)

		self.password_edit = QLineEdit()
		self.password_edit.setPlaceholderText("该站点需要凭证时填写")
		self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

		self.raw_check = QCheckBox("下载原始像素（仅支持海纳医信相关站点）")

		self.output_edit = QLineEdit()
		self.output_edit.setPlaceholderText("选择输出目录")

		browse_button = QPushButton("选择目录")
		browse_button.clicked.connect(self._select_output_dir)

		self.site_hint = QLabel()
		self.site_hint.setObjectName("HintLabel")
		self.site_hint.setWordWrap(True)

		form_layout.addWidget(QLabel("报告链接"), 0, 0)
		form_layout.addWidget(self.url_edit, 0, 1)
		form_layout.addWidget(scan_button, 0, 2)
		self.password_label = QLabel("访问凭证")
		form_layout.addWidget(self.password_label, 1, 0)
		form_layout.addWidget(self.password_edit, 1, 1, 1, 2)
		form_layout.addWidget(self.raw_check, 2, 1, 1, 2)
		form_layout.addWidget(QLabel("保存目录"), 3, 0)
		form_layout.addWidget(self.output_edit, 3, 1)
		form_layout.addWidget(browse_button, 3, 2)
		form_layout.addWidget(self.site_hint, 4, 0, 1, 3)

		status_box = QGroupBox("运行状态")
		status_layout = QVBoxLayout(status_box)
		status_layout.setSpacing(10)

		self.status_label = QLabel("待开始")
		self.status_label.setObjectName("StatusLabel")

		self.progress_bar = QProgressBar()
		self.progress_bar.setTextVisible(False)
		self.progress_bar.setRange(0, 1)
		self.progress_bar.setValue(0)

		log_actions = QHBoxLayout()
		log_actions.setSpacing(10)

		self.start_button = QPushButton("开始下载")
		self.start_button.clicked.connect(self._start_download)

		self.stop_button = QPushButton("停止任务")
		self.stop_button.clicked.connect(self._stop_download)
		self.stop_button.setEnabled(False)

		self.open_button = QPushButton("打开目录")
		self.open_button.clicked.connect(self._open_output_dir)
		self.open_button.setEnabled(False)

		self.log_edit = QPlainTextEdit()
		self.log_edit.setReadOnly(True)
		self.log_edit.setPlaceholderText("运行日志会显示在这里")

		clear_button = QPushButton("清空日志")
		clear_button.clicked.connect(self.log_edit.clear)

		log_actions.addWidget(self.start_button)
		log_actions.addWidget(self.stop_button)
		log_actions.addWidget(self.open_button)
		log_actions.addStretch(1)
		log_actions.addWidget(clear_button)

		status_layout.addWidget(self.status_label)
		status_layout.addWidget(self.progress_bar)
		status_layout.addLayout(log_actions)
		status_layout.addWidget(self.log_edit, 1)

		layout.addWidget(header)
		layout.addWidget(subtitle)
		layout.addWidget(form_box)
		layout.addWidget(status_box, 1)
		self.setCentralWidget(root)

	def _apply_style(self):
		self.setStyleSheet(
			"""
			QWidget {
				background: #f4efe7;
				color: #1f2933;
				font-size: 14px;
			}
			QGroupBox {
				background: #fffaf3;
				border: 1px solid #d8cdbf;
				border-radius: 14px;
				margin-top: 12px;
				padding-top: 18px;
				font-weight: 600;
			}
			QGroupBox::title {
				subcontrol-origin: margin;
				left: 14px;
				padding: 0 6px;
			}
			QLineEdit, QPlainTextEdit {
				background: #fffdf9;
				border: 1px solid #cbbba9;
				border-radius: 10px;
				padding: 10px 12px;
				selection-background-color: #a54d2d;
			}
			QPushButton {
				background: #a54d2d;
				border: none;
				border-radius: 10px;
				color: white;
				padding: 10px 16px;
				font-weight: 600;
			}
			QPushButton:disabled {
				background: #c7b8aa;
				color: #f7f2eb;
			}
			QPushButton:hover:!disabled {
				background: #8f4125;
			}
			QCheckBox {
				padding-top: 2px;
			}
			QProgressBar {
				border: 1px solid #d8cdbf;
				border-radius: 8px;
				background: #fbf6ee;
				min-height: 12px;
			}
			QProgressBar::chunk {
				border-radius: 8px;
				background: #ce7338;
			}
			QLabel#HeroTitle {
				color: #3f2a1d;
			}
			QLabel#HeroSubtitle, QLabel#HintLabel {
				color: #64584c;
			}
			QLabel#StatusLabel {
				font-size: 15px;
				font-weight: 600;
				color: #583726;
			}
			"""
		)

	def _restore_settings(self):
		self.output_edit.setText(self.settings.value("output_dir", default_output_dir()))

	def closeEvent(self, event):
		if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
			reply = QMessageBox.question(self, "退出确认", "当前还有下载任务，确定要退出吗？")
			if reply != QMessageBox.StandardButton.Yes:
				event.ignore()
				return
			self.process.kill()
			self.process.waitForFinished(3000)

		self.settings.setValue("output_dir", self.output_edit.text().strip())
		super().closeEvent(event)

	def _select_output_dir(self):
		directory = QFileDialog.getExistingDirectory(self, "选择下载目录", self.output_edit.text().strip())
		if directory:
			self.output_edit.setText(directory)

	def _scan_qr_from_image(self):
		path, _ = QFileDialog.getOpenFileName(
			self,
			"选择报告图片",
			"",
			"Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;All Files (*)",
		)
		if not path:
			return

		try:
			payloads = decode_qr_image(path)
		except Exception as exc:
			QMessageBox.warning(self, "扫码失败", str(exc))
			return

		url = pick_share_url(payloads)
		if not url:
			QMessageBox.information(self, "未识别到链接", "图片中没有识别到可用的二维码链接。")
			return

		self.url_edit.setText(url)
		self.status_label.setText("已从图片识别链接")
		self._append_log_text(f"已从图片识别到链接：{url}\n")

	def _update_url_state(self):
		url = self.url_edit.text().strip()
		password_required = False
		password_prompt = None
		raw_supported = False

		if url:
			try:
				password_required = url_requires_password(url)
				password_prompt = url_password_prompt(url)
				raw_supported = url_supports_raw(url)
			except Exception:
				self.site_hint.setText("链接暂时无法识别，请检查是否完整。")
			else:
				if password_required:
					if password_prompt == "手机号/身份证后四位":
						self.site_hint.setText("该链接需要填写手机号或身份证后四位，程序会自动从列表中选择 CT 检查。")
					elif is_suffix_code_prompt(password_prompt):
						self.site_hint.setText(f"该链接需要填写{password_prompt}。")
					else:
						self.site_hint.setText("该站点需要访问密码。")
				elif raw_supported:
					self.site_hint.setText("该站点支持原始像素下载。")
				else:
					self.site_hint.setText("该站点不需要密码，默认按兼容格式下载。")
		else:
			self.site_hint.setText("支持直接粘贴医疗影像报告链接。")

		self.password_edit.setEnabled(password_required)
		if is_suffix_code_prompt(password_prompt):
			self.password_label.setText("后四位")
			self.password_edit.setPlaceholderText(f"填写{password_prompt}")
			self.password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
		else:
			self.password_label.setText("访问凭证")
			self.password_edit.setPlaceholderText("该站点需要凭证时填写")
			self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
		if not password_required:
			self.password_edit.clear()

		self.raw_check.setEnabled(raw_supported)
		if not raw_supported:
			self.raw_check.setChecked(False)

	def _start_download(self):
		url = self.url_edit.text().strip()
		password = self.password_edit.text().strip() or None
		output_dir = self.output_edit.text().strip()

		if not url:
			QMessageBox.warning(self, "参数错误", "请先输入报告链接。")
			return
		if not output_dir:
			QMessageBox.warning(self, "参数错误", "请选择保存目录。")
			return
		try:
			password_required = url_requires_password(url)
			password_prompt = url_password_prompt(url)
		except Exception:
			QMessageBox.warning(self, "参数错误", "报告链接格式不正确。")
			return

		if password_required and not password:
			if is_suffix_code_prompt(password_prompt):
				QMessageBox.warning(self, "参数错误", f"该链接需要填写{password_prompt}。")
			else:
				QMessageBox.warning(self, "参数错误", "该站点需要访问密码。")
			return

		request = DownloadRequest(
			url=url,
			password=password,
			raw=self.raw_check.isChecked(),
			output_dir=output_dir,
		)
		try:
			request, selection_label = self._prepare_request(request)
		except Exception as exc:
			QMessageBox.warning(self, "参数错误", str(exc))
			self.status_label.setText("待开始")
			return
		if request is None:
			self.status_label.setText("已取消")
			return

		self.log_edit.clear()
		self.stdout_buffer = ProcessOutputBuffer()
		self.stderr_buffer = ProcessOutputBuffer()
		if selection_label:
			self._append_log_text(f"已选择检查：{selection_label}\n")
		self.current_output_path = None
		self.open_button.setEnabled(False)
		self.status_label.setText("任务启动中")
		self.progress_bar.setRange(0, 0)
		self.start_button.setEnabled(False)
		self.stop_button.setEnabled(True)
		self.settings.setValue("output_dir", output_dir)

		process = QProcess(self)
		process.setWorkingDirectory(str(BASE_DIR))
		process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)

		env = QProcessEnvironment.systemEnvironment()
		env.insert("PYTHONUNBUFFERED", "1")
		env.insert("PYTHONUTF8", "1")
		env.insert("PYTHONIOENCODING", "utf-8")
		env.insert(DOWNLOAD_ROOT_ENV, output_dir)
		process.setProcessEnvironment(env)

		process.readyReadStandardOutput.connect(self._consume_stdout)
		process.readyReadStandardError.connect(self._consume_stderr)
		process.finished.connect(self._on_process_finished)

		if getattr(sys, "frozen", False):
			program = sys.executable
			args = build_worker_arguments(request)
		else:
			program = sys.executable
			args = [str(Path(__file__).resolve()), *build_worker_arguments(request)]

		self.process = process
		process.start(program, args)
		if not process.waitForStarted(5000):
			self.status_label.setText("任务启动失败")
			self.progress_bar.setRange(0, 1)
			self.progress_bar.setValue(0)
			self.start_button.setEnabled(True)
			self.stop_button.setEnabled(False)
			QMessageBox.critical(self, "启动失败", "无法启动本地下载进程。")
			process.deleteLater()
			self.process = None
			return

	def _prepare_request(self, request: DownloadRequest) -> tuple[DownloadRequest | None, str | None]:
		if not request.password or not jdyfy.requires_authority_code(request.url):
			return request, None

		self.status_label.setText("正在读取检查列表")
		QApplication.processEvents()
		studies = asyncio.run(jdyfy.list_login_free_ct_studies(request.url, request.password))
		if len(studies) == 1:
			study = studies[0]
		else:
			dialog = StudySelectionDialog(studies, self)
			if dialog.exec() != QDialog.DialogCode.Accepted:
				return None, None
			study = dialog.selected_study()
			if not study:
				return None, None

		study_label = format_study_option(study)
		return DownloadRequest(
			url=jdyfy.build_login_free_view_image_url(request.url, study),
			password=None,
			raw=request.raw,
			output_dir=request.output_dir,
		), study_label

	def _append_log_text(self, text: str):
		cleaned = text.replace("\r", "\n")
		if not cleaned:
			return

		self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
		self.log_edit.insertPlainText(cleaned)
		self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

		for line in cleaned.splitlines():
			path = self._extract_output_path(line.strip())
			if path:
				self.current_output_path = path
				self.open_button.setEnabled(True)

	def _extract_output_path(self, line: str) -> str | None:
		for pattern in _SAVE_PATTERNS:
			match = pattern.search(line)
			if match:
				return match.group(1).strip()
		return None

	def _consume_stdout(self):
		if not self.process:
			return
		text = self.stdout_buffer.feed(bytes(self.process.readAllStandardOutput()))
		self._append_log_text(text)

	def _consume_stderr(self):
		if not self.process:
			return
		text = self.stderr_buffer.feed(bytes(self.process.readAllStandardError()))
		self._append_log_text(text)

	def _on_process_finished(self, exit_code: int, _status):
		self._append_log_text(self.stdout_buffer.flush())
		self._append_log_text(self.stderr_buffer.flush())
		self.progress_bar.setRange(0, 1)
		self.progress_bar.setValue(1 if exit_code == 0 else 0)
		self.start_button.setEnabled(True)
		self.stop_button.setEnabled(False)

		if exit_code == 0:
			self.status_label.setText("下载完成")
		else:
			self.status_label.setText("下载失败")

		if self.process:
			self.process.deleteLater()
			self.process = None

	def _stop_download(self):
		if not self.process:
			return

		self.status_label.setText("正在停止任务")
		self.process.terminate()

		def hard_kill():
			if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
				self.process.kill()

		QTimer.singleShot(3000, hard_kill)

	def _open_output_dir(self):
		path = self.current_output_path or self.output_edit.text().strip()
		if not path:
			return
		QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def gui_entry() -> int:
	app = QApplication(sys.argv)
	window = MainWindow()
	window.show()
	return app.exec()


def main() -> int:
	if "--worker" in sys.argv[1:]:
		return worker_entry(sys.argv[1:])
	return gui_entry()


if __name__ == "__main__":
	raise SystemExit(main())
