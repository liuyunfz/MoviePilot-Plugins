# MoviePilot 插件库

适用于 MoviePilot V2 的第三方插件，提供 PT 站点任务、论坛与云盘签到，以及 CloudDrive2 文件事件联动。

## 插件一览

| 插件 | 插件 ID | 版本 | 功能 | 使用说明 |
| --- | --- | --- | --- | --- |
| PT 自动任务 | `PTAutoTask` | 1.2.0 | 多站点签到、喊话、领取任务与福利，按站点汇总结果 | [配置与开发](doc/ptautotask.md) |
| CD2 Webhook 通知 CMS | `CloudDrive2Webhook` | 1.1.1 | 命中文件事件后通知 CMS，整理中可跳过 | [Webhook 接入](doc/clouddrive2webhook.md) |
| NodeSeek 签到 | `NodeSeekSign` | 1.2.1 | NodeSeek 签到，支持浏览器会话及旧配置迁移 | [配置说明](doc/nodeseeksign.md) |

## 安装与更新

1. 在 MoviePilot **插件商店 → 添加第三方仓库**中填写：

   ```text
   https://github.com/liuyunfz/MoviePilot-Plugins
   ```

2. 安装所需插件，按照对应使用说明填写配置。
3. 首次运行后查看插件详情与日志，确认账号、执行结果和通知设置。
4. 更新时在插件商店刷新本仓库并升级。各插件的版本及变更记录以 [package.json](package.json) 为准。

本地调试可将 `plugins/<插件目录>` 复制到 MoviePilot 的 `app/plugins` 下，再重载插件。签到和 Webhook 插件同时提供对应 ZIP 源码包；解压后保留插件目录结构。ZIP 不包含 Cookie、配置、运行历史或 Python 缓存。

## 如何选择认证方式

- **PT 自动任务**：读取 MoviePilot 已配置站点的 Cookie。
- **NodeSeek**：使用目标站点 Cookie，或配置账号用于自动登录。
- **CD2 Webhook**：使用 MoviePilot API Key，可附加 Webhook 令牌；CMS Token 单独配置。

凭据只填写在自己的 MoviePilot 配置中，不要放入 Issue、截图或提交文件。普通签到与随机奖励的收益规则由对应站点决定。

## 开发与验证

PT 自动任务移植自 [PT_AUTO_TASK](https://github.com/liuyunfz/PT_AUTO_TASK)，新增站点可参考 [站点开发说明](doc/develop_ptautotask.md)。

离线定向测试：

```bash
python -m pip install requests 'APScheduler<4' pytz fastapi httpx pytest
python -m pytest tests -q
```

测试覆盖已发布插件的任务结果、调度或事件处理。站点和 CMS 的线上行为需在用户自己的环境中确认。测试不代表已经在完整 MoviePilot 容器或所有站点环境中验证。

## 许可证

本项目采用 [GPLv3](LICENSE)。
