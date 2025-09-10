import json
import re
import time
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType
from app.utils.http import RequestUtils
from app.db.site_oper import SiteOper

import importlib
import pkgutil
import inspect
from pathlib import Path


class PTAutoTask(_PluginBase):
    # 插件名称
    plugin_name = "PT自动任务"
    # 插件描述
    plugin_desc = "用来执行一些站点的定期任务，包括但不限于签到、喊话、领取任务等"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/liuyunfz/MoviePilot-Plugins/main/icons/ptautotask.png"
    # 插件版本
    plugin_version = "1.0.1"
    # 插件作者
    plugin_author = "liuyunfz"
    # 作者主页
    author_url = "https://github.com/liuyunfz"
    # 插件配置项ID前缀
    plugin_config_prefix = "ptautotask_"
    # 加载顺序
    plugin_order = 24
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _cron = None
    _onlyonce = False
    _notify = False
    _history_days = None
    # 重试相关
    _retry_count = 0  # 最大重试次数
    _current_retry = 0  # 当前重试次数
    _retry_interval = 2  # 重试间隔(小时)
    # 代理相关
    _use_proxy = False  # 是否使用代理，默认启用

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def __init__(self):
        super().__init__()
        self.support_sites = None
        self.filter_sites = None
        self.config_list = None
        self.config_group_by_domain = None

    def __init_load_sites(self):
        """
        初始化插件支持站点
        """
        sites_info = []

        # 确定 sites 文件夹路径（相对 ptautotask 模块）
        sites_path = Path(__file__).parent / "sites"
        pkg_prefix = __package__ or "ptautotask"
        for module_info in pkgutil.iter_modules([str(sites_path)]):
            module_name = f"{pkg_prefix}.sites.{module_info.name}"
            try:
                module = importlib.import_module(module_name)

                # 找出 Client 类
                client_cls = None
                tasks_cls = None
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # 确保是当前模块定义的类，而不是导入的
                    if getattr(obj, "__module__", "") != getattr(module, "__name__", ""):
                        continue
                    if name.lower() == "tasks":
                        tasks_cls = obj
                    else:
                        client_cls = obj

                if not client_cls or not tasks_cls:
                    continue

                site_name = client_cls.get_site_name() if hasattr(client_cls, "get_site_name") else module_info.name
                site_url = client_cls.get_site_domain() if hasattr(client_cls, "get_site_domain") else ""
                # 初始化 tasks
                # 由于获取任务方法非静态，故需要实例化
                tasks = tasks_cls(cookie=None)
                task_list = tasks.get_registered_tasks() if hasattr(tasks, "get_registered_tasks") else []
                # 释放 tasks 实例,防止占用过多内存
                del tasks
                sites_info.append({
                    "name": site_name,
                    "domain": site_url,
                    "tasks": task_list
                })
                logger.info(f"成功加载站点 {site_name}，支持任务数：{len(task_list)}")
            except ModuleNotFoundError:
                # 回退：尝试通过文件路径直接加载模块（适用于作为脚本或没有把包放入 sys.path 的情况）
                try:
                    module_file = sites_path / (module_info.name + ".py")
                    if module_file.exists():
                        spec = importlib.util.spec_from_file_location(module_info.name, str(module_file))
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                    else:
                        # 如果是包目录（含 __init__.py），也尝试加载包的 __init__.py
                        package_dir = sites_path / module_info.name
                        init_file = package_dir / "__init__.py"
                        if init_file.exists():
                            spec = importlib.util.spec_from_file_location(module_info.name, str(init_file))
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                        else:
                            raise ModuleNotFoundError(f"模块文件未找到: {module_info.name}")
                except Exception as e:
                    logger.error(f"加载站点 {module_info.name} 失败: {e}")
                    continue
            except Exception as e:
                logger.error(f"加载站点 {module_name} 失败: {e}")

        return sites_info

    def get_support_sites(self):
        """
        获取插件支持的所有站点列表（不含 cookie）
        """
        if not hasattr(self, "support_sites") or self.support_sites is None:
            self.support_sites = self.__init_load_sites()
        return self.support_sites

    def __init_filter_sites(self):
        """
        过滤出已启用的站点
        """
        support_sites = self.get_support_sites()
        filter_sites = []
        for support_site in support_sites:
            domain = support_site.get("domain")
            mp_site = SiteOper().get_by_domain(domain)
            if mp_site is not None and mp_site.is_active:
                support_site.update({"cookie": mp_site.cookie})
                filter_sites.append(support_site)

        return filter_sites

    def get_filter_sites(self, force: bool = False):
        """
        获取已启用并带 cookie 的站点列表。
        默认使用缓存；当需要最新数据时传入 force=True 强制重新扫描。
        """
        if not force and self.filter_sites is not None:
            return self.filter_sites
        self.filter_sites = self.__init_filter_sites()
        return self.filter_sites

    def __init_build_config(self):
        """
        构造出需要读取/写入的配置项
        """
        filter_sites = self.get_filter_sites()
        configs_by_domain = {}
        for support_site in filter_sites:
            domain = support_site.get("domain")
            configs_by_domain[domain] = [ids for ids in support_site.get("tasks")]
        # 返回示例 {"m-team.cc": ["mteam_daily_checkin", "mteam_bonus_checkin"], "pt.sjtu.edu.cn": ["sjtu_daily_checkin"]}
        return configs_by_domain

    def get_config_group_by_domain(self):
        if self.config_group_by_domain is None:
            self.config_group_by_domain = self.__init_build_config()
        return self.config_group_by_domain

    def __build_form_item(self, config):
        title_json = {
            'component': 'VRow',
            'content': [
                {
                    'component': 'VCol',
                    'props': {'cols': 12},
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'd-flex align-center mb-3'
                            },
                            'content': [
                                {
                                    'component': 'VIcon',
                                    'props': {
                                        'style': 'color: #1976D2;',
                                        'class': 'mr-2'
                                    },
                                    'text': 'mdi-chart-box'
                                },
                                {
                                    'component': 'span',
                                    'props': {
                                        'style': 'font-size: 1.1rem; font-weight: 500;'
                                    },
                                    'text': '{}站点设置'.format(config.get("name") if config.get("name") else "未知")
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        tasks = config.get("tasks", [])
        cnt = len(tasks)
        rows = []
        for i in range(0, cnt, 3):
            group = tasks[i:i + 3]
            cols = 12 // len(group)
            row = {
                'component': 'VRow',
                'props': {"align": "center"},
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': cols
                        },
                        'content': [
                            {
                                'component': 'VSwitch',
                                'props': {
                                    'model': task.get("id"),
                                    'label': task.get("label"),
                                    'hint': task.get("hint")
                                }
                            }
                        ]
                    } for task in group
                ]
            }
            rows.append(row)
        divider = {
            'component': 'VRow',
            'content': [
                {
                    'component': 'VCol',
                    'props': {'cols': 12},
                    'content': [
                        {
                            'component': 'VDivider',
                            'props': {'class': 'my-3'}
                        }
                    ]
                }
            ]
        }
        return [title_json] + rows + [divider]

    def __build_form(self):
        """
        构造出配置页面（返回 Python 列表/字典结构）
        """
        filter_sites = self.get_filter_sites(force=True)

        head_components = [
            {
                'component': 'VCardTitle',
                'props': {
                    'class': 'd-flex align-center'
                },
                'content': [
                    {
                        'component': 'VIcon',
                        'props': {
                            'style': 'color: #1976D2;',
                            'class': 'mr-2'
                        },
                        'text': 'mdi-calendar-check'
                    },
                    {
                        'component': 'span',
                        'text': '站点个性化设置'
                    }
                ]
            },
            {
                'component': 'VDivider'
            }
        ]

        # 收集所有站点的组件片段（__build_form_item 返回的是 Python 元素列表）
        site_sections = []
        for support_site in filter_sites:
            site_sections.extend(self.__build_form_item(support_site))

        # 将所有站点片段放到一个 VCardText 的 content 中
        components = []
        components.extend(head_components)
        components.append({
            'component': 'VCardText',
            'content': site_sections
        })

        return components

    def get_config_list(self) -> List[str]:
        """
        获取站点配置项列表
        """
        if self.config_list is None:
            configs_by_domain = self.__init_build_config()
            config_list = []
            for configs in configs_by_domain.values():
                config_list.extend([config.get("id") for config in configs])
            self.config_list = config_list
        # 返回示例 ["mteam_daily_checkin", "mteam_bonus_checkin", "sjtu_daily_checkin"]
        return self.config_list

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        sites_configs = self.get_config_list()
        # 接收参数
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron", "30 9,21 * * *")
            self._onlyonce = config.get("onlyonce", False)
            self._history_days = config.get("history_days", 30)
            # 站点个性化配置属性
            for site_config in sites_configs:
                setattr(self, site_config, config.get(site_config, None))

        # 停止现有任务
        self.stop_service()

        # 确保scheduler是新的
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        # 立即运行一次
        if self._onlyonce:
            logger.info(f"PT-Auto-Task服务启动，立即运行一次")
            self._scheduler.add_job(func=self.__do_tasks, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="PT_Task")
            # 关闭一次性开关
            self._onlyonce = False
            # 在更新持久配置时保留所有站点开关，避免覆盖为 False
            payload = {
                "onlyonce": False,
                "cron": self._cron,
                "enabled": self._enabled,
                "notify": self._notify,
                "history_days": self._history_days,
            }
            for site_config in sites_configs:
                # 保留当前内存中该站点配置的值（之前已从 config 赋值）
                payload[site_config] = getattr(self, site_config, False)

            self.update_config(payload)
        # 周期运行
        elif self._cron:
            logger.info(f"站点周期任务服务启动，周期：{self._cron}")
            self._scheduler.add_job(func=self.__do_tasks,
                                    trigger=CronTrigger.from_crontab(self._cron),
                                    name="PT_Task")
        # 启动任务
        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def _send_notification(self, title, text):
        """
        发送通知
        """
        if self._notify:
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title=title,
                text=text
            )

    def _schedule_retry(self, hours=None):
        """
        安排重试任务
        :param hours: 重试间隔小时数，如果不指定则使用配置的_retry_interval
        """
        pass


    def _schedule_retry(self, hours=None):
        """
        安排重试任务：在当前 scheduler 中增加一次性任务以便稍后重试 __do_tasks
        """
        try:
            interval = hours if hours is not None else self._retry_interval
            run_date = datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(hours=interval)
            if not self._scheduler:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.start()
            self._scheduler.add_job(func=self.__do_tasks, trigger='date', run_date=run_date, name="PT_Task_Retry")
            logger.info(f"已安排重试任务，{interval} 小时后执行")
        except Exception as e:
            logger.error(f"安排重试任务失败: {e}")


    def __do_tasks(self):
        """
        站点周期任务执行（按 run 保存历史并合并通知）
        """
        if hasattr(self, '_auto_task_in') and self._auto_task_in:
            logger.info("已有周期任务在执行，跳过当前任务")
            return

        self._auto_task_in = True
        try:
            filter_sites = self.get_filter_sites() or []
            any_failure = False
            run_records = []  # 本次运行的所有任务记录（list）
            _site_notify_map: Dict[str, List[str]] = {}  # 按站点分组的通知行
            _site_order: List[str] = []  # 保持站点顺序

            for support_site in filter_sites:
                site_name = support_site.get("name") or support_site.get("domain") or "未知站点"
                domain = support_site.get("domain") or ""
                cookie = support_site.get("cookie")

                tasks = support_site.get("tasks") or []
                for task in tasks:
                    try:
                        task_id = task.get("id")
                        if not task_id:
                            continue

                        enabled = getattr(self, task_id, False)
                        if not enabled:
                            logger.debug(f"任务 {task_id} 被配置为禁用，跳过")
                            continue

                        func_obj = task.get("func")
                        if not func_obj:
                            logger.warning(f"任务 {task_id} 未包含可执行函数，跳过")
                            continue

                        try:
                            method_name = getattr(getattr(func_obj, "__func__", func_obj), "__name__", None)
                        except Exception:
                            method_name = None

                        tasks_cls = None
                        try:
                            if hasattr(func_obj, "__self__") and func_obj.__self__ is not None:
                                tasks_cls = func_obj.__self__.__class__
                        except Exception:
                            tasks_cls = None

                        if not method_name:
                            logger.warning(f"无法确定 {task_id} 的方法名，跳过")
                            continue

                        result = None
                        if tasks_cls:
                            try:
                                new_instance = None
                                try:
                                    new_instance = tasks_cls(cookie=cookie)
                                except TypeError:
                                    new_instance = tasks_cls()
                                    if cookie is not None:
                                        setattr(new_instance, "cookie", cookie)
                                method = getattr(new_instance, method_name, None)
                                if not method:
                                    raise RuntimeError(f"在新实例中未找到方法 {method_name}")
                                logger.info(f"开始执行任务 {task_id}（站点: {site_name}）")
                                result = method()
                            except Exception:
                                raise
                        else:
                            logger.info(f"使用原绑定方法执行任务 {task_id}（站点: {site_name}，可能无 cookie）")
                            result = func_obj()

                        # 构造单个任务记录（假设任务返回字符串状态）
                        now_str = datetime.now(tz=pytz.timezone(settings.TZ)).strftime('%Y-%m-%d %H:%M:%S')
                        # 兼容不同返回类型：优先把字符串作为状态
                        if isinstance(result, str):
                            status_text = result
                        elif isinstance(result, dict):
                            status_text = result.get("status") or result.get("message") or "执行完成"
                        elif result is None:
                            status_text = "执行完成"
                        else:
                            status_text = repr(result)

                        record = {
                            "date": now_str,
                            "site": site_name,
                            "domain": domain,
                            "task_id": task_id,
                            "task_label": task.get("label"),
                            "status": status_text,
                        }

                        # 记录到本次运行集合
                        run_records.append(record)
                        is_fail = "失败" in status_text or "error" in status_text.lower() or "异常" in status_text
                        emoji = "❌" if is_fail else "✅"
                        line = f"{emoji} {task.get('label') or task_id}: {status_text}"
                        if site_name not in _site_order:
                            _site_order.append(site_name)
                        _site_notify_map.setdefault(site_name, []).append(line)
                        # 判断失败关键字
                        if "失败" in status_text or "error" in status_text.lower() or "异常" in status_text:
                            any_failure = True
                            logger.warning(f"{site_name} - {task_id} 返回失败: {status_text}")
                        else:
                            logger.info(f"{site_name} - {task_id} 执行成功: {status_text}")

                    except Exception as e:
                        any_failure = True
                        logger.error(f"{site_name} - {task.get('id')} 异常: {e}", exc_info=True)
                        now_str = datetime.now(tz=pytz.timezone(settings.TZ)).strftime('%Y-%m-%d %H:%M:%S')
                        record = {
                            "date": now_str,
                            "site": site_name,
                            "domain": domain,
                            "task_id": task.get("id"),
                            "task_label": task.get("label"),
                            "status": f"执行失败: {str(e)}",
                        }
                        run_records.append(record)
                        err_line = f"❌ {task.get('label') or task.get('id')}: 执行失败: {str(e)}"
                        if site_name not in _site_order:
                            _site_order.append(site_name)
                        _site_notify_map.setdefault(site_name, []).append(err_line)
                        run_records.append(record)
            # 根据失败与配置判断是否安排重试，并在需要时更新失败记录的 retry 信息
            if any_failure and self._retry_count and self._retry_count > 0:
                self._current_retry = min(self._current_retry + 1, self._retry_count)
                if self._current_retry <= self._retry_count:
                    logger.info(f"检测到执行失败，安排第 {self._current_retry} 次重试")
                    # 在失败记录中标注重试信息
                    for rec in run_records:
                        st = rec.get("status", "")
                        if "失败" in st or "异常" in st or "error" in st.lower():
                            rec["retry"] = {
                                "enabled": True,
                                "current": self._current_retry,
                                "max": self._retry_count,
                                "interval": self._retry_interval
                            }
                    self._schedule_retry()
                else:
                    logger.info("已达到最大重试次数，不再安排重试")
            else:
                # 没有失败或不需要重试时重置计数
                self._current_retry = 0

            # 保存本次运行为一个 list（each run is a list of records）
            try:
                self._save_history_run(run_records)
            except Exception as e:
                logger.error(f"保存本次运行历史失败: {e}")

            # 合并并发送一次通知（若启用）
            if self._notify and _site_notify_map:
                logger.info("推送启用，开始合并整理任务通知")
                title = "PT自动任务执行汇总"
                parts: List[str] = []
                for site in _site_order:
                    lines = _site_notify_map.get(site, [])
                    if not lines:
                        continue
                    parts.append(f"🔔 {site}")
                    parts.extend(lines)
                    parts.append("────────────────────")  # 站点间分隔符
                # 移除最后一个分隔符
                if parts and parts[-1].startswith("─"):
                    parts = parts[:-1]
                body = "\n".join(parts)
                try:
                    self._send_notification(title, body)
                    logger.info(f"已发送合并通知")
                except Exception as e:
                    logger.error(f"发送合并通知失败: {e}")

        finally:
            self._auto_task_in = False

    def _save_history_run(self, run_records: list):
        """
        将一次运行（run_records: list）追加到 history 中。
        history 的结构为 list，每项为 {'date': '...', 'records': [...]}
        """
        history = self.get_data('history') or []
        now_str = datetime.now(tz=pytz.timezone(settings.TZ)).strftime('%Y-%m-%d %H:%M:%S')
        run_entry = {
            "date": now_str,
            "records": run_records
        }

        history.append(run_entry)

        # 保留指定天数的记录（按 run 的日期判断）
        if self._history_days:
            try:
                cutoff = time.time() - int(self._history_days) * 24 * 60 * 60
                history = [h for h in history if
                           datetime.strptime(h["date"], '%Y-%m-%d %H:%M:%S').timestamp() >= cutoff]
            except Exception as e:
                logger.error(f"清理历史记录异常: {e}")

        self.save_data(key="history", value=history)


    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        services = []

        if self._enabled and self._cron:
            services.append({
                "id": "PT_Auto_Task",
                "name": "站点周期任务服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__do_tasks,
                "kwargs": {}
            })

        return services

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mt-3'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'd-flex align-center'
                                },
                                'content': [
                                    {
                                        'component': 'VIcon',
                                        'props': {
                                            'style': 'color: #1976D2;',
                                            'class': 'mr-2'
                                        },
                                        'text': 'mdi-calendar-check'
                                    },
                                    {
                                        'component': 'span',
                                        'text': '全局设置'
                                    }
                                ]
                            },
                            {
                                'component': 'VDivider'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
                                    # 基本开关设置
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'enabled',
                                                            'label': '启用插件',
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'notify',
                                                            'label': '开启通知',
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'onlyonce',
                                                            'label': '立即运行一次',
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    # Cron与日志保留天数
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VCronField',
                                                        'props': {
                                                            'model': 'cron',
                                                            'label': '执行周期',
                                                            'placeholder': '30 9,21 * * *',
                                                            'hint': '五位cron表达式，每天9:30与21:30执行'
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'history_days',
                                                            'label': '历史保留天数',
                                                            'placeholder': '30',
                                                            'hint': '历史记录保留天数'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mt-3'
                        },
                        'content': self.__build_form(),
                        # [
                        #     {
                        #         'component': 'VCardTitle',
                        #         'props': {
                        #             'class': 'd-flex align-center'
                        #         },
                        #         'content': [
                        #             {
                        #                 'component': 'VIcon',
                        #                 'props': {
                        #                     'style': 'color: #1976D2;',
                        #                     'class': 'mr-2'
                        #                 },
                        #                 'text': 'mdi-calendar-check'
                        #             },
                        #             {
                        #                 'component': 'span',
                        #                 'text': '站点个性化设置'
                        #             }
                        #         ]
                        #     },
                        #     {
                        #         'component': 'VDivider'
                        #     },
                        #     # 站点-Car 标题
                        #     {
                        #         'component': 'VRow',
                        #         'content': [
                        #             {
                        #                 'component': 'VCol',
                        #                 'props': {'cols': 12},
                        #                 'content': [
                        #                     {
                        #                         'component': 'div',
                        #                         'props': {
                        #                             'class': 'd-flex align-center mb-3'
                        #                         },
                        #                         'content': [
                        #                             {
                        #                                 'component': 'VIcon',
                        #                                 'props': {
                        #                                     'style': 'color: #1976D2;',
                        #                                     'class': 'mr-2'
                        #                                 },
                        #                                 'text': 'mdi-chart-box'
                        #                             },
                        #                             {
                        #                                 'component': 'span',
                        #                                 'props': {
                        #                                     'style': 'font-size: 1.1rem; font-weight: 500;'
                        #                                 },
                        #                                 'text': 'Car 站点设置'
                        #                             }
                        #                         ]
                        #                     }
                        #                 ]
                        #             }
                        #         ]
                        #     },
                        #     # 站点-Car 数据设置
                        #     {
                        #         'component': 'VRow',
                        #         'content': [
                        #             {
                        #                 'component': 'VCol',
                        #                 'props': {'cols': 12},
                        #                 'content': [
                        #                     {
                        #                         'component': 'VSwitch',
                        #                         'props': {
                        #                             'model': 'car_claim',
                        #                             'label': '领取任务',
                        #                             'hint': '领取Car的天天快乐任务'
                        #                         }
                        #                     }
                        #                 ]
                        #             }
                        #         ]
                        #     },
                        #     {
                        #         'component': 'VRow',
                        #         'content': [
                        #             {
                        #                 'component': 'VCol',
                        #                 'props': {'cols': 12},
                        #                 'content': [
                        #                     {
                        #                         'component': 'VDivider',
                        #                         'props': {
                        #                             'class': 'my-3'
                        #                         }
                        #                     }
                        #                 ]
                        #             }
                        #         ]
                        #     },
                        #     # 站点-QingWa 标题
                        #     {
                        #         'component': 'VRow',
                        #         'content': [
                        #             {
                        #                 'component': 'VCol',
                        #                 'props': {'cols': 12},
                        #                 'content': [
                        #                     {
                        #                         'component': 'div',
                        #                         'props': {
                        #                             'class': 'd-flex align-center mb-3'
                        #                         },
                        #                         'content': [
                        #                             {
                        #                                 'component': 'VIcon',
                        #                                 'props': {
                        #                                     'style': 'color: #1976D2;',
                        #                                     'class': 'mr-2'
                        #                                 },
                        #                                 'text': 'mdi-chart-box'
                        #                             },
                        #                             {
                        #                                 'component': 'span',
                        #                                 'props': {
                        #                                     'style': 'font-size: 1.1rem; font-weight: 500;'
                        #                                 },
                        #                                 'text': '🐸青蛙 站点设置'
                        #                             }
                        #                         ]
                        #                     }
                        #                 ]
                        #             }
                        #         ]
                        #     },
                        #     # 站点-QingWa 数据设置
                        #     {
                        #         'component': 'VRow',
                        #         'content': [
                        #             {
                        #                 'component': 'VCol',
                        #                 'props': {'cols': 6},
                        #                 'content': [
                        #                     {
                        #                         'component': 'VSwitch',
                        #                         'props': {
                        #                             'model': 'qingwa_shotbox',
                        #                             'label': '喊话',
                        #                             'hint': '执行站点-青蛙的喊话任务'
                        #                         }
                        #                     }
                        #                 ]
                        #             },
                        #             {
                        #                 'component': 'VCol',
                        #                 'props': {'cols': 6},
                        #                 'content': [
                        #                     {
                        #                         'component': 'VSwitch',
                        #                         'props': {
                        #                             'model': 'qingwa_buy_bonus',
                        #                             'label': '领取蝌蚪',
                        #                             'hint': '领取站点-青蛙的每日福利'
                        #                         }
                        #                     }
                        #                 ]
                        #             }
                        #         ]
                        #     }
                        #
                        # ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "cron": "30 9,21 * * *",
            "onlyonce": False,
            "history_days": 30,
            # # 站点-Car
            # "car_claim": True,
            # # 站点-QingWa
            # "qingwa_shotbox": True,
            # "qingwa_buy_bonus": True,
            **{k: True for k in self.get_config_list()}
        }

    # python
    def get_page(self) -> List[dict]:
        """
        构建插件详情页面，顶部展示统计信息，下面展示按运行（run）分组的历史，每条运行可展开按站点查看详情。
        """
        # 基本数据
        filter_sites = self.get_filter_sites() or []
        supported_sites = len(filter_sites)
        supported_tasks = sum(len(s.get("tasks", [])) for s in filter_sites)
        # 已启用任务数：根据当前配置属性判断
        enabled_tasks = 0
        for s in filter_sites:
            for t in s.get("tasks", []):
                if getattr(self, t.get("id"), False):
                    enabled_tasks += 1

        history = self.get_data('history') or []
        # 按时间倒序
        history = sorted(history, key=lambda x: x.get("date", ""), reverse=True)

        # 统计最近一次执行与累计成功/失败
        def is_fail(status: str) -> bool:
            if not status:
                return False
            st = status.lower()
            return ("失败" in status) or ("异常" in status) or ("error" in st)

        total_success = 0
        total_fail = 0
        for run in history:
            for r in run.get("records", []):
                if is_fail(r.get("status", "")):
                    total_fail += 1
                else:
                    total_success += 1

        last_run_success = 0
        last_run_fail = 0
        if history:
            last = history[0]
            for r in last.get("records", []):
                if is_fail(r.get("status", "")):
                    last_run_fail += 1
                else:
                    last_run_success += 1

        # 顶部统计卡片
        header_card = {
            'component': 'VCard',
            'props': {'variant': 'outlined', 'class': 'mb-4'},
            'content': [
                {
                    'component': 'VCardTitle',
                    'props': {'class': 'd-flex align-center'},
                    'content': [
                        {'component': 'VIcon', 'props': {'class': 'mr-2'}, 'text': 'mdi-chart-box'},
                        {'component': 'span', 'text': '运行统计概览'},
                        {'component': 'VSpacer'},
                        {
                            'component': 'VChip',
                            'props': {'size': 'small', 'variant': 'elevated', 'class': 'ma-1'},
                            'text': f'站点: {supported_sites}'
                        },
                        {
                            'component': 'VChip',
                            'props': {'size': 'small', 'variant': 'elevated', 'class': 'ma-1'},
                            'text': f'任务: {supported_tasks}'
                        },
                        {
                            'component': 'VChip',
                            'props': {'size': 'small', 'variant': 'elevated', 'color': 'primary', 'class': 'ma-1'},
                            'text': f'启用: {enabled_tasks}'
                        }
                    ]
                },
                {'component': 'VDivider'},
                {
                    'component': 'VCardText',
                    'content': [
                        {
                            'component': 'VRow',
                            'content': [
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 12, 'md': 4},
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-subtitle-1'},
                                            'text': f'最近一次（{history[0]["date"] if history else "无记录"}）: 成功 {last_run_success} / 失败 {last_run_fail}'
                                        }
                                    ]
                                },
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 12, 'md': 4},
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-subtitle-1'},
                                            'text': f'历史总计: 成功 {total_success} / 失败 {total_fail}'
                                        }
                                    ]
                                },
                                {
                                    'component': 'VCol',
                                    'props': {'cols': 12, 'md': 4},
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-subtitle-1'},
                                            'text': f'重试配置: {self._retry_count or 0} 次, 间隔 {self._retry_interval} 小时'
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        # 历史面板：每个 run 一个展开项
        panels = []
        for run in history:
            run_date = run.get("date", "")
            records = run.get("records", []) or []
            # 计算本次运行的启用/成功/失败（启用按当前配置判定）
            run_enabled = sum(1 for r in records if getattr(self, r.get("task_id"), False))
            run_success = sum(1 for r in records if not is_fail(r.get("status", "")))
            run_fail = len(records) - run_success

            # 按站点分组
            sites_map: Dict[str, List[dict]] = {}
            site_order: List[str] = []
            for r in records:
                site = r.get("site") or r.get("domain") or "未知站点"
                if site not in site_order:
                    site_order.append(site)
                sites_map.setdefault(site, []).append(r)

            # 构造每个站点的详情节点（simple list）
            site_blocks = []
            for site in site_order:
                recs = sites_map.get(site, [])
                # site header
                site_block = {
                    'component': 'VCard',
                    'props': {'variant': 'outlined', 'class': 'mb-2'},
                    'content': [
                        {
                            'component': 'VCardTitle',
                            'props': {'class': 'd-flex align-center'},
                            'content': [
                                {'component': 'VIcon', 'props': {'class': 'mr-2'}, 'text': 'mdi-bell-ring'},
                                {'component': 'span', 'text': site},
                                {'component': 'VSpacer'},
                                {
                                    'component': 'VChip',
                                    'props': {'size': 'small', 'variant': 'elevated'},
                                    'text': f'任务数: {len(recs)}'
                                }
                            ]
                        },
                        {'component': 'VDivider'},
                        {
                            'component': 'VCardText',
                            'content': [
                                {
                                    'component': 'VList',
                                    'props': {'dense': True},
                                    'content': [
                                        {
                                            'component': 'VListItem',
                                            'content': [
                                                {
                                                    'component': 'div',
                                                    'props': {'class': 'ml-0'},
                                                    'content': [
                                                        {
                                                            'component': 'div',
                                                            'text': f"{'✅' if not is_fail(r.get('status', '')) else '❌'}  {r.get('task_label') or r.get('task_id')}: {r.get('status', '')}"
                                                        }
                                                    ]
                                                }
                                            ]
                                        } for r in recs
                                    ]
                                }
                            ]
                        }
                    ]
                }
                site_blocks.append(site_block)

            # 面板标题（简洁汇总）
            panel_title = {
                'component': 'div',
                'props': {'class': 'd-flex align-center'},
                'content': [
                    {'component': 'span', 'text': run_date, 'props': {'class': 'mr-4'}},
                    {
                        'component': 'VChip',
                        'props': {'size': 'small', 'variant': 'elevated', 'class': 'ma-1'},
                        'text': f'启用: {run_enabled}'
                    },
                    {
                        'component': 'VChip',
                        'props': {'size': 'small', 'variant': 'elevated', 'color': 'success', 'class': 'ma-1'},
                        'text': f'成功: {run_success}'
                    },
                    {
                        'component': 'VChip',
                        'props': {'size': 'small', 'variant': 'elevated', 'color': 'error', 'class': 'ma-1'},
                        'text': f'失败: {run_fail}'
                    }
                ]
            }

            panels.append({
                'component': 'VExpansionPanel',
                'props': {},
                'content': [
                    {
                        'component': 'VExpansionPanelTitle',
                        'content': [panel_title]
                    },
                    {
                        'component': 'VExpansionPanelText',
                        'content': site_blocks or [
                            {'component': 'div', 'text': '无详细记录'}
                        ]
                    }
                ]
            })

        history_section = {
            'component': 'VCard',
            'props': {'variant': 'outlined', 'class': 'mb-4'},
            'content': [
                {
                    'component': 'VCardTitle',
                    'props': {'class': 'd-flex align-center'},
                    'content': [
                        {'component': 'VIcon', 'props': {'class': 'mr-2'}, 'text': 'mdi-history'},
                        {'component': 'span', 'text': '执行历史记录'},
                        {'component': 'VSpacer'},
                        {'component': 'span', 'text': f'共 {len(history)} 次运行'}
                    ]
                },
                {'component': 'VDivider'},
                {
                    'component': 'VCardText',
                    'content': [
                        {
                            'component': 'VExpansionPanels',
                            'props': {'accordion': True},
                            'content': panels if panels else [
                                {'component': 'div', 'text': '暂无历史记录'}
                            ]
                        }
                    ]
                }
            ]
        }

        # 结果页面组合
        components = []
        components.append(header_card)
        components.append(history_section)

        # 若有用户信息或其他保持原有逻辑（简化：保留前面用户信息卡片逻辑）
        user_info = self.get_data('user_info')
        if user_info and 'data' in user_info and 'attributes' in user_info['data']:
            # 尽量保留之前构造的 user_info_card 逻辑，若需要更复杂显示可复用原实现
            username = user_info['data']['attributes'].get('displayName', '未知用户')
            avatar_url = user_info['data']['attributes'].get('avatarUrl', '')
            user_card = {
                'component': 'VCard',
                'props': {'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {'component': 'VCardTitle', 'content': [{'component': 'span', 'text': username}]},
                    {'component': 'VDivider'},
                    {'component': 'VCardText', 'content': [{'component': 'div', 'text': f'头像: {avatar_url}'}]}
                ]
            }
            components.insert(0, user_card)

        return components

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
