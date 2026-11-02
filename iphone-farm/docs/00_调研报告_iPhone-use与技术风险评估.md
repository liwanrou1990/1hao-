# 00 调研报告：YueChen-C/iPhone-use 与 30 台群控的技术风险评估

> 调研日期 2026-11-02。来源为项目 README、`docs/user-guide.md`、`docs/architecture.md`、`Sources/iPhoneUse/**/*.swift`（main 分支，v1.0.1，2026-10-29 发布）以及 Apple 开发者论坛/社区资料。
> 本文所有数字凡未注明“实测”，均为文档/源码推断，**最终以 docs/07 的实机测试为准**。

## 1. iPhone-use 是什么

- 定位：“macOS 上类似 Android scrcpy 的 iPhone 操控工具”。**USB 取视频 + 蓝牙 LE HID 送输入 + 本机 HTTP API/MCP**。
- 不越狱、不在 iPhone 装任何 App、不需要 WebDriverAgent、不需要 Apple 开发者账号、Mac 与 iPhone 不需要同一 Apple ID。
- 要求：macOS 14+（Apple Silicon/Intel 通用包）、蓝牙 LE；iPhone 保持解锁、用数据线连接并“信任此电脑”、开启 **辅助功能 → 触控 → AssistiveTouch**，并在 AssistiveTouch → 设备 → 蓝牙设备 里连接名为“iPhone Use”的 Mac。
- Mac 需要 **摄像头** 与 **蓝牙** 权限（视频走 AVFoundation 的 muxed 外部设备，所以要摄像头权限）。
- 许可证 MIT；仓库 8 star / 27 commits，单人维护，**成熟度低**，需做好自行修补源码的准备（Swift 6，仅系统框架，无第三方依赖，`swift build -c release` 即可）。

### 1.1 架构（源码核对）

| 层 | 文件 | 机制 |
|---|---|---|
| 视频 | `Capture/ScreenCapture.swift` | 启动时置 `kCMIOHardwarePropertyAllowScreenCaptureDevices=1`，用 `AVCaptureDevice.DiscoverySession(deviceTypes:[.external], mediaType:.muxed)` 枚举 `modelID` 含 “iOS” 的设备；**每台一个 `AVCaptureSession`**，像素格式 32BGRA，无显式帧率设置；1 秒定时 `refresh()` + 连接/断开通知处理插拔 |
| 设备身份 | `Capture/CaptureIdentityVerifier.swift`、`USBIdentityScreenshot.swift` | 设备 ID = AVFoundation `uniqueID`；源码注释明确 “AVFoundation 的名义 UID 可能挂到另一台 iPhone 的像素上”，所以额外通过 MobileDevice 服务（隔离子进程）截图比对，核实物理身份 |
| 蓝牙 | `Bluetooth/HIDPeripheral.swift`、`HIDConnectionState.swift`、`HIDReports.swift` | Mac 作为 **一个 BLE 外设** 广播 “iPhone Use”，每台 iPhone 是一个 central；`centrals: [UUID: CBCentral]` 按 central UUID 记录；发送用 `updateValue(_, for:, onSubscribedCentrals:[central])` 定向到单台；**没有连接数上限常量**，上限由 CoreBluetooth/控制器决定 |
| 报文队列 | `HIDConnectionState.swift` | **全局单一 FIFO** `pending`，`drain()` 在第一条被拒绝的通知处停止 → 一台手机背压会阻塞后面所有手机的报文（推断，见 §3.3） |
| 配对映射 | `Devices/DevicePairing.swift`、`DeviceRegistry.swift` | 把 BLE host 与 USB 屏幕匹配靠 **探针**：移动指针到 3 个目标点，在 90×195 灰度缩略图上看哪块屏幕出现/消失光标（像素差>12、15×15 窗口、命中像素 3–150、质心偏差≤3px）；结果写 `~/Library/Application Support/iPhoneUse/pairings.json`（屏幕 ID → host UUID）；已保存的 host 不会被别的屏幕抢占；断线重连只认原机 |
| 输入 | `Input/DeviceInput.swift` | 每台手机一个串行队列；HTTP 手势同步执行；会话 token 防止重连后发旧报文 |
| API | `API/HTTPServer.swift`、`ControlAPI.swift` | 只监听 127.0.0.1，默认 7390（`IPHONE_USE_PORT`）；`--resident-quiet` 无窗口常驻 |

