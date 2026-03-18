import asyncio
import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import Frame, Page, ElementHandle, Playwright, Browser, Error, BrowserContext, WebSocket, \
	Response, async_playwright

_driver_instance: Any = None
_playwright: Playwright
_browser: Browser


def _runtime_search_roots():
	roots = []
	env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
	if env_path:
		roots.append(Path(env_path))

	exe_dir = Path(sys.executable).resolve().parent
	roots.append(exe_dir)
	roots.append(exe_dir / "_internal")
	roots.append(exe_dir.parent / "Resources")
	roots.append(exe_dir.parent / "Resources" / "_internal")

	if hasattr(sys, "_MEIPASS"):
		meipass = Path(sys._MEIPASS)
		roots.append(meipass)
		roots.append(meipass / "_internal")

	roots.append(Path.home() / "Library/Caches/ms-playwright")
	roots.append(Path.home() / ".cache/ms-playwright")

	unique = []
	for root in roots:
		root = root.expanduser()
		if root not in unique and root.exists():
			unique.append(root)

	return unique


def _find_packaged_chromium():
	patterns = []
	if sys.platform == "darwin":
		patterns = [
			"ms-playwright/chromium-*/**/Chromium.app/Contents/MacOS/Chromium",
			"chromium-*/**/Chromium.app/Contents/MacOS/Chromium",
		]
	elif sys.platform == "win32":
		patterns = [
			"ms-playwright/chromium-*/chrome-win/chrome.exe",
			"chromium-*/chrome-win/chrome.exe",
		]
	else:
		patterns = [
			"ms-playwright/chromium-*/chrome-linux/chrome",
			"chromium-*/chrome-linux/chrome",
		]

	candidates = []
	for root in _runtime_search_roots():
		for pattern in patterns:
			candidates.extend(root.glob(pattern))

	return max((path for path in candidates if path.is_file()), default=None)


def _find_system_chromium():
	if sys.platform == "darwin":
		candidates = [
			Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
			Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
			Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
		]
	elif sys.platform == "win32":
		candidates = [
			Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
			Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
			Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
			Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
		]
	else:
		for name in ("google-chrome", "microsoft-edge", "chromium", "chromium-browser"):
			location = shutil.which(name)
			if location:
				return Path(location)
		return None

	for path in candidates:
		if path.is_file():
			return path

	return None


async def launch_browser(playwright: Playwright) -> Browser:
	"""
	考虑到 Playwright 的支持成熟度，还是尽可能地选择 chromium 系浏览器。
	"""
	try:
		return await playwright.chromium.launch(headless=False)
	except Error as e:
		if not e.message.startswith("BrowserType.launch: Executable doesn't exist"):
			raise

	executable = _find_packaged_chromium()
	if executable:
		print(f"PlayWright: 使用打包的 Chromium 浏览器 {executable}")
		return await playwright.chromium.launch(headless=False, executable_path=str(executable))

	executable = _find_system_chromium()
	if executable:
		print(f"PlayWright: 使用系统浏览器 {executable}")
		return await playwright.chromium.launch(headless=False, executable_path=str(executable))

	raise Exception("未找到可用的 Chromium 浏览器，请先运行 playwright install chromium。")


async def wait_text(context: Page | Frame | ElementHandle, selector: str):
	"""
	等待匹配指定选择器的元素出现，并读取其 textContent 属性。
	最好使用 wait_for_selector 而不是 query_selector，以确保元素已插入。

	:param context: 搜索范围，可以是页面或某个元素。
	:param selector: CSS 选择器
	"""
	return await (await context.wait_for_selector(selector)).text_content()


class PlaywrightCrawler:
	"""本项目的爬虫都比较简单，有固定的模式，所以写个抽象类来统一下代码"""

	_autoclose_waiter = asyncio.Event()
	_context: BrowserContext = None

	def _prepare_page(self, page: Page):
		page.on("websocket", self._on_websocket)
		page.on("close", self._check_all_closed)

	# 关闭窗口并不结束浏览器进程，只能依靠页面计数来判断。
	# https://github.com/microsoft/playwright/issues/2946
	def _check_all_closed(self, _):
		if len(self._context.pages) == 0:
			self._autoclose_waiter.set()

	def _on_response(self, response: Response):
		pass

	def _on_websocket(self, ws: WebSocket):
		pass    

	def _do_run(self, context: BrowserContext):
		pass

	def run(self, context: BrowserContext):
		self._context = context
		context.on("page", self._prepare_page)
		context.on("response", self._on_response)
		return self._do_run(context)


async def run_with_browser(crawler: PlaywrightCrawler, **kwargs):
	"""
	启动 Playwright 浏览器的快捷函数，单个 Browser 实例创建新的 Context。

	因为这库有四层（ContextManager，Playwright，Browser，BrowserContext）
	每次启动都要嵌套好几个 with 很烦，所以搞了一个全局的实例并支持自动销毁。

	:param crawler:
	:param kwargs: 转发到 Browser.new_context() 的参数
	"""
	global _browser, _playwright, _driver_instance

	if not _driver_instance:
		_driver_instance = async_playwright()
		_playwright = await _driver_instance.__aenter__()
		_browser = await launch_browser(_playwright)

	try:
		async with await _browser.new_context(**kwargs) as context:
			return await crawler.run(context)
	finally:
		if len(_browser.contexts) == 0:
			await _browser.close()
			await _driver_instance.__aexit__()
