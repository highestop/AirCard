# 连接与扫描排查

## 找不到 iPhone

1. 用 USB 连接，解锁 iPhone，并确认已经信任这台 Mac。
2. 点击「刷新设备列表」或「识别诊断」中的「重新连接」。多台设备连接时，明确选择目标 iPhone。
3. 检查原生工具是否已编译：运行 `make all`。需要单独检查枚举时，可运行 `build/device_helper list`；输出包含设备标识，请保留在本机。

原生发现使用 `AMDeviceNotificationSubscribeWithOptions` 的 USBMux 传输。曾在 macOS 27 上验证：同时启用 `NotificationOptionEnableRemoteXPC` 会让已配对的 USB iPhone 消失；因此当前实现不启用该选项。`tests/test_device_discovery.py` 和对应原生测试防止重新引入这个问题。

设备选择不会在目标失联时自动切换到另一部 iPhone。重新连接后，仍需在页面明确选择当前设备。

## 已连接但扫描无卡片

1. 点击「扫描卡片」，展开「操作日志」。
2. 确认出现 `Connected to the unified device log stream`。
3. 在 iPhone 上打开 Wallet，逐张打开需要的卡片。付款卡也可通过双击侧边键、认证后切换；会员卡可能需要在 Wallet 内直接打开。
4. 若扫描器退出，重新连接、解锁并再次扫描。若激活了付款卡但无法映射，可点击「读取缓存」后重试。

扫描使用 `com.apple.os_trace_relay`，读取包含 Info/Debug 事件的统一日志。旧 `com.apple.syslog_relay` 在已验证的 iOS 18.6.2 环境中会遗漏含卡片路径的资源查询消息。原生测试覆盖分片、合并帧、字节序、畸形长度、截断记录和多行路径。

## 有些卡始终不显示

- **只有付款激活 ID，没有卡片文件 ID**：二者不是同一个标识。需要 Mac 的 Wallet 缓存提供精确映射；缺少映射时，不能按名称、卡号尾号或卡片位置猜测。
- **日志字段为 `<private>`**：隐藏内容无法从当前日志恢复。
- **仅出现在 `passIDs[global]` / Express Mode 配置中**：这类记录可能反映配置而不是当前卡片状态，当前解析器不把它当作本次确认。
- **Mac 缓存过期或不完整**：「读取缓存」只读取现有文件，不会强制 iCloud 刷新。缓存数量不是手机卡片总数。
- **重启后保存记录隐藏**：属于正常行为，必须通过当前设备的新一轮扫描确认。

更多身份核对和持久化规则见 [卡片识别](wallet-discovery.md)。

## 验证范围

保留的设备验证记录包括：iPhone 15 Pro / iOS 18.6.2 的卡片路径扫描、iPhone 16 Pro / iOS 27.0 的设备发现，以及 Web 迁移后 iPhone 14 Pro / iOS 27.0.1 的设备发现和日志扫描启动/停止。这些只证明各次连接或扫描结果，不代表所有系统版本或实际卡面写入都已验收。

排查时记录系统版本、设备型号、仓库提交和错误摘要即可；不要将完整设备日志、卡片 ID、会员号或二维码提交进仓库。