### 1.2 HTTP API（v1.0.1，`docs/user-guide.md#http-api` + `ControlAPI.swift`）

| 方法 | 路径 | 参数 / 说明 |
|---|---|---|
| GET | `/status` | `bluetooth`、`bluetoothHosts[{host, subscribed, session, screen{id,name}}]`、`capture{identityState, screens[]}`、`devices[]`、`unmatchedHosts[]` |
| GET | `/devices` | `{capture, devices:[{id, name, bluetooth, lockState, bluetoothState, width, height, pointer{ready,state,host,...}}]}` |
| GET | `/screenshot?device=&format=png&maxEdge=` | 默认 JPEG；响应头 `X-Device / X-Screen-Width / X-Screen-Height / X-Image-Width / X-Image-Height` |
| POST | `/tap` | `x,y`（默认像素；`fraction:true` 为 0–1；或给 `width/height/maxEdge`），`hold` 默认 0.08s |
| POST | `/swipe` | `points:[{x,y},…]`≥2，`duration` 默认 0.3s，中途不抬起 |
| POST | `/move` `/nudge` | 移动指针 / 原始相对位移（`dx,dy`，可 `click`） |
| POST | `/type` | `text`，**仅 ASCII**（美式键盘布局） |
| POST | `/key` | `key`，`modifiers[]`；Spotlight = `space`+`cmd`，输入法切换 `ctrl+space`，粘贴 `cmd+v` |
| POST | `/home` | Consumer 0x0223，唤醒但不解锁 |
| POST | `/power` | Consumer 0x0030 短按，“不保证锁定的幂等指令” |
| POST | `/match` | `force`、`probe`：重新匹配屏幕与蓝牙 host |
| POST | `/pair` | `device`、`host`：人工指定 host → 屏幕 |
| POST | `/calibration/auto` | `verifyOnly`；不带 device 时处理所有设备 |
| POST | `/calibration/reload` | 热加载校准 |

- 设备选择：POST 用 body `device`，GET 用 `?device=`。服务端解析顺序 **id 精确 → 名称精确 → id 前缀**；不带 device 且多台在线时报错。
- 成功响应 `{"ok":true,"device":"<id>"}`；错误码：404 路径/方法；409 指针忙（窗口拖拽、校准中）；503 指针未就绪/无画面；其余 400/500 文本。
- 无 MJPEG 推流端点；`/viewer` 浏览器预览约 2 秒刷新、最高 5 FPS、长边 1200px。

### 1.3 对本项目 7 个目标的覆盖度

| 目标 | 覆盖 | 说明 |
|---|---|---|
| 1 独立控制每台 | ✓ | 按完整 USB 设备 ID 路由，每台独立窗口与输入队列 |
| 2 鼠标点击/滑动/键盘 | ✓（ASCII） | 中文需切换手机输入法或通用剪贴板 |
| 3 多台实时画面 | △ | 每台一个原生窗口（30 Hz 刷新）；HTTP 只有截图，无视频流；30 个窗口的显示与 USB 2.0 带宽需实测 |
| 4 分组同步操作 | ✗ 需自建 | 本项目 `broadcast.py` 实现 |
| 5 独立操作原生短信 App | ✓ | 单机通过 HID 操作信息 App；无 API 读取短信内容 |
| 6 断线检测与恢复 | △ | 有插拔检测、会话 token、`/match` `/pair`；无告警与策略，本项目 `monitor.py` 实现 |
| 7 设备 ID 与目标一致 | ✓ 基础 + 本项目加固 | 服务端有像素级身份核验；本项目再加名称/UDID/host 三重校验与回显检查 |

