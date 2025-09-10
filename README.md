# MoviePilot-Plugins

这是一个MoviePilot的**第三方插件库**

## 插件列表

### PT自动任务 (PTAutoTask)
- 支持多种PT站点
- 自动签到、做任务、领取奖励等功能
- 定时执行，支持自定义时间间隔
- 动态读取支持站点列表，方便扩展
- **模块化设计，欢迎PR贡献更多PT站点支持**


## 安装说明

**本仓库为第三方插件库，需在MoviePilot中添加仓库地址使用**

1. 在MoviePilot的插件商店页面，点击"添加第三方仓库"
2. 添加本仓库地址：`https://github.com/liuyunfz/MoviePilot-Plugins`
3. 添加成功后，在插件列表中找到需要的插件
4. 安装并启用插件
5. 根据下方说明配置插件参数

## 使用说明

### PT自动任务 (PTAutoTask)
本插件移植自[PT_AUTO_TASK](https://github.com/liuyunfz/PT_AUTO_TASK)，插件会自动读取支持的站点并显示在配置页面中，用户只需选择对应站点需要启动的任务运行即可。

本插件使用站点模块化设计，欢迎各位进行贡献，详细的开发教程请参考：[PTAutoTask开发说明](doc/develop_ptautotask.md)

## 许可证

本项目采用 GPLv3 - 详见 [LICENSE](LICENSE) 文件 