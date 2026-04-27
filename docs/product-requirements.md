# 产品需求文档

## 2026-04-16 efilmcloud 断点续传重试

### 背景

用户通过桌面版下载 `*.efilmcloud.com` 链接时，单张 DICOM 的 WADO 请求可能中途卡住或断开。当前实现失败后会删除临时文件，并在再次启动时创建新的序列目录，导致用户只能整套重新下载。

### 目标

当 `efilmcloud` 下载任务中断后，用户再次点击重试，应当从已有进度继续，而不是从头下载整套检查。

### 范围

- 支持桌面版对同一链接、同一保存目录执行失败后重试。
- 支持跳过已经完整落盘的 DICOM 文件。
- 仅要求 `efilmcloud` 站点启用该行为，不扩大到所有站点作为本次交付目标。

### 功能要求

1. 下载中断时，再次启动同一任务应继续复用已有保存目录中的已完成文件。
2. 再次启动同一下载任务时，程序必须复用原有序列目录，不能自动切换到新的 `(1)` 目录。
3. 若目标 `.dcm` 文件已经存在，必须判定为已完成并跳过，不重复下载。
4. 若存在未完成的 `.part` 文件，程序可以直接丢弃，并在重试时重新下载该单个文件。
5. 桌面版失败后的主操作按钮和状态文案应明确提示用户可执行重试，并说明会从断点继续。

### 验收标准

1. 人工构造一个已存在的 `.part` 文件时，再次下载会丢弃它并完整重下该单个文件。
2. 已经写完的 `.dcm` 文件在重试时不会再次请求网络。
3. 同一保存目录下重试时会继续写入原序列目录，而不是新建副本目录。
4. GUI 下载失败后，界面可见“重试下载”入口或等价提示，用户无需理解内部机制即可继续任务。
---

## 2026-04-27 gjwlyy 改用 Playwright JS Hook 像素截获

### 背景

原实现通过 Playwright 打开影像查看器，模拟点击"导出 → PNG"按钮，将每帧导出为 PNG，再包装成 Secondary Capture DICOM 写盘。该方案存在以下问题：

1. PNG 是有损（或无损但 8-bit）中转格式，16-bit CT 灰度值在写入 PNG 前已被查看器映射到 8-bit，像素信息不完整。
2. 生成的是 Secondary Capture（OT 模态），而非原始 DICOM，不符合医学存档标准。
3. 依赖 UI 控件（"导出"按钮、"PNG"选项），对 UI 变更脆弱。
4. 站点实际返回 `CLOCLHAAR`/`application/clowrapper` 自有压缩数据，不能通过网络响应直接拿到 DICOM P10。

### 目标

通过页面内 JS Hook 复用 Eunity 查看器自身的 HAAR 解码器，截获反小波变换后的完整像素 tile，在 Python 侧拼装为 DICOM 文件，不再经过 PNG/JPEG 中转。

### 方案

- **JS Hook**：在 Dart/JS 应用运行前注入脚本，包装 `Module.inverseHaar16FromByteArrays`、`Module.inverseHaar8FromByteArrays`、`Module.inverseHaarFromByteArrays` 和 `Module.inverseHaarColorSeparatePlaneFromByteArrays`。
- **元数据补齐**：同时 hook `createNewModalityLutFromRescale` 和 `createNewLutInMemoryHandle`，获取 `BitsStored`、有符号标记、`RescaleSlope`、`RescaleIntercept`、窗宽窗位。
- **tile 拼接**：Python 侧按解码顺序收集完整分辨率 tile，按行优先拼成 `Rows x Columns` 像素缓冲。
- **单视口加载**：打开查看器时强制追加 `format=1upSeriesBox`，避免默认 4 宫格同时解码多个序列导致像素归属混淆。
- **导航触发加载**：通过底部序列缩略图切换序列，保留键盘导航逻辑（`ArrowDown`/`Home`/`End`）以驱动查看器逐帧解码。
- **DICOM 重建**：使用 pydicom 写入 `CT/MR/... Image Storage`，保留 Study/Series/SOP UID、患者基础信息、像素位深和 Rescale 参数。

### 不在范围内

- 不修改其他站点的爬虫。
- 不承诺生成源站原始 DICOM 的逐字节副本；当前结果是由完整像素和可获取元数据重建的 DICOM。
- 不逆向 `CLOCLHAAR` 的离线解码算法，仍依赖浏览器查看器完成解码。

### 验收标准

1. 下载结果为合规 DICOM P10 文件，SOPClassUID 非 SecondaryCaptureImageStorage。
2. 16-bit CT 像素数据完整（不经 8-bit PNG 降级）。
3. 不再有 PNG 导出 UI 交互（不点击"导出"按钮）。
4. 单元测试覆盖 tile 拼接、完整分辨率筛选和 DICOM 构建。
5. 在线烟测至少覆盖连续两帧导航，避免只验证首帧缓存。
