# Codex Pulse for vivo WATCH GT 2

一个 BlueOS 方形手表应用，用于查看 Codex 的 5 小时额度、7 天额度、今日 Token 用量和任务动态。

## 工程结构

- `src/watch3001/index.ux`：四页手表界面与显示逻辑，包括额度、用量、任务和调试信息。
- `src/shared/monitor.js`：前台页面和后台通知共用的地址、令牌、请求与响应校验。
- `src/app.ux`：15 秒后台检查与任务完成通知。
- `bridge/codex_watch_bridge.py`：电脑端只读数据桥接。
- `scripts/start-bridge.ps1`：生成本机令牌并启动或复用桥接。
- `scripts/configure-watch.ps1`：把 HTTPS 地址及本机令牌写入被忽略的本地配置。

工程未内置可公开使用的监控令牌；安装手表应用、手机与手表配对，也不会自动完成下面的数据服务配置。

图标资源位于 `src/assets/images/codex-pulse.png`，为 114×114、透明边角的圆形视觉。需要重新生成时，在 PowerShell 运行 `./scripts/generate-icon.ps1`；较大的预览图会写入 `design-reference`，不会混进手表资源目录。

## 数据怎么到手表

手表不需要连接电脑。应用使用 `blueos.network.fetch` 访问一个 HTTPS 地址：

1. 电脑上的 `bridge/codex_watch_bridge.py` 通过 Codex App Server 只读接口获取额度和今日总量；接口暂时不可用时，会从本机会话中的新鲜额度记录回退读取。运行中任务优先从 Codex Desktop 的 `thread_history_1.sqlite` / `state_5.sqlite` 只读识别，因为另起的 App Server 会把桌面进程中的任务标成 `notLoaded`。分时柱状图由本机会话记录聚合“0 点至现在”的用量，长时间持续的旧会话也会按实际更新时间纳入。
2. 将电脑端的本地接口通过你自己的 HTTPS 反向代理或 Tunnel 暴露为一个 HTTPS 地址。
3. 蓝牙版手表通过已配对手机的网络访问该地址；eSIM 版也可以使用自身移动网络。

如果真机可以访问普通公网，但对 Cloudflare 免费 Universal SSL 域名返回网络错误，可使用 `relay/aliyun-fc` 中的阿里云函数计算中转。函数计算默认 HTTPS 域名使用 RSA 证书，由它继续访问原 Cloudflare Tunnel；部署细节见该目录 README。

电脑离线时，手表会保留最后一次数据并显示“连接中断”。未配置地址时，会显示演示数据，也不会启动后台通知检查。

额度接口暂时不可用时，任务状态仍可同步；额度显示 `--`，不会误报为 100% 剩余。任务检测依赖 Codex Desktop 当前的本机数据库结构；如果未来版本改变该结构，桥接会尝试使用 App Server 状态，仍无法判断时返回 `activity.available: false`，手表显示“状态未知”，不触发误报的完成通知。

桥接会优先查找系统 `PATH` 中的 `codex` 命令；如果 Codex Desktop 没有把命令加入 `PATH`，也会自动查找 Desktop 自带的 `codex.exe`。

应用配置为每 15 秒由 BlueOS 后台定时任务检查一次状态。当曾经处于运行状态的任务变为非运行状态时，会尝试发布“Codex 任务已完成”系统通知，并将最近一次完成的标题与时间保存在手表本地，供第三页显示。第一次同步只建立任务基线，不会误报历史任务；升级后要有一次新的完成事件，第三页才会出现最近完成记录。实际后台执行间隔与主界面弹窗效果仍需在 WATCH GT 2 真机上验证。

## 1. 启动电脑端桥接

必须设置至少 32 个字符的随机 Token，避免公开地址被他人读取；桥接程序缺少 Token 时会拒绝启动：

在这台已安装 Codex Desktop 且代理位于 `127.0.0.1:7897` 的 Windows 电脑上，可直接运行 `./scripts/start-bridge.cmd`。它会仅对本次启动绕过 PowerShell 脚本策略，再调用 `start-bridge.ps1`；不会永久修改系统执行策略。桥接会在本机用户目录生成并保存随机 Token，以隐藏窗口启动，然后输出额度与任务状态；如果同一桥接已在运行，脚本会直接复用。不要把令牌文件发送给别人或提交到仓库。