## 2. 硬件与系统事实

### 2.1 Sipolar A-832
- 多家卖家资料一致：**USB 2.0**（480 Mbps）、32 口、工业级铝壳、双 12V 10A 电源（约 240 W），每口 5V 1.5A（部分页面写 5V 1A），过流/过压/短路保护，免驱。
- **分歧（必须到货实测）**：一俄语卖家页面称 “30 口充电 + 2 口数据同步”，而多数页面称 32 口均可同步。若手里的 A-832 是前者，30 台 USB 识别根本不可能，需换 A-213P 一类明确“全数据口”的机型。
- 上行口数量未知（1 或 2 条上行线）。**整条 USB 2.0 树共享 480 Mbps（实际有效约 280–320 Mbps）**。
- 本容器无法访问 sipolar.com / alibaba.com（网络白名单），上述为搜索摘要，未能核对官方页。

### 2.2 USB 2.0 带宽估算（需实测）
- iPhone 屏幕捕获走 QuickTime 同款协议，设备端 H.264 编码后经 USB 送出。经验值每台 **2–8 Mbps**（静态桌面更低，视频/滚动更高），再加 usbmuxd 控制流量。
- 30 台 × 5 Mbps ≈ 150 Mbps，理论可行；但 USB 2.0 hub 的 TT（Transaction Translator）调度、每台的等时/批量传输以及 A-832 内部级联（多级 hub 芯片）都会拉低有效带宽。**只能逐级实测**，并观察 `/screenshot` 延迟、画面“paused”提示次数。
- macOS 每条 USB 树最多 127 个地址，A-832 内部级联 hub 本身会占用若干地址；30 台 + 内部 hub 预计不超限，但要看 `system_profiler SPUSBDataType` 实际枚举。

### 2.3 蓝牙 LE 并发（最大风险）
- iPhone-use 让 **Mac 作为一个 BLE 外设**，30 台 iPhone 作为 central 同时连接它。Apple 明确“同时连接数有限且不公开、因机型而异”（开发者论坛 DTS 回复），社区经验 Mac 常规蓝牙“7 台规范上限、3–4 台实用”。BLE 外设角色能接受多少 central，**没有任何公开数字**。
- 另有开发者报告 M2 MacBook Air（BCM_4387 控制器）上 `CBPeripheralManager` 掉线而 M1 正常——控制器固件差异很大，**M5 Pro 必须实测**。
- 即便连接数够，HID 报文队列是全局 FIFO：任何一台背压都会阻塞其他台（源码推断）。多台同时拖动时延迟会叠加。
- **判定蓝牙是瓶颈的标准（docs/06）**：某级别下 `bluetoothHosts` 数稳定小于在线台数，或 10 分钟观察期内掉线 ≥ 2 次/台，或新增设备配对导致老设备掉线。

### 2.4 iPhone 6s/7/8 系统版本
- 6s / 7 最高 iOS 15.8.x；8 最高 iOS 16.7.x。AssistiveTouch 蓝牙鼠标自 iOS 13 起支持；三款都能配对。
- 都不是 iOS 17+，所以 pymobiledevice3/go-ios 不需要 `tunneld`，开发者磁盘镜像可直接挂载（备选方案有利）。
- 老机型电池老化严重：USB 2.0 口 5V 1.5A 够充电，但 30 台同时亮屏常驻可能发热，建议降低亮度、关闭自动锁定、定期轮换。

### 2.5 其他前提
- 画面捕获要求手机 **保持解锁且亮屏**；需关闭“自动锁定”（设置 → 显示与亮度 → 自动锁定 → 永不）并尽量不用锁屏密码（或由人工解锁）。
- 竖屏为主，横屏/旋转不保证。
- iPhone-use 释放包是 ad-hoc 签名、未公证，首次打开要在“系统设置 → 隐私与安全性”放行。

