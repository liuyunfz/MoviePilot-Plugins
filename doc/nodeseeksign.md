# NodeSeek 签到

插件 ID：`NodeSeekSign` · 当前版本：`1.2.1`。

自动执行 NodeSeek 每日签到，支持固定或随机奖励、失败重试、账号登录、历史记录和通知。

## 配置步骤

1. 填写 NodeSeek Cookie；需要自动更新会话时配置用户名和密码。
2. 选择固定或随机奖励，设置 Cron、重试次数和间隔。
3. 默认使用 `direct` 请求模式。遇到站点验证时，可根据 MoviePilot 环境选择 `playwright` 浏览器模式或 `flaresolverr` 会话模式。
4. 使用 FlareSolverr 时，需要在 MoviePilot 配置 `FLARESOLVERR_URL`；浏览器模式依赖 MoviePilot 的浏览器组件和可用浏览器环境。
5. 保存并启用，首次可执行一次检查结果。站点要求额外人工验证时，自动登录可能无法完成。

旧版 `random_choice` 以及带 `nodeseeksign_` 前缀的配置会兼容读取。保存“仅运行一次”时会保留其他配置字段。

## 1.2.1 发布整理

停用后不再安排周期签到；启用时单次任务不会替代周期任务。移除重复的 MoviePilot 公共服务调度，Cron 使用 MoviePilot 时区，并处理尚未启动的调度器停止情况。

定向检查覆盖旧配置迁移、调度开关、单次配置保存及表单/详情渲染。未使用真实 NodeSeek 账号验证线上签到、浏览器登录或 FlareSolverr。
