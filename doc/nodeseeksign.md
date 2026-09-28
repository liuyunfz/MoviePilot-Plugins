# NodeSeek 签到

插件 ID：`NodeSeekSign` · 当前版本：`1.2.3`。

自动执行 NodeSeek 每日签到，支持固定或随机奖励、失败重试、账号登录、历史记录和通知。

## 配置步骤

1. 填写已登录 NodeSeek 的 Cookie。可选配置用户名和密码，用于没有 Cookie 或浏览器发现会话过期时尝试自动登录。站点验证不能自动完成时，需更新已登录的 Cookie。
2. 选择固定或随机奖励，设置 Cron、重试次数和间隔。
3. 可保留默认“自动”模式，或选择“浏览器会话”。插件使用 MoviePilot 已安装的浏览器组件，把 Cookie 注入站点 Cookie 容器，访问签到页并等待站点 Service Worker 接管后发送请求。
4. 默认“自动”模式在有 Cookie 时先请求接口，遇到 Cloudflare 或 `high risk action` 时切换浏览器；没有 Cookie 时直接尝试浏览器登录。选择 FlareSolverr 时需配置 `FLARESOLVERR_URL`；如果站点仍要求浏览器验证，也会转浏览器完成签到。仅取得 CF Cookie 并不能保证直连接口可用。
5. 保存并启用，首次可执行一次检查结果。MoviePilot 所在环境需要能启动浏览器并访问 NodeSeek；站点要求人工验证时，自动登录可能无法完成。

旧版 `random_choice` 以及带 `nodeseeksign_` 前缀的配置会兼容读取。保存“仅运行一次”时会保留其他配置字段。

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