## 3. 风险清单与应对

| # | 风险 | 可能性 | 影响 | 应对 |
|---|---|---|---|---|
| R1 | A-832 只有 2 个数据口 | 中 | 致命 | 到货先用 `scripts/preflight_mac.sh` 看 `system_profiler` 能否枚举到插在每个口上的 iPhone；不行换全数据口机型 |
| R2 | USB 2.0 带宽不够 30 路视频 | 中 | 高 | 逐级测截图延迟/“paused”次数；必要时拆成 2–3 条上行（A-832 若有双上行）或分两台 Mac |
| R3 | BLE central 数量上限 < 30 | **高** | 致命 | 第一阶段就从 3→5→10 测蓝牙；超限则切换 docs/06 的 XCTest/WDA 方案承担输入，USB 仍用于画面 |
| R4 | HID 全局队列头阻塞 | 高 | 中 | 群控层限制并发（`max_parallel`）、错峰（`stagger_ms`）；必要时改源码为按 host 分队列（PR 上游） |
| R5 | 蓝牙 host ↔ 屏幕交叉绑定 | 中 | 高 | 注册表记录 host 一对一；动作前核对 `pointer.host`；`/pair` 人工纠正；探针验证 |
| R6 | AVFoundation uniqueID 漂移到另一台像素 | 低 | 高 | 服务端已有像素身份核验；本项目再比对名称 IFxx 与 UDID |
| R7 | 手机锁屏/息屏 → 无画面无控制 | 高 | 中 | 自动锁定=永不，去掉密码或人工解锁；monitor 对 LOCKED/NO_FRAME 告警 |
| R8 | 单人维护项目停更/缺功能 | 中 | 中 | 源码构建，自行 patch；关键逻辑都在本项目 Python 层，可替换后端 |
| R9 | 30 个原生窗口 + 30 路解码占用 Mac | 中 | 中 | `--resident-quiet` 无窗口常驻，画面由本项目按需截图；48GB 内存充足，关注 CPU/GPU |

## 4. 结论与下一步

1. **iPhone-use 适合作为第一阶段（3 台）与第二阶段（广播）的后端**：API 简单、设备 ID 路由明确、无越狱与账号负担。
2. **30 台能否全部落在 iPhone-use 上，取决于两个实测：A-832 的数据口数量/带宽与 M5 Pro 的 BLE central 上限。** 两者任一失败，分别走“拆分 USB 树/多 Mac”与“XCTest/WDA 输入”路线（docs/06）。
3. 本项目交付的 `ifarm` 群控层对后端做了抽象（`client.py` 只封装 HTTP），日后若换 WDA，只需替换 client 与 actions，注册/广播/监控/测试报告不变。

## 来源
- https://github.com/YueChen-C/iPhone-use （README、docs/user-guide.md、docs/architecture.md、Sources/iPhoneUse/*）
- https://github.com/YueChen-C/iPhone-use/releases （v1.0.0 2026-10-21、v1.0.1 2026-10-29）
- Apple 开发者论坛《Maximum number of Bluetooth peripheral connections》《BLE 外设连接数量限制》（DTS：有上限但不公开）
- Apple 开发者论坛《What is the programmatic way to capture screen from a connected iOS device》（AVCaptureDevice muxed）
- codejam.info《USB iPhone screen recording in Swift》（kCMIOHardwarePropertyAllowScreenCaptureDevices）
- https://github.com/danielpaulus/quicktime_video_hack （iOS 屏幕 USB 协议的跨平台实现，作者称并行多台跑 16 小时）
- https://github.com/danielpaulus/go-ios 、Appium XCUITest driver 文档（real-device-config / provisioning-profile）
- Sipolar A-832 卖家页面（deviceparts.com、amperkot.ru、sim3gviettel.com 等；官方站与 alibaba 在本环境不可达）
