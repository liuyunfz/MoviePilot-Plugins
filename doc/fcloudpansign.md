# F-Cloudpan 签到（MoviePilot V2）

通过 F-Cloudpan 的第三方应用 OAuth 授权，定时签到并查看当前账号的云盘匿名昵称、头像、积分余额、VIP、连续签到、累计签到和近 14 日记录。每个插件实例绑定一个用户；更换授权会清除旧账号展示和执行记录。

界面参考 [madrays/MoviePilot-Plugins 的蜂巢论坛签到](https://github.com/madrays/MoviePilot-Plugins/tree/main/plugins/fengchaosignin) 的原生 Vuetify 分区卡片、资料卡和签到记录布局。本插件的授权及签到实现独立编写。

## 安装

可在 MoviePilot 添加本仓库并安装 **F-Cloudpan 签到**。本地调试可将 `plugins/fcloudpansign` 整个目录复制到 MoviePilot 的 `app/plugins` 下，再重载插件；同目录提供的 `fcloudpansign.zip` 解压后得到相同文件夹。必须同时保留 `__init__.py`、`client.py` 和 `ui.py`。

## 注册应用

1. 在 F-Cloudpan **后台 → 第三方应用**创建应用。
2. 权限仅选择 `account:read`、`account:write`。
3. 登记精确回调地址：`https://你的MP域名/api/v1/plugin/FCloudpanSign/oauth/callback`。不添加 MoviePilot API Key 或其他查询参数。
4. 将 Client ID 和仅展示一次的 Client Secret 填入插件配置。密钥保存在这台 MoviePilot 的服务端，不应打包分发或共享到公共仓库。

当前 F-Cloudpan 仅支持服务端机密客户端和精确登记的回调地址，因此每台 MoviePilot 实例需要登记自己的回调并安全配置应用凭据。本插件不内置通用共享密钥。若未来需要面向所有用户的统一安装即授权体验，需要服务端增加相应客户端/回调管理能力。

## 连接账号

1. 填写 **F-Cloudpan 站点根地址**、**MoviePilot 浏览器访问根地址**及应用凭据，选择“保存后生成授权入口”并保存。
2. 打开插件详情，点击“前往云盘授权”。
3. 在同一浏览器登录云盘并确认权限，完成后自动回到 MoviePilot 授权结果页。
4. 关闭结果页，重新打开插件详情即可看到资料。插件不会在授权回调时自动签到。
5. 启用定时签到，或选择“保存后立即签到一次”。“立即刷新资料”只读取信息。

地址使用 HTTPS，不包含子路径、查询参数或用户密码。回调由用户浏览器访问，不要求云盘服务器主动连接 MoviePilot；浏览器须能访问登记的 MoviePilot 地址。本机开发允许 `http://localhost:端口`、`http://127.0.0.1:端口`，插件到云盘的地址也必须能从 MoviePilot 进程访问。

需要支持插件 `allow_anonymous` API 的 MoviePilot V2。授权入口与回调不使用 MP API Key，而是验证一次性入口、随机 state、issuer、浏览器 HttpOnly Cookie 和 PKCE。若回调返回 MP 的 401，请升级 MoviePilot；不要将 API Key 添加到回调地址。反向代理需要把 `/api/v1/plugin/FCloudpanSign/oauth/` 转发到 MoviePilot，并避免记录该路径的查询字符串（含一次性授权参数）。

## 签到与资料

- 普通签到：使用站点固定奖励，默认模式。
- 随机签到：使用站点随机奖励范围，**可能扣减积分**，由用户显式选择。
- 默认周期 `30 9 * * *`，使用 MoviePilot 时区；随机延迟 0–300 秒。云盘每天的签到边界由北京时间确定。
- 先查询服务端当天状态，已经签到不再提交。提交超时或服务端 5xx 时回查记录，不盲目重复 POST。
- 页面读取本地缓存，打开页面不请求云盘；显示最后同步时间，跨天后提示更新今日状态。VIP 到期及页面时间统一显示为北京时间。
- 最多保存 500 条执行记录，默认保留 30 天，详情显示最近 50 条。近 14 日签到卡来自云盘 API。
- 配置保存会重置所有一次性开关，保留其他配置。关闭定时签到仍可明确选择执行一次或刷新资料。
- 可选通过 MoviePilot 已配置的消息渠道发送签到结果；默认关闭。

## 授权生命周期

授权码一次性使用、5 分钟有效；插件入口 10 分钟有效。访问令牌有效期 1 小时，在到期前 90 秒或收到 401 后串行轮换。刷新授权从首次交换起最长 30 天，不滑动延期；到期必须由用户重新确认。

刷新前持久化“正在刷新”标记，成功后把新令牌对作为一个记录保存。超时、进程中断或响应无效后停止使用旧刷新令牌，提示重新授权，避免重放导致整个授权被撤销。这里只支持单个 MoviePilot 进程；不要复制同一授权存储到多个容器同时运行。

“保存后撤销并解除授权”会先调用 `/oauth/revoke`，成功后清理本地授权、资料和历史；撤销未确认则停止定时账户操作并保留撤销凭据，用户可再次撤销或在云盘个人中心撤销。更换站点、MP 地址、Client ID 或 Secret 会清除本地旧授权，请先撤销旧授权；清除本地状态不代表远端授权已撤销。

配置和令牌存放在 MoviePilot 的配置/插件数据库中，使用 MoviePilot 自身的访问控制和备份保护。本插件不把令牌放入表单、详情、历史或通知，不读取用户 Cookie，不关闭 TLS 校验，不跟随接口重定向，也不继承系统代理或 `.netrc` 凭据。

## 接口契约与排查

| 用途 | 接口 | 鉴权 / 响应 |
|---|---|---|
| 用户同意 | `GET /oauth/authorize` | code + PKCE S256，回调校验 state 与 iss |
| 交换/刷新 | `POST /oauth/token` | HTTP Basic + form-urlencoded |
| 账户 | `GET /oauth/account` | Bearer；顶层 `sub/name/avatar/points/vip_level/vip_expires_at` |
| 签到状态 | `GET /api/check-in` | account:read；`code: 0, data: {...}` |
| 签到 | `POST /api/check-in` | account:write；JSON `mode: STANDARD / LAS_VEGAS` |
| 撤销 | `POST /oauth/revoke` | HTTP Basic + form-urlencoded |

403：检查两项账户权限及应用/用户状态。401 或 30 天到期：重新授权。429：稍后运行。页面显示“刷新结果未知”：重新授权，不要手工回填旧令牌。回调 state/issuer/浏览器校验失败：重新生成入口，用同一浏览器完成授权；不支持从另一浏览器复制回调地址。

## 定向测试

在独立 Python 环境安装 `requests APScheduler<4 pytz fastapi httpx pytest` 后运行：

```bash
python -m pytest tests/test_fcloudpansign.py -q
```

测试使用隔离的 MoviePilot 存储替身与本机 HTTP 服务验证 OAuth 协议、回调安全、轮换、签到幂等和界面数据边界，不使用真实用户凭据。

2026-09-10 验证记录：41 项定向测试、Ruff 与差异检查通过。另使用运行中的本地 F-Cloudpan 和临时账号完成授权回调、签到、重复签到、刷新轮换、旧令牌 401 和撤销后 401 验证，数据库确认只有一条签到记录；测试账号和应用已清理。使用原生 Vuetify 预览检查深浅主题及表单。尚未在完整 MoviePilot 容器中安装验证，也未部署生产。
