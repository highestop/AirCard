# AirCard

个人使用的 Apple Wallet 卡面定制工具。界面运行在浏览器中，设备操作由 Mac 上的 Python 服务和原生 USB 工具完成：

```text
浏览器 → http://127.0.0.1:8765 → Python → macOS 原生设备工具 → USB iPhone
```

## 开发与运行环境

- **Mac，macOS 14 或更新版本**，Apple Silicon 或 Intel。原生工具依赖 macOS 的 MobileDevice / AirTrafficHost 私有框架，不能直接在 Windows 或 Linux 上使用。
- **Python 3.9+**，不需要安装 pip 包。
- **Xcode Command Line Tools**，用于编译两个 Objective-C 设备工具；不需要完整 Xcode。未安装时运行 `xcode-select --install`。
- 现代浏览器，以及通过 USB 连接、已解锁并信任这台 Mac 的 iPhone。

卡面写入沿用现有 iOS 18+ 实现。私有设备接口与 Wallet 日志会随系统版本变化，支持范围仍以实际设备验证为准。

## 启动

```sh
git clone https://github.com/highestop/AirCard.git
cd AirCard
./start.sh
```

脚本会按需编译 `build/device_helper` 和 `build/airtraffic_host`，然后启动本地服务并打开浏览器。保持终端运行；按 `Ctrl+C` 停止服务。如果正在写入，会先等待当前卡片完成清理，再退出。

已经编译过时可以直接运行：

```sh
python3 aircard.py
# 自选端口，不自动打开浏览器
python3 aircard.py --port 8766 --no-browser
```

服务只监听 `127.0.0.1`，请使用终端显示的完整地址。刷新或关闭网页不会中断后台操作；重新打开相同地址即可继续查看。同一个数据目录只能运行一个服务实例；已经启动时直接打开现有页面。如果端口被其他程序占用，请换端口。

## 使用流程

1. 连接 iPhone，解锁并信任 Mac，在页面中选择设备；需要时点击「刷新设备」或「重新连接」。
2. 点击「扫描卡片」。在 iPhone 中打开 Wallet 卡片，或双击侧边键、认证后切换付款卡。
3. 本次扫描确认的卡片会出现在页面中。可逐张选择或拖入图片，也可选中多张后批量分配同一图片。
4. 点击「停止扫描」，检查选中卡片和预览，再点击写入按钮。
5. 写入完成后，在 iPhone 中强制退出并重新打开 Wallet 查看结果。

图片在 Mac 上转换为 **1536 × 969 PNG**，按比例填满并居中裁切。支持系统图像工具可解码的常见格式，包括 PNG、JPEG、HEIC；单文件最多 30 MiB、48 MP、单边 16,384 像素。也可使用页面中的[卡面编辑器](docs/artwork.md)调整构图，再直接应用到选中的卡片；仍可下载 PNG。

### 保留的交互

- 设备选择、刷新、重连；扫描开始、停止与诊断。
- 卡面预览、选择/拖放图片、批量分配、内置裁切编辑器、全选/取消选择、复制 ID。
- 清除图片、移除本地记录、清空本地列表；这些操作不会删除手机中的卡片，也不会恢复原卡面。
- 手动保存 ID；未经本次扫描确认的记录保持隐藏，不能写入。
- 默认只写入选中且图片发生变化的卡片；全部未变化时可重新写入全部选中卡片。
- 写入进度、成功/错误反馈、日志折叠/清空/自动滚动。
- Wallet 本地缓存诊断、名称匹配、未确认卡片提示和按设备保存设置。

### 扫描和缓存

付款卡使用 NFC 激活事件与卡片资源路径进行识别。当前日志中的 ID 与某个远端设备缓存唯一匹配后，才补充该缓存中的其他付款卡。会员卡、票券等需要分别打开确认。

Mac 缓存数量不是手机卡片总数，页面顺序也不是 Wallet 显示顺序。「读取缓存」只重读 Mac 上现有元数据，不会强制 iCloud 同步。扫描无结果时，检查日志是否出现 `Connected to the unified device log stream`，然后重新连接、解锁并扫描。系统日志中的 `<private>` 无法恢复。

详见[卡片识别和诊断](docs/wallet-discovery.md)、[连接与扫描排查](docs/troubleshooting.md)。

## 本地数据

默认保存在 `~/Library/Application Support/AirCard/`：

- `state.json`：每台 iPhone 的卡片、选择状态、图片引用和成功写入签名。
- `artwork/`：导入图片的本地副本；移动原始文件不会影响新上传的图片。

第一次启动会读取旧版偏好设置和旧 JSON 卡片列表，并复制可用图片；旧文件保持原样。迁移记录仍须通过本次扫描确认。清空后的列表不会在重启时重新导入。找不到旧图片时，页面提示重新选图。

可用 `--data-dir /path/to/data` 指定独立数据目录。页面不连接外网、不使用 CDN，也不会把图片或设备日志上传到云端。本地服务校验 Host、Origin 和会话令牌；不要通过反向代理暴露给其他设备。

## 仓库结构与验证

- `web/`：无框架的 HTML / CSS / JavaScript 界面。
- `aircard_server.py`：仅回环地址的 HTTP 服务。
- `wallet_service.py`、`wallet_store.py`、`wallet_discovery.py`：设备状态、持久化、扫描和任务调度。
- `wallet_catalog.py`：Mac Wallet 元数据读取。
- `image_processing.py`：基于 macOS `sips` 的图片标准化。
- `aircard_backend.py`、`apply_card_skin.py`、`card_assets.py`：卡面写入、资源生成与缓存清理。
- `native/`：macOS 原生设备通信工具源码。

```sh
make all
python3 -m unittest discover -s tests -v
node --check web/app.js
node --test web/tests/*.test.cjs
```

Node.js 仅用于前端检查，不是运行依赖。自动化测试覆盖本地服务和模拟设备链路；真实 iPhone 上的最终显示效果需要实际写入验收。
