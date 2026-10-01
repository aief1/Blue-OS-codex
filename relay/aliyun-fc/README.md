# 阿里云函数计算 RSA 中转

该函数让不兼容 Cloudflare ECDSA 边缘证书的手表，通过阿里云函数计算默认的 RSA HTTPS 域名访问现有桥接：

```text
WATCH GT 2 -> *.cn-hangzhou.fcapp.run -> watch.lmc222.site -> 本机桥接
```

函数只开放两个路径：

- `GET /health`：不含敏感数据，用于手表连通性检查。
- `GET /api/status`：必须携带与桥接一致的 `X-Codex-Watch-Token`。

## 控制台创建步骤

1. 打开阿里云控制台，进入“函数计算 FC 3.0”。
2. 地域选择“华东 1（杭州）”，创建一个事件函数。
3. 函数名填写 `codex-watch-relay`，运行时选择 `Node.js 20`，处理程序填写 `index.handler`。
4. 将本目录的 `index.js` 全部复制到在线编辑器的 `index.js`，然后部署代码。
5. 在函数“配置 > 环境变量”中添加：
   - `UPSTREAM_URL`：现有 Cloudflare Tunnel 的完整 `/api/status` 地址。
   - `WATCH_TOKEN`：电脑桥接当前使用的 Token。不要把值写进代码、截图或 Git。
6. 创建 HTTP 触发器：允许 `GET`，认证方式选择“无需认证”，保持“公网访问 URL”开启。业务代码仍会校验 Token。
7. 复制形如 `https://xxxx.cn-hangzhou.fcapp.run` 的公网 URL。

## 验证

先在浏览器打开：

```text
https://xxxx.cn-hangzhou.fcapp.run/health
```

应返回：

```json
{"ok":true,"relay":"aliyun-fc"}
```

再在 PowerShell 测试受保护接口：

```powershell
$token = Get-Content "$env:LOCALAPPDATA\CodexPulse\bridge-token.txt" -Raw
Invoke-RestMethod 'https://xxxx.cn-hangzhou.fcapp.run/api/status' `
  -Headers @{ 'X-Codex-Watch-Token' = $token.Trim() }
```

成功后运行项目的配置脚本，将 URL 换成函数计算地址：

```powershell
.\scripts\configure-watch.ps1 `
  -Url 'https://xxxx.cn-hangzhou.fcapp.run/api/status'
```

之后由用户在 BlueOS Studio 中重新打包和安装。不要停掉电脑桥接或 Cloudflare Tunnel，中转仍需访问它们。

## 安全与费用

- HTTP 触发器虽选择“无需认证”，`/api/status` 仍由函数代码校验 Token；阿里云自身的签名认证不适合手表直接调用。
- 函数最小实例数保持 `0`，避免空闲费用。
- 函数计算是按量计费服务；首次开通可能有试用额度，仍应在阿里云设置费用预警并查看实际账单。
