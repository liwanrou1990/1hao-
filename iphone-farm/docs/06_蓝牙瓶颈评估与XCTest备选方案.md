# 06 蓝牙瓶颈评估与 macOS + XCTest 多设备控制备选方案

## 1. 为什么蓝牙可能是瓶颈
iPhone-use 让 Mac 扮演 **一个** BLE HID 外设，30 台 iPhone 作为 central 同时连接。Apple 不公开单机可同时维持的连接数（DTS 原话：“There is a limit, but the numbers are not published… will vary from device to device”），社区经验是常规蓝牙 3–7 台。M5 Pro 的控制器能承受多少个 central、在 30 台同时订阅 HID 通知时是否掉线，**没有任何公开数据**，必须实测。此外 iPhone-use 的 HID 待发队列是全局 FIFO，一台背压会拖慢全部。

## 2. 判定标准（逐级测试时记录）
在某级别 N 连续 10 分钟 `ifarm monitor --duration 600` 后，满足任一即判定“蓝牙成为瓶颈”：
1. `bluetoothHosts` 稳定小于在线台数（新手机配不上，或配上一台掉一台）；
2. 平均每台 `ble_drops ≥ 2` 次 / 10 分钟，或任意一台 ≥ 5 次；
3. 广播 `home` 的失败率 > 5%，且失败原因为 503 pointer 未就绪 / 蓝牙未连；
4. 单次 `tap` 的 P95 延迟 > 1.5s（HID 队列阻塞）。
记录：首次出现问题的台数 N*，即 iPhone-use 蓝牙路线的实际容量。

## 3. 缓解措施（不换架构）
- 降低 `broadcast.max_parallel`、增大 `stagger_ms`；避免 30 台同时拖动。
- 外接 USB 蓝牙适配器 **不可行**：macOS 不支持第二个蓝牙控制器用于 CoreBluetooth。
- 分摊到 2–3 台 Mac（每台 10–15 部 iPhone），用本项目的 `IFARM_API_URL` 指向各自的 iPhone-use；分组按 Mac 划分。
- 给 iPhone-use 提 patch：按 host 分队列 drain，避免头阻塞（`HIDConnectionState.pending` 改为 `[UUID: [PendingReport]]`）。

## 4. 备选方案：macOS + XCTest / WebDriverAgent（WDA）
**思路**：输入不再走蓝牙，而是每台 iPhone 上运行一个 XCTest runner（WebDriverAgent），Mac 通过 usbmuxd 端口转发用 HTTP 发 tap/swipe/type；画面仍走 USB（iPhone-use 的捕获，或 WDA 自带的 `/screenshot` 与 MJPEG 流）。这是 Appium/设备农场的成熟路线，每台一条独立 USB 通道，没有蓝牙上限。

| 项目 | iPhone-use（当前） | XCTest/WDA（备选） |
|---|---|---|
| 输入通道 | BLE HID（Mac 单外设） | USB（usbmuxd → iproxy/`ios forward` → WDA 8100+N） |
| 手机侧 | 不装任何东西 | 需安装签名的 WebDriverAgentRunner（XCTest） |
| 账号 | 无 | 免费 Apple ID 可签（手动配置，证书需在手机“设备管理”信任；iOS 16 需 Developer Mode；免费签名约 7 天失效需重签）或付费开发者账号（自动签名、1 年） |
| 越狱 | 否 | 否 |
| iOS 15/16（6s/7/8） | 支持 | 支持；非 iOS 17+，无需 tunneld |
| 画面 | USB 捕获 | WDA `/screenshot`、MJPEG 端口 9100+N；或继续用 iPhone-use/`qvh` 捕获 |
| 并发上限 | 蓝牙未知 | USB 带宽 + Mac CPU（每台一个 xcodebuild/testmanagerd 会话） |
| 文本输入 | ASCII HID | WDA `sendKeys` 支持 Unicode（中文可直接输入） |
| 短信 App | HID 操作 | WDA 可用 accessibility 树定位控件，更稳 |

### 4.1 实施要点
1. Mac 装 Xcode；clone appium/WebDriverAgent；`xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner -destination 'id=<UDID>' test` 为每台启动 runner（或用 `go-ios`：`ios runwda --udid <UDID>`，`ios image auto` 挂开发者镜像，`ios forward 8101 8100`）。
2. 每台固定端口：WDA 8100+N、MJPEG 9100+N；写入注册表（本项目 `registry.py` 可加字段 `wda_port`）。
3. `client.py` 增加 `WDAClient`：`POST /session`，`/wda/tap`（`/session/{id}/wda/tap/0`）、`/wda/dragfromtoforduration`、`/wda/keys`、`/wda/homescreen`、`/screenshot`。`actions.py`/`broadcast.py`/`monitor.py` 不变。
4. 身份一致：WDA 会话绑定 UDID，`GET /status` 返回 `sessionId` 与设备信息；注册表以 UDID 为主键，`capture_id` 退化为画面来源。
5. 30 台 runner 的资源：每个 xcodebuild 进程约 200–400 MB；48 GB 够。但 `xcodebuild test` 启动慢（30–60 s/台），建议用 go-ios 常驻。

### 4.2 风险
- 免费 Apple ID 签名 7 天失效、每个 Apple ID 只能注册有限设备（免费账号 3 台/周限制），30 台实际需要**付费开发者账号**（$99/年，100 台设备）。
- iOS 16（iPhone 8）需开启“开发者模式”，且 Apple 要求在线验证签名。
- WDA 偶发崩溃，需要监控 `/status` 并自动重启 runner（本项目 monitor 可扩展）。

## 5. 决策流程
```
第一阶段 3 台 OK → 5/10 台
   ├─ 蓝牙达标（§2 全部不满足）→ 继续 15/20/30
   └─ 蓝牙瓶颈于 N* 台
        ├─ N* ≥ 15：双 Mac 各 15 台（继续 iPhone-use）
        └─ N* < 15：输入切 WDA（本文 §4），画面保留 USB 捕获
USB 识别 < 台数 或 画面 paused 频发 → 先排除 A-832 数据口/带宽（docs/00 R1/R2），再考虑拆分 USB 树或多 Mac
```
