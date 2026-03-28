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
  * [cloud.wehzsy.com](#cloudwehzsycom)
  * [medapi.dsrmyy.cn](#medapidsrmyycn)
  * [cyemis.bjcyh.mobi](#cyemisbjcyhmobi)
  * [film.radonline.cn](#filmradonlinecn)

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
如果要指定版本号，也可以执行 `./build_macos.sh 0.1.0`，生成带版本号的 dmg 文件名。
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

便携版 `.zip` 现在会一并带上常见的 VC++ 运行库 DLL，减少在干净 Windows 机器上直接解压运行时缺少 `QtCore` 依赖的概率。对普通用户分发时，仍然优先建议使用安装包。

如果机器上已经安装了 Inno Setup，并且 `ISCC` 在 `PATH` 中，脚本还会额外生成 `dist\Cloud-DICOM-Downloader-Setup.exe`。
安装包会自动捆绑并静默安装 `Microsoft Visual C++ 2015-2022 Redistributable (x64)`。

### GitHub Actions 自动构建桌面安装包

仓库已经增加工作流 [build-windows.yml](/Users/johan/panda/cloud-dicom-downloader/.github/workflows/build-windows.yml)：

- 手动触发：`Actions -> Build Desktop Packages -> Run workflow`
- 自动触发：推送 `v*` 标签，例如 `v0.1.0`

工作流会分别在 `windows-latest` 和 `macos-latest` runner 上：

- 安装 Python 3.12
- 缓存 pip 依赖
- 执行 `build_windows.ps1`
- 执行 `build_macos.sh`
- 上传 Windows 安装包、Windows 便携版和 macOS dmg 为 workflow artifact

当触发来源是 tag 时，还会自动创建或更新 GitHub Release，并上传：

- `Cloud-DICOM-Downloader-windows-<version>.zip`
- `Cloud-DICOM-Downloader-Setup-<version>.exe`
- `Cloud-DICOM-Downloader-macOS-unsigned-<version>.dmg`

建议的发版命令：

```bash
git tag v0.1.0
git push origin v0.1.0
```

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


### cloud.wehzsy.com

杭州市第一人民医院影像查阅系统（微影），注意医院公众号分享时，有效期设置为1年，允许下载需勾选，否则会未授权无法下载，URL 格式为 `http://cloud.wehzsy.com:9003/PC/#/share_report?tel=<TEL>&pid=<PATIENT_ID>&rid=<RESULT_ID>&download=1&forward=1&Expires=<EXPIRES>&Signature=<SIGNATURE>`，下载为DICOM(自带浏览器)，查看需自行解压Zip后使用RSVS.exe查看，DICOM文件在IMAGE目录里

```
python downloader.py <url>
```

### medapi.dsrmyy.cn

支持以下分享入口：

- `http://medapi.dsrmyy.cn:9088/s/<share_sid>`
- `http://medapi.dsrmyy.cn:9088/sharevisit/mobile/digitalimage/index?sid=<share_sid>`

### cyemis.bjcyh.mobi

URL 格式为 `https://cyemis.bjcyh.mobi:8082/Study/ViewImage?studyId=<studyId>`


### pacs.ydyy.cn

支持以下入口：

- `https://pacs.ydyy.cn:8860/M-Viewer/shortserver/<shortUrl>`
- `https://pacs.ydyy.cn:8860/M-Viewer/#/phone-visible/<bussId>?...`
- `https://pacs.ydyy.cn:8860/M-Viewer/m/2D?tenantId=default&userId=<userId>&checkserialnum=<bussId>`

`phone-visible` 分享链接需要输入身份证后四位，程序会先调用站点验证接口，再自动切到移动端 XML/WADO 下载链路。

### film.radonline.cn

支持以下入口：

- `https://film.radonline.cn/web/fore-end/index.html#/check-detail-share?...`
- `https://film.radonline.cn/webImageSyn/activeImage.html?mergeParameters=...#/`

```
python downloader.py <url>
```

该站点的原图下载逻辑运行在网页查看器内，因此依赖 Playwright 浏览器自动化。程序会按序列逐个触发站点内建的原图打包下载，再在本地解压成 `.dcm` 文件。
