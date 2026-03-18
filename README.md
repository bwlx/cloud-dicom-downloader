# cloud-dicom-downloader

医疗云影像下载器，从在线报告下载 CT、MRI 等片子的 DICOM 文件。

> [!WARNING]
> 
> 由于没有时间，本项目不再更新，也不再免费帮下载片子；如要下载可联系 [contact@kaciras.com](mailto:contact@kaciras.com)，每次检查 50￥，需要报告的二维码或链接。
> 
> 下载须知：
> - 部分医院的系统复杂，需要较长的时间（数小时）才能爬取。
> - 少数系统不提供原始文件，无法下载，已知有：`锐珂 CareaStream`、`联众医疗 eImage`、`东软睿影 cloud film system`

关于下载的格式：

一个检查下包含多个目录，每目录对应一个序列，序列内每一个切面保存为一个`.dcm`扩展名结尾的 DICOM 文件，文件的结构为：
```text
[患者姓名]-[检查项目]-[时间]
|
├─── [序列编号]-[序列名1]
|    |
|    ├── 00001.dcm
|    ├── 00002.dcm
|    └── ......
|
├─── [序列编号]-[序列名2]
└─── ......
```
可以通过阅片软件或在线阅片网站来查看下载的文件，打开时选择某个序列或整个检查的文件夹即可。

