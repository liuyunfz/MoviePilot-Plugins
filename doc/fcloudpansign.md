# F-Cloudpan 签到（MoviePilot V2）

通过 F-Cloudpan 的公共应用设备授权，定时签到并查看昵称、头像、积分、VIP、连续签到、累计签到和近 14 日记录。每台 MoviePilot 独立授权；无需公网地址、端口映射、回调地址或共享密钥。

界面参考 [madrays/MoviePilot-Plugins 的蜂巢论坛签到](https://github.com/madrays/MoviePilot-Plugins/tree/main/plugins/fengchaosignin) 的原生 Vuetify 分区卡片。授权及签到逻辑独立实现。

## 版本与前提

**1.1.0 改用 RFC 8628 Device Authorization Grant。需要 F-Cloudpan 服务端支持 `PUBLIC_DEVICE` 公共应用。** 旧版只支持机密应用的云盘不能直接使用本版本。

正式站点需由管理员注册一个公共应用，所有 MP 用户共用它的 **Client ID**，但各自登录、确认并取得独立授权。Client ID 是公开标识，可以写入开源代码；本插件不需要也不保存 Client Secret。

目前尚未登记正式公共应用，因此站点地址与公共 Client ID 保留为配置项，不内置测试 ID。正式注册后可统一提供给用户，无需每人申请应用。

## 安装与连接

1. 在 MoviePilot 添加本仓库并安装 **F-Cloudpan 签到**。
2. 填入云盘站点根地址及该站点提供的公共 Client ID，开启“保存后连接 / 重新授权”并保存。
3. 打开插件详情，点击“前往 F-Cloudpan 授权”。也可在另一台设备打开云盘确认页面，输入显示的确认码。
4. 在云盘登录，核对应用名称、确认码与权限，点击允许。插件在后台完成连接；重新打开插件详情查看结果，无需浏览器回调 MP。
5. 启用定时签到，或选择“保存后立即签到一次”。授权完成本身只读取资料，不自动签到。

MP 只需能主动连接云盘。云盘地址使用 HTTPS 根地址，不含子路径或查询参数。本机开发允许 `http://localhost:端口` 和回环 IP；容器中的 localhost 指容器自身，手机中的 localhost 指手机，必须按实际网络配置联调。

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
| 图标 | 本仓库 `icons/fcloudpansign.svg`，按站点图标上传要求提供 |

说明可填写：

> 面向 MoviePilot 用户的开源签到插件。用户主动授权后，插件在其自己的 MoviePilot 实例中执行每日签到，并读取云盘昵称、头像、积分余额、VIP 和签到记录。仅申请账户读取及签到所需权限，不申请资源解锁、购买或支付权限，用户可随时撤销授权。

应用类型和权限由服务端校验；公开 Client ID 不用于证明运行的代码来自官方。授权确认页和用户的已授权应用列表由 F-Cloudpan 服务端展示应用图标、名称和权限。

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

访问令牌有效期 1 小时，到期前 90 秒或收到 401 后串行轮换。授权从首次兑换起最多 **30 天，不滑动续期**，届时需要再次打开链接确认。刷新前保存正在刷新标记；刷新结果未知后停止重试旧 refresh token，避免重放撤销授权。

- **取消本次连接**：停止本地等待并移除设备码，已有账号授权保持不变。该操作不撤销已发出的云端确认码；不要再确认它，等待其过期即可。
- **撤销并解除授权**：撤销当前设备授权，成功后清除本地资料和历史，不影响其他 MP 设备。撤销未确认则停止账户操作并保留凭据，允许再次撤销或在云盘个人中心撤销。
- **重新授权**：成功后替换账号并清除旧资料和历史。旧云端授权不会因为本地替换自动消失，可在云盘个人中心撤销。
- 更换站点或 Client ID 会清除旧本地身份，请先撤销旧授权。

令牌仅保存在 MoviePilot 的插件存储中，不出现在表单、详情、日志、历史、通知或源码。设备码仅供 MP 后台使用，页面只展示用户确认码。不要复制同一授权存储给多个 MP 进程同时运行；各实例应分别授权。

## 从 1.0.0 升级

旧版的 Client Secret、MP 地址配置会被移除，旧授权不会迁移为公共客户端授权。请使用站点提供的 **PUBLIC_DEVICE Client ID** 重新连接，并在云盘个人中心撤销旧版应用授权。旧 `/oauth/start`、`/oauth/callback` 插件路由已移除，不再需要 MP 匿名接口或相应反向代理配置。

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

公共客户端请求不带 Basic 或 Secret。接口不跟随重定向，不关闭 TLS 校验，不读取系统代理、`.netrc` 或用户 Cookie。确认链接必须与配置站点同源。

- 404 / 不支持设备授权：服务端版本未支持此流程，联系站点管理员。
- invalid_client / unauthorized_client：确认 Client ID 属于当前站点且应用类型为 PUBLIC_DEVICE。
- 403：检查权限、应用和用户状态。
- 30 天到期 / 刷新结果未知：重新连接，不手工回填旧令牌。
- 云盘已允许但插件显示失效：兑换响应可能丢失，重新连接并按需撤销未使用的旧授权。

## 验证

在独立 Python 环境安装 `requests APScheduler<4 pytz pytest` 后运行：

```bash
python -m pytest tests/test_fcloudpansign.py -q
```

定向测试覆盖设备授权、间隔与退避、取消/拒绝/到期、重载恢复、公共客户端鉴权、刷新轮换、签到幂等及 UI 数据隔离。完整 MoviePilot 容器安装与生产站点授权仍需在对应环境验证。

2026-09-11：63 项插件定向测试与 Ruff 通过；原生组件预览验证直接授权链接、深浅主题和手机宽度。真实本地 F-Cloudpan 联调通过等待/拒绝/允许、账户与签到、重复签到、刷新后旧令牌 401、撤销后 401 及两设备授权隔离。临时数据由服务端开发任务统一清理。未部署生产。
