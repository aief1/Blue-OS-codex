# Codex Pulse for vivo WATCH GT 2

一个 BlueOS 方形手表应用，用于查看 Codex 的 5 小时额度、7 天额度和今日 Token 用量。

## 数据怎么到手表

手表不需要连接电脑。应用使用 `blueos.network.fetch` 访问一个 HTTPS 地址：

1. 电脑上的 `bridge/codex_watch_bridge.py` 通过官方 Codex App Server 只读接口获取额度、今日总量和任务状态，并从本机 Codex 会话记录聚合“0 点至现在”的分时柱状图。
2. 将电脑端的本地接口通过你自己的 HTTPS 反向代理或 Tunnel 暴露为一个 HTTPS 地址。
3. 蓝牙版手表通过已配对手机的网络访问该地址；eSIM 版也可以使用自身移动网络。

电脑离线时，手表会保留最后一次数据并显示“连接中断”。未配置地址时，会显示演示数据，也不会启动后台通知检查。

应用每 15 秒由 BlueOS 后台定时任务检查一次状态。当曾经处于运行状态的任务变为非运行状态时，会发布“Codex 任务已完成”系统通知；即使应用已经退回手表主界面，通知仍由系统展示。第一次同步只建立任务基线，不会误报历史任务。

## 1. 启动电脑端桥接

建议设置一个随机 Token，避免公开地址被他人读取：

```powershell
$env:CODEX_WATCH_TOKEN = "请替换为随机长字符串"
python .\bridge\codex_watch_bridge.py --token $env:CODEX_WATCH_TOKEN
```

本地测试：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/status -Headers @{ "X-Codex-Watch-Token" = $env:CODEX_WATCH_TOKEN }
```

若手表需要跨网络访问，请把 `http://127.0.0.1:8765` 通过 HTTPS Tunnel 暴露出去。不要直接开放电脑的 8765 端口到公网。

## 2. 配置手表应用

分别编辑 `src/app.ux` 和 `src/watch3001/index.ux` 脚本顶部的常量。两个文件的 URL 与 Token 要保持一致：

```js
const MONITOR_URL = 'https://你的域名/api/status'
const MONITOR_TOKEN = '与 bridge 的 --token 相同'
const POLL_INTERVAL_MS = 15000
```

## 3. 在 BlueOS Studio 中运行

用 BlueOS Studio 打开本目录，选择 `watch-square`，然后启动实时预览或打包安装。工程预览尺寸已设置为 vivo WATCH GT 2 的 `432 × 514`。

也可以使用 Studio 内置的 `blueos-pack` 命令行构建：

```powershell
jax build --device-type watch-square
```

## 接口返回格式

```json
{
  "ok": true,
  "updatedAt": 1790100000,
  "quota": {
    "fiveHour": { "usedPercent": 7, "remainingPercent": 93, "resetsAt": 1790110782 },
    "weekly": { "usedPercent": 50, "remainingPercent": 50, "resetsAt": 1790501162 }
  },
  "activity": {
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

`bars` 始终为 12 个等宽时段，覆盖当天 0 点到本次刷新时刻；视觉上只有数值最高的一根使用亮绿色，其余柱子使用深绿色。
