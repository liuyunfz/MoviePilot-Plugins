# NodeSeek 签到

插件 ID：`NodeSeekSign` · 当前版本：`1.2.2`。

自动执行 NodeSeek 每日签到，支持固定或随机奖励、失败重试、账号登录、历史记录和通知。

## 配置步骤

1. 填写已登录 NodeSeek 的 Cookie。可选配置用户名和密码，用于没有 Cookie 时尝试自动登录；过期 Cookie 需手动更新。
2. 选择固定或随机奖励，设置 Cron、重试次数和间隔。
3. 推荐选择“浏览器会话”。插件使用 MoviePilot 的浏览器组件，把 Cookie 注入站点 Cookie 容器，访问签到页并等待站点 Service Worker 接管后发送请求。
4. 默认“自动”模式先直连，遇到 Cloudflare 或 `high risk action` 时切换浏览器。选择 FlareSolverr 时需配置 `FLARESOLVERR_URL`；如果站点仍要求浏览器验证，也会转浏览器完成签到。仅取得 CF Cookie 并不能保证直连接口可用。
5. 保存并启用，首次可执行一次检查结果。MoviePilot 所在环境需要能启动浏览器并访问 NodeSeek；站点要求人工验证时，自动登录可能无法完成。

旧版 `random_choice` 以及带 `nodeseeksign_` 前缀的配置会兼容读取。保存“仅运行一次”时会保留其他配置字段。

## 1.2.2 修复

- 浏览器模式直接使用浏览器，不再先发直连请求。Cookie 使用站点 Cookie 容器，避免 Service Worker 请求缺少登录会话；保留浏览器新获得的 CF 通行 Cookie。
- 区分 NodeSeek 的 `high risk action` 与 Cloudflare 验证页，等待 Service Worker 就绪后发送签到请求。
- 非 200 响应也解析业务 JSON。NodeSeek 重复签到返回 HTTP 500 和“今天已完成签到，请勿重复操作”，应记为“已签到”，不再无效重试。
- 直连请求使用 MoviePilot 代理，不再声明当前 Python 环境可能不支持的压缩编码。

## 验证记录（2026-09-28）

本地 Edge 已登录会话中执行插件 `_browser_sign_script` 的同一段请求逻辑：

- 执行前，签到页显示“今日还未签到”。
- 固定奖励签到返回 HTTP 200，`success=true`、`gain=5`、`current=1296`；刷新页面显示“今日签到获得鸡腿5个，当前排名第6199”。
- 再次执行返回 HTTP 500，`success=false`、`message="今天已完成签到，请勿重复操作"`。
- 16 项 Python 回归检查通过，覆盖实站响应判定、浏览器调用契约、Cookie 注入、代理、模式选择、风控回退和原有配置/调度逻辑。

实站验证复用了本地已登录浏览器；MoviePilot helper 与调度使用测试替身验证。本次未部署到完整 MoviePilot 容器，也未实测用户名密码登录或独立 FlareSolverr 服务。仓库不包含账号密码、Cookie 或浏览器会话文件。