代理或端口变化时可以直接传参，也可以用 `CODEX_WATCH_PROXY` 环境变量设置代理：

```powershell
.\scripts\start-bridge.cmd -ProxyUrl 'http://127.0.0.1:7897' -Port 8765
```

```powershell
$env:CODEX_WATCH_TOKEN = "请替换为随机长字符串"
python .\bridge\codex_watch_bridge.py --token $env:CODEX_WATCH_TOKEN
```

本地测试：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/status -Headers @{ "X-Codex-Watch-Token" = $env:CODEX_WATCH_TOKEN }
```

重点检查返回的 `activity.running`、`activity.count` 和 `activity.items`。修改桥接程序后必须重启正在运行的桥接进程，旧进程不会自动加载新代码。

若手表需要跨网络访问，请把 `http://127.0.0.1:8765` 通过 HTTPS Tunnel 暴露出去。不要直接开放电脑的 8765 端口到公网。

## 2. 配置手表应用

运行 `./scripts/configure-watch.ps1`，脚本会读取本机桥接令牌并生成被 `.gitignore` 排除的 `src/monitor.local.js`。前台和后台会通过共享模块读取该文件，不需要手工粘贴令牌，更不要把令牌提交到 GitHub。更换域名时可使用 `-Url 'https://你的域名/api/status'`。生成的配置内容相当于：

```js
export const MONITOR_URL = 'https://watch.lmc222.site/api/status'
export const MONITOR_TOKEN = '由本机脚本读取的随机令牌'
```

## 3. 在 BlueOS Studio 中运行

协作约定：后续由用户本人在 BlueOS Studio 中执行打包和安装；助手只负责准备代码、配置与验证步骤，不代替用户打包。

用 BlueOS Studio 打开本目录，选择 `watch-square`，然后启动实时预览或打包安装。工程预览尺寸已设置为 vivo WATCH GT 2 的 `432 × 514`。

也可以使用 Studio 内置的 `blueos-pack` 命令行构建：

```powershell
jax build --device-type watch-square
```

## 修改与自测

页面主色集中在 `src/watch3001/index.ux` 的 LESS 顶部；轮询时间、存储键和网络请求集中在 `src/shared/monitor.js`。修改电脑端桥接后，可运行：

```powershell
npm run test:bridge
```

测试不会打包或安装手表应用。桥接代码修改后需要重启正在运行的旧桥接进程，才会加载新版本。

## 接口返回格式

```json
{
  "ok": true,
  "updatedAt": 1790100000,
  "quota": {
    "fiveHour": { "usedPercent": 7, "remainingPercent": 93, "resetsAt": 1790110782 },
    "weekly": { "usedPercent": 50, "remainingPercent": 50, "resetsAt": 1790501162 }
  },
  "quotaAvailable": true,
  "activity": {
    "available": true,
    "running": true,
    "count": 1,
    "title": "正在实现 Codex Pulse",
    "items": [
      { "id": "0199...", "title": "正在实现 Codex Pulse" }
    ]
  },
  "usage": {
    "todayTokens": 23450008,
    "inputTokens": 455620,
    "outputTokens": 86996,
    "cachedTokens": 22907392,
    "bars": [4128475, 5088168, 12583770, 0, 0, 0, 0, 0, 0, 0, 0, 1649595],
    "peakLabel": "01:45",
    "currentModel": "gpt-5.6-sol",
    "source": "account+local"
  }
}
```

`bars` 始终为 12 个等宽时段，覆盖当天 0 点到本次刷新时刻；手表第二页过滤数值为零的时段，压紧显示活跃柱形，最高值使用亮绿色。第三页的运行任务来自 `activity`，最近完成记录来自手表本地存储。第四页显示最近一次连接错误、HTTP 状态、接口域名、同步时间与额度可用性。网络请求失败时会自动依次探测同域 `/health` 和普通公网，以区分 API/请求头、域名 DNS/TLS 与当前应用网络通道问题。
