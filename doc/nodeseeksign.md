# NodeSeek 签到

插件 ID：`NodeSeekSign` · 当前版本：`1.2.4`。

自动执行 NodeSeek 每日签到，支持固定或随机奖励、失败重试、账号登录、历史记录和通知。

## 配置步骤

1. 可仅填写 NodeSeek 用户名和密码，并配置 MoviePilot 的 `FLARESOLVERR_URL`（实测 FlareSolverr 3.5.2）。Cookie 可留空：插件会自动验证、登录并保存新会话。也支持直接填写已登录 Cookie。账号启用 2FA 时无法仅凭账号密码完成登录。
2. 选择固定或随机奖励，设置 Cron、重试次数和间隔。
3. 可保留默认“自动”模式，或选择“浏览器会话”。插件使用 MoviePilot 已安装的浏览器组件，把 Cookie 注入站点 Cookie 容器，访问签到页并等待站点 Service Worker 接管后发送请求。
4. 默认“自动”模式在有 Cookie 时先请求接口，遇到 Cloudflare 或 `high risk action` 时切换浏览器；没有 Cookie 时直接尝试浏览器登录。选择 FlareSolverr 时需配置 `FLARESOLVERR_URL`；如果站点仍要求浏览器验证，也会转浏览器完成签到。仅取得 CF Cookie 并不能保证直连接口可用。
5. 保存并启用，首次可执行一次检查结果。MoviePilot 所在环境需要能启动浏览器并访问 NodeSeek；站点要求人工验证时，自动登录可能无法完成。

旧版 `random_choice` 以及带 `nodeseeksign_` 前缀的配置会兼容读取。保存“仅运行一次”时会保留其他配置字段。

## 1.2.4 修复

- 纯账号密码登录复用 FlareSolverr 的 Turnstile 能力，临时独立会话先预加载再请求验证令牌，结束后清理会话。服务 `status=ok` 而没有令牌时按失败处理。
- 登录保留 CloakBrowser 原生指纹，只使用 FlareSolverr 的验证令牌，不移植它的 User-Agent 或 CF Cookie。实际调试中搬运两者的路径曾出现登录／签到 403。
- 从站点页面动态发现 preLogin、postLogin 模块，生成登录请求头并保存响应令牌；不固定带版本哈希的 JS 地址。
- 用 `/api/account/signOut` 链接确认登录，兼容无文字退出图标；登录后在首页同一上下文签到。
- 当前 NodeSeek 桌面页面的验证参数为 `tabs_till_verify=25`。站点布局或模块接口变化可能需要更新插件；验证服务必须能经与 MoviePilot 相同的代理访问 NodeSeek。

## MoviePilot 纯账号密码实测（2026-09-28，1.2.4）

正式候选包安装至 MoviePilot v2.15.6，保留自动模式、账号密码和随机签到，清空 Cookie 后执行一次：

- 17:33:21：配置日志确认 `cookie=未设置`。
- 17:33:33：调用 FlareSolverr 3.5.2 完成登录页验证。
- 17:34:51：验证登录态成功，保存新 Cookie。
- 17:34:52：签到接口返回“今天已完成签到，请勿重复操作”，记录为已签到。

这验证了从空 Cookie 开始的账号密码登录与签到接口完整链路；当日已签，未新增奖励。使用 CloakBrowser 0.5.6 / Playwright 1.62.0，无需安装 cf_clearance。

## 1.2.3 修复

- 原“Playwright 未安装”把缺少 `cf_clearance` 的导入错误混为一谈。移除旧依赖，优先使用 MoviePilot 已安装的 CloakBrowser；旧版环境兼容 PlaywrightHelper，并准确报告缺失的模块。
- 登录入口改为 `/signIn.html`，登录和签到复用同一浏览器会话；只有验证到“登出”入口后才保存 Cookie。等待站点正常验证，超时不提交账号密码。
- 将 MoviePilot 的 requests 代理配置转换为浏览器需要的 `server` 格式，修复 `proxy.server: expected string, got undefined`。验证未完成时尝试直连。
- 自动／浏览器模式不强制调用全局配置的可选 FlareSolverr。显式选择 FlareSolverr 时仍需安装并配置对应服务。

## 1.2.2 修复

- 浏览器模式直接使用浏览器，不再先发直连请求。Cookie 使用站点 Cookie 容器，避免 Service Worker 请求缺少登录会话；保留浏览器新获得的 CF 通行 Cookie。
- 区分 NodeSeek 的 `high risk action` 与 Cloudflare 验证页，等待 Service Worker 就绪后发送签到请求。
- 非 200 响应也解析业务 JSON。NodeSeek 重复签到返回 HTTP 500 和“今天已完成签到，请勿重复操作”，应记为“已签到”，不再无效重试。
- 直连请求使用 MoviePilot 代理，不再声明当前 Python 环境可能不支持的压缩编码。

## MoviePilot 实测（2026-09-28，1.2.3）

通过“本地插件安装”安装修复 ZIP，在用户实际 MoviePilot 中保留“自动”模式、随机签到及原账号配置，填入用户授权的本地 NodeSeek 登录会话：

- 14:44:10：接口要求浏览器会话，自动切换浏览器。
- 14:44:22、14:45:17：均返回“今天已完成签到，请勿重复操作”，历史记为“已签到”。当天此前已经签到，因此本轮未产生新的奖励。
- 实际环境：Playwright 1.62.0、CloakBrowser 0.5.6，未安装 `cf_clearance`；全局浏览器模式为 flaresolverr，验证成功路径没有依赖该可选服务。
- 26 项回归测试通过。

账号密码自动登录尚未实测成功：同一 MoviePilot 的代理连接能打开登录表单，但站点验证超时；直连访问也超时。本次确认可用的是已登录 Cookie 的自动签到路径，不能据此保证无 Cookie 登录可用。Cookie 到期后可能需要重新提供有效会话。未实测独立 FlareSolverr 服务。

仓库及发布包不包含账号密码、Cookie 或浏览器会话文件。

## 本地浏览器历史验证（2026-09-28，1.2.2）

本地 Edge 已登录会话中执行插件 `_browser_sign_script` 的同一段请求逻辑：

- 执行前，签到页显示“今日还未签到”。
- 固定奖励签到返回 HTTP 200，`success=true`、`gain=5`、`current=1296`；刷新页面显示“今日签到获得鸡腿5个，当前排名第6199”。
- 再次执行返回 HTTP 500，`success=false`、`message="今天已完成签到，请勿重复操作"`。
- 16 项 Python 回归检查通过，覆盖实站响应判定、浏览器调用契约、Cookie 注入、代理、模式选择、风控回退和原有配置/调度逻辑。

以上为 1.2.2 的本地浏览器验证记录；1.2.3 的实际 MoviePilot 验证及限制见上文。
