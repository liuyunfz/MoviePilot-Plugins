# F-Cloudpan 签到（MoviePilot V2）

通过 F-Cloudpan 的公共应用设备授权，定时签到并查看昵称、头像、积分、VIP、连续签到、累计签到和近 14 日记录。同一用户对同一应用共用授权，各台 MoviePilot 独立连接；无需公网地址、端口映射、回调地址或共享密钥。

界面参考 [madrays/MoviePilot-Plugins 的蜂巢论坛签到](https://github.com/madrays/MoviePilot-Plugins/tree/main/plugins/fengchaosignin) 的原生 Vuetify 分区卡片。授权及签到逻辑独立实现。

## 版本与前提

**1.1.1 固定官方站点与公共应用，使用 RFC 8628 Device Authorization Grant。需要 F-Cloudpan 服务端支持 `PUBLIC_DEVICE` 公共应用。** 旧版只支持机密应用的云盘不能直接使用本版本。

正式站点需由管理员注册一个公共应用，所有 MP 用户共用它的 **Client ID**，但各自登录、确认并取得独立授权。Client ID 是公开标识，可以写入开源代码；本插件不需要也不保存 Client Secret。

站点固定为 **https://fcloudpan.com**。站点地址与 Client ID 均由插件维护者内置，普通用户无需填写，也不能通过旧版配置覆盖。

插件已内置维护者提供的正式应用 Client ID：`app_TobngQ3HIC-XGWODMEDA40k4zO9nPElvzFyh1a9QzpM`。用户可直接发起授权，无需填写应用信息。本插件要求生产应用为 PUBLIC_DEVICE 类型，并允许 account:read、account:write。

## 安装与连接

1. 在 MoviePilot 添加本仓库并安装 **F-Cloudpan 签到**。
2. 开启“保存后连接 / 重新授权”并保存。
3. 打开插件详情，点击“前往 F-Cloudpan 授权”。也可在另一台设备打开云盘确认页面，输入显示的确认码。
4. 在云盘登录，核对应用名称、确认码与权限，点击允许。插件在后台完成连接；重新打开插件详情查看结果，无需浏览器回调 MP。
5. 启用定时签到，或选择“保存后立即签到一次”。授权完成本身只读取资料，不自动签到。

MP 只需能主动连接 `https://fcloudpan.com`；浏览器打开同一站点完成登录与确认。用户不必申请自己的应用，也无需配置 MP 公网地址。

授权入口直接打开 F-Cloudpan 确认页面，不需要扫码功能或二维码依赖。手工安装时请复制完整 `plugins/fcloudpansign` 目录，同目录 ZIP 包含相同文件。

## 站点管理员：注册统一应用

| 字段 | 建议值 |
|---|---|
| 名称 | F-Cloudpan · MoviePilot 签到助手 |
| 类型 | PUBLIC_DEVICE（公共设备应用） |
| 主页 | https://github.com/liuyunfz/MoviePilot-Plugins |
| 权限 | `account:read account:write` |
| 回调地址 | 无需填写 |
| Client Secret | 无需生成或分发 |
| 图标 | 与 F-Cloudpan Telegram 使用同一图标；本仓库 `icons/fcloudpansign.png` |

说明可填写：

> 面向 MoviePilot 用户的开源签到插件。用户主动授权后，插件在其自己的 MoviePilot 实例中执行每日签到，并读取云盘昵称、头像、积分余额、VIP 和签到记录。仅申请账户读取及签到所需权限，不申请资源解锁、购买或支付权限，用户可随时撤销授权。

维护者在生产后台创建一次应用，取得公开 Client ID 后填入 `FCloudpanSign.OAUTH_CLIENT_ID`。`FCLOUDPAN_ORIGIN` 固定为 `https://fcloudpan.com`。两项均不属于用户配置，不包含 Secret。

所有用户授权的是同一个已注册应用，但每次授权会产生绑定当前用户与 MP 实例的独立令牌。MP 使用该令牌读取该用户资料并签到，云盘在该用户的“已授权应用”中合并为一张应用卡片，显示图标、名称、权限和时间，可展开查看设备。断开一台设备不影响其他设备；在个人中心撤销整个应用会断开该用户的全部设备；管理端停用应用则会影响使用该应用的全部授权。

应用类型和权限由服务端校验；公开 Client ID 不用于证明运行的代码来自官方。

## 签到与资料

- 普通签到使用固定奖励，默认开启此模式；随机签到奖励**可能为负**，需用户显式选择。
- 默认周期 `30 9 * * *`，按 MoviePilot 时区调度，并随机延迟 0–300 秒。云盘签到日按北京时间划分。
- 先读取当天状态，已签到不再提交；提交超时或 5xx 时回查实际结果，不盲目重复 POST。
- “立即刷新资料”只读取信息。详情页面使用本地缓存，打开页面不会请求云盘；授权等待由后台任务完成。
- 运行记录最多 500 条，默认保留 30 天，详情展示最近 50 条。近 14 日签到来自云盘 API。
- 通知使用 MoviePilot 已配置渠道，默认关闭。关闭定时签到后仍可手动连接、签到或刷新。

## 设备授权与生命周期

设备授权通常 10 分钟有效，以服务端响应为准。插件只在用户打开连接开关并保存时申请新设备码；过期或拒绝后停止，不自动反复发起授权。

后台默认每 5 秒查询一次：`authorization_pending` 继续等待，`slow_down` 将后续间隔累加 5 秒；连接超时、429 和 5xx 使用退避，429 同时尊重数字形式的 `Retry-After`。拒绝、失效或无效响应停止本次连接。MP 重载保留未过期设备码与下一次查询时间，不会重新申请；重启时发现未完成的兑换会延迟后再确认。

访问令牌最多有效 1 小时，到期前适当提前量（最多 90 秒）或收到 401 后串行轮换；临近应用到期时，令牌有效期会缩短。同一用户×应用从首次兑换起共用 **30 天期限，添加设备和刷新均不延期**，届时需要再次打开链接确认。插件读取服务端返回的 `authorization_expires_at` 作为真实到期时间，不从当前设备连接时间推算。旧服务未返回此字段时显示到期未知，交由服务端校验；升级时清除旧版按设备推算的时间。刷新前保存正在刷新标记；刷新结果未知后停止重试旧 refresh token，避免重放撤销授权。

- **取消本次连接**：停止本地等待并移除设备码，已有账号授权保持不变。该操作不撤销已发出的云端确认码；不要再确认它，等待其过期即可。
- **断开本设备授权**：撤销当前设备授权，成功后清除本地资料和历史，不影响其他 MP 设备。个人中心的“撤销整个应用”会断开该用户在该应用下的全部设备。撤销未确认则停止账户操作并保留凭据，允许再次撤销或在云盘个人中心撤销。
- **重新授权**：成功后替换账号并清除旧资料和历史。旧云端授权不会因为本地替换自动消失，可在云盘个人中心撤销。
- 维护者变更内置应用时会清除旧本地身份，需要重新连接；应先在云盘撤销旧授权。

令牌仅保存在 MoviePilot 的插件存储中，不出现在表单、详情、日志、历史、通知或源码。设备码仅供 MP 后台使用，页面只展示用户确认码。不要复制同一授权存储给多个 MP 进程同时运行；各实例应分别授权。

## 从旧版升级

旧版的 Client Secret、MP 地址配置会被移除，旧授权不会迁移为公共客户端授权。请通过插件内置的公共应用重新连接，并在云盘个人中心撤销旧版应用授权。旧 `/oauth/start`、`/oauth/callback` 插件路由已移除，不再需要 MP 匿名接口或相应反向代理配置。

1.1.0 的可填写站点地址和 Client ID 配置会被移除，统一使用内置值。已有授权只有在其原站点及 Client ID 与内置值一致时才保留；否则重新连接。

## 接口与排查

| 用途 | 接口 | 鉴权 / 请求 |
|---|---|---|
| 申请设备码 | `POST /oauth/device/code` | form：client_id、scope |
| 用户确认页 | `GET /oauth/device?user_code=…` | 云盘登录、显式确认 |
| 查询并兑换 | `POST /oauth/token` | form：client_id、device_code、grant_type=urn:ietf:params:oauth:grant-type:device_code |
| 轮换令牌 | `POST /oauth/token` | form：client_id、refresh_token、grant_type=refresh_token |
| 账户 | `GET /oauth/account` | Bearer；顶层账户对象 |
| 签到状态 | `GET /api/check-in` | Bearer / account:read；`code: 0, data: {...}` |
| 签到 | `POST /api/check-in` | Bearer / account:write；JSON mode=STANDARD 或 LAS_VEGAS |
| 撤销 | `POST /oauth/revoke` | form：client_id、token |

公共客户端请求不带 Basic 或 Secret。接口不跟随重定向，不关闭 TLS 校验，不读取系统代理、`.netrc` 或用户 Cookie。确认链接必须与内置官方站点同源。

- 404 / 不支持设备授权：服务端版本未支持此流程，联系站点管理员。
- invalid_client / unauthorized_client：联系插件维护者核对内置 Client ID 及生产应用的 PUBLIC_DEVICE 类型。
- 403：检查权限、应用和用户状态。
- 30 天到期 / 刷新结果未知：重新连接，不手工回填旧令牌。
- 云盘已允许但插件显示失效：兑换响应可能丢失，重新连接并按需撤销未使用的旧授权。

## 验证

在独立 Python 环境安装 `requests APScheduler<4 pytz pytest` 后运行：

```bash
python -m pytest tests/test_fcloudpansign.py -q
```

定向测试覆盖设备授权、间隔与退避、取消/拒绝/到期、重载恢复、公共客户端鉴权、刷新轮换、签到幂等及 UI 数据隔离。完整 MoviePilot 容器安装与生产站点授权仍需在对应环境验证。

本地 F-Cloudpan 真实联调已覆盖等待/拒绝/允许、账户读取、签到与重复签到、刷新后旧令牌 401、撤销后 401 及设备隔离；共享到期时间、短有效期令牌和旧状态迁移由定向测试覆盖。图标与原 Telegram 文件逐字节一致，原生组件预览检查了深浅主题和手机宽度。

正式应用 ID 已内置，但本次发布未替用户执行生产账号授权或签到。需要在自己的 MoviePilot 中按上述步骤完成连接。应用生态展示由 F-Cloudpan 后台控制，不改变插件的固定站点、Client ID 或用户授权流程。
