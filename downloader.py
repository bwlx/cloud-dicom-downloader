import argparse
import asyncio

from desktop_core import DownloadRequest, run_download_request


def parse_args(argv=None):
	parser = argparse.ArgumentParser()
	parser.add_argument("url", help="报告链接")
	parser.add_argument("password", nargs="?", help="需要密码的站点使用")
	parser.add_argument("--raw", action="store_true", help="下载原始像素，仅部分站点支持")
	parser.add_argument("--output", help="自定义下载根目录")
	return parser.parse_args(argv)


async def main(argv=None):
	args = parse_args(argv)
	request = DownloadRequest(
		url=args.url,
		password=args.password,
		raw=args.raw,
		output_dir=args.output,
	)
	await run_download_request(request)


if __name__ == "__main__":
	asyncio.run(main())
