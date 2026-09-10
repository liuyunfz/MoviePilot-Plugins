# CD2 Webhook 通知 CMS

插件 ID：`CloudDrive2Webhook` · 当前版本：`1.1.1`。

流程为：CD2 收到文件创建事件 → 路径命中监听目录 → MoviePilot 未在整理 → 通知 CMS 增量同步。插件本身不搬运文件。

## 配置

- **监听路径**：填写 CD2 事件中的路径，例如 `/115/upload/inbox`，不是 MoviePilot 本机的另一套映射路径。
- **监听动作**：默认 `create`，多个动作以英文逗号分隔。
- **CMS 地址 / Token**：对应 CMS 服务和同步接口凭据。
- **通知类型**：`lift_sync` 或 `auto_organize`，依 CMS 接口配置选择。
- **整理中跳过**：默认开启，检测 MoviePilot 的文件整理进度；已在整理时记录跳过。
- **Webhook 令牌**：可选附加校验，推荐由 CD2 使用 `X-Webhook-Token` 请求头发送；它不替代 MoviePilot API Key。

CD2 `file_system_watcher.url` 指向：

```text
https://你的MP域名/api/v1/plugin/CloudDrive2Webhook/file_notify?apikey=你的MP_API_KEY
```

请求体需要 `event_category=file`、`event_name=notify` 和 `data` 数组，例如：

```json
{
  "event_category": "file",
  "event_name": "notify",
  "data": [{ "action": "create", "source_file": "/115/upload/inbox/movie.mkv" }]
}
```

`/mount_notify` 接收挂载事件，仅记录历史。`/webhook/cd2` 为旧文件回调路径的兼容入口，均需 MoviePilot 鉴权。

CMS 调用方式对齐 [cmsnotify](https://github.com/imaliang/MoviePilot-Plugins/tree/main/plugins.v2/cmsnotify)：`GET /api/sync/lift_by_token`，查询参数为 `token` 与 `type`。HTTP 2xx 表示通知请求成功，不代表 CMS 后续整理已经完成。

## 1.1.1 发布整理

修复路径前缀和 `..` 判断；支持监听根目录。CMS Token、完整请求 URL 和原始响应不再写入返回值、历史或日志。旧版本已经产生的历史不会被本次升级自动删除。

定向测试覆盖路径边界、整理中跳过、未命中事件以及 CMS 成功/异常响应的凭据保护；未向真实 CMS 发送通知。