* [使用步骤](#使用步骤)
* [支持的站点](#支持的站点)
  * [medicalimagecloud.com](#medicalimagecloudcom)
  * [mdmis.cq12320.cn](#mdmiscq12320cn)
  * [ylyyx.shdc.org.cn](#ylyyxshdcorgcn)
  * [zscloud.zs-hospital.sh.cn](#zs-hospitalshcn)
  * [ftimage.cn](#ftimagecn)
  * [qr.szjudianyun.com](#qrszjudianyuncom)
  * [ss.mtywcloud.com](#ssmtywcloudcom)
  * [m.yzhcloud.com](#myzhcloudcom)
  * [work.sugh.net](#worksughnet)

## 使用步骤

- 先确保您的报告链接是有效的，能够通过浏览器访问，没有过期。
- 本项目需要 Python 来运行，没有就去 [https://www.python.org](https://www.python.org/downloads) 下载并安装。
- 克隆代码（不会的可以点击右上角的 Code -> Download ZIP，然后解压）。
- 进入解压后的目录，运行命令行（右键 -> 在终端中打开）。
- 输入`pip install -r requirements.txt`并按回车键。
- 等待运行完成，然后根据要下载的网站，选择下面一节中的的命令运行。

### 桌面客户端

如果希望直接使用本地桌面客户端，可以安装桌面依赖后运行：

```bash
pip install -r requirements-desktop.txt
python desktop_app.py
```

桌面版仍然完全本地运行，不依赖服务端；可以直接粘贴报告链接、选择保存目录并查看下载日志。
也支持在桌面端选择本地图片识别报告单二维码，然后自动回填下载链接。
应用内部路径处理按 Unicode 实现，安装目录和下载目录可使用中文路径。

### 打包 macOS 安装包

在 macOS 上可以直接执行：

```bash
./build_macos.sh
```

脚本会安装打包依赖、补齐 Playwright 的 Chromium、生成 `.app` 和 `.dmg`，产物位于 `dist/` 目录。
当前脚本生成的是未签名安装包；如果要在外部分发，还需要自行做 Apple 签名和 notarization。

### 打包 Windows 版本

PyInstaller 不是跨平台交叉编译器，Windows 版需要在 Windows 机器上构建。

准备环境：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

直接执行：

```powershell
.\build_windows.ps1
```

脚本会：

- 安装桌面端和打包依赖
- 运行 `playwright install chromium`
- 用 `cloud_dicom_downloader.spec` 生成 `dist\Cloud DICOM Downloader\`
- 额外生成 `dist\Cloud-DICOM-Downloader-windows.zip`

如果机器上已经安装了 Inno Setup，并且 `ISCC` 在 `PATH` 中，脚本还会额外生成 `dist\Cloud-DICOM-Downloader-Setup.exe`。
安装包会自动捆绑并静默安装 `Microsoft Visual C++ 2015-2022 Redistributable (x64)`。

### GitHub Actions 自动构建 Windows 安装包

仓库已经增加工作流 [build-windows.yml](/Users/johan/panda/cloud-dicom-downloader/.github/workflows/build-windows.yml)：

- 手动触发：`Actions -> Build Windows Installer -> Run workflow`
- 自动触发：推送 `v*` 标签，例如 `v0.1.0`

工作流会在 `windows-latest` runner 上：

- 安装 Python 3.12
- 缓存 pip 依赖
- 安装 Inno Setup
- 执行 `build_windows.ps1`
- 分别上传安装包和便携版为 workflow artifact

当触发来源是 tag 时，还会自动创建或更新 GitHub Release，并上传：

- `Cloud-DICOM-Downloader-windows-<version>.zip`
- `Cloud-DICOM-Downloader-Setup-<version>.exe`

建议的发版命令：

```bash
git tag v0.1.0
git push origin v0.1.0
```

### GitHub Actions 上传到 OSS

Windows 工作流现在支持在构建完成后把产物额外上传到阿里云 OSS，适合国内分发。

先在 GitHub 仓库配置变量：

- `ALIYUN_OSS_BUCKET`：目标 Bucket 名
- `ALIYUN_OSS_ENDPOINT`：Bucket Endpoint，例如 `https://oss-cn-hangzhou.aliyuncs.com`
- `ALIYUN_OSS_REGION`：可选，Bucket 所在地域，例如 `cn-hangzhou`。如果 `Endpoint` 形如 `oss-cn-hangzhou.aliyuncs.com`，脚本也会自动推导
- `ALIYUN_OSS_PREFIX`：可选，对象前缀，例如 `cloud-dicom-downloader/windows`
- `ALIYUN_OSS_PUBLIC_BASE_URL`：可选，公开访问域名或 CDN 域名，用于 workflow summary 生成直链

鉴权二选一即可：

- OIDC：`ALIYUN_OIDC_PROVIDER_ARN`、`ALIYUN_ROLE_TO_ASSUME`
- AccessKey：`ALIYUN_ACCESS_KEY_ID`、`ALIYUN_ACCESS_KEY_SECRET`

可选：

- `ALIYUN_STS_TOKEN`：如果你使用的是临时凭证

上传规则：

- 每次构建上传到 `oss://<bucket>/<prefix>/<version>/`
- tag 构建额外覆盖 `oss://<bucket>/<prefix>/latest/`

如果没有配置这些变量和 secrets，workflow 会自动跳过 OSS 上传。

### Windows 兼容性说明

当前桌面版不是面向 Windows 7/8.x 的。

- 工作流固定使用 Python 3.12，见 [build-windows.yml](/Users/johan/panda/cloud-dicom-downloader/.github/workflows/build-windows.yml#L20)
- 桌面界面使用 PySide6，也就是 Qt 6，依赖见 [requirements-desktop.txt](/Users/johan/panda/cloud-dicom-downloader/requirements-desktop.txt)
- 部分站点依赖 Playwright 浏览器自动化，相关入口见 [crawlers/_browser.py](/Users/johan/panda/cloud-dicom-downloader/crawlers/_browser.py#L74)

如果你在 Windows 7 上看到：

- `api-ms-win-core-path-l1-1-0.dll` 缺失
- `python312.dll` 无法加载

这通常不是“少拷了一个 DLL”，而是系统版本本身不满足运行条件。当前建议的目标系统是 `Windows 10 x64 / Windows 11 x64`。

## 支持的站点

### medicalimagecloud.com

海纳医信的云影像，URL 格式为`https://*.medicalimagecloud.com:<port?>/t/<hex>`，还需要一个密码。

```
python downloader.py <url> <password> [--raw]
```

`--raw` 如果指定该参数，则下载未压缩的像素，默认下载 JPEG2000 无损压缩的图像。

> [!WARNING]
> 由于未能下载到标签的类型信息，所有私有标签将保存为`LO`类型。

### mdmis.cq12320.cn

重庆卫健委在线报告查看网站，其中的影像查看器也是海纳医信。

URL 格式：`https://mdmis.cq12320.cn/wcs1/mdmis-app/h5/#/share/detail?share_id=<hex>&content=<token>&channel=share`

命令用法与注意事项跟`medicalimagecloud.com`相同，但不需要密码。

### ylyyx.shdc.org.cn

上海申康医院发展中心的在线影像查看器，URL 格式支持以下两种：

- `https://ylyyx.shdc.org.cn/#/home?sid=<number>&token=<hex>`
- `https://ylyyx.shdc.org.cn/code.html?appid=<xxx>&share_id=<uuid>&ctype=5`

```
python downloader.py <url>
```

### zs-hospital.sh.cn

复旦大学附属中山医院所使用的影像平台，URL 格式为`https://zscloud.zs-hospital.sh.cn/film/#/shared?code=<code>`。

```
python downloader.py <url>
```

### ftimage.cn

飞图影像的医疗云影像平台，支持以下两种链接：

- `https://yyx.ftimage.cn/dimage/index.html?stm=<一长串>`
- `https://app.ftimage.cn/dimage/index.html?accessionNumber=<hex>&hsCode=<number>&date=<number>`

```
python downloader.py <url>
```

该爬虫依赖浏览器，在 Windows 上默认使用 Edge，如果启动失败请尝试运行`playwright install`改用捆绑的浏览器。

### qr.szjudianyun.com

URL 格式为`http://qr.szjudianyun.com/<xxx>/?a=<hospital_id>&b=<study>&c=<password>`，可从报告单扫码得到。

```
python downloader.py <url>
```

### ss.mtywcloud.com

明天医网的移动影像处理工作站，URL 格式为`https://ss.mtywcloud.com/ICCWebClient/Image/Viewer?AllowQuery=0&DicomDirPath=<URL>&OrganizationID=xxx&Anonymous=true&Token=xxx`。

```
python downloader.py <url>
```

### m.yzhcloud.com

URL 格式为`https://m.yzhcloud.com/w_viewer_2/?study_instance_uid=xxx&org_id=xxx`

```
python downloader.py <url>
```

### work.sugh.net

URL 格式为`https://work.sugh.net:8002/pc/auth-viewer?clinicalShareToken=<token>`

```
python downloader.py <url>
```

### medapi.dsrmyy.cn

支持以下分享入口：

- `http://medapi.dsrmyy.cn:9088/s/<share_sid>`
- `http://medapi.dsrmyy.cn:9088/sharevisit/mobile/digitalimage/index?sid=<share_sid>`

### cyemis.bjcyh.mobi

URL 格式为 `https://cyemis.bjcyh.mobi:8082/Study/ViewImage?studyId=<studyId>`

### cyemis.bjcyh.mobi

URL 格式为 `https://cyemis.bjcyh.mobi:8082/Study/ViewImage?studyId=<studyId>`
