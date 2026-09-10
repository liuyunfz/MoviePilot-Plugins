import posixpath
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Tuple

from fastapi import Request

from app.helper.progress import ProgressHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import ProgressKey


class CloudDrive2Webhook(_PluginBase):
    plugin_name = "CD2 Webhook通知CMS"
    plugin_desc = "接收 CloudDrive2 create webhook，命中指定路径后通知 CMS 整理"
    plugin_icon = "https://raw.githubusercontent.com/liuyunfz/MoviePilot-Plugins/main/icons/ptautotask.png"
    plugin_version = "1.1.1"
    plugin_author = "liuyunfz"
    author_url = "https://github.com/liuyunfz"
    plugin_config_prefix = "cd2webhook_"
    plugin_order = 25
    auth_level = 2

    _enabled = False
    _monitor_path = ""
    _monitor_actions = "create"
    _webhook_token = ""
    _cms_domain = ""
    _cms_api_token = "cloud_media_sync"
    _cms_notify_type = "lift_sync"
    _cms_timeout = 10
    _history_limit = 20
    _ignore_when_busy = True

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._monitor_path = (config.get("monitor_path") or config.get("source_dir") or "").strip()
            self._monitor_actions = (config.get("monitor_actions") or "create").strip()
            self._webhook_token = (config.get("webhook_token") or "").strip()
            self._cms_domain = (config.get("cms_domain") or "").strip()
            self._cms_api_token = (config.get("cms_api_token") or "cloud_media_sync").strip()
            self._cms_notify_type = (config.get("cms_notify_type") or "lift_sync").strip()
            self._cms_timeout = int(config.get("cms_timeout") or 10)
            self._history_limit = int(config.get("history_limit") or 20)
            self._ignore_when_busy = self._to_bool(config.get("ignore_when_busy", True))

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/file_notify",
                "endpoint": self.handle_file_notify,
                "methods": ["POST"],
                "summary": "接收 CloudDrive2 文件事件",
                "description": "接收 CD2 file_system_watcher 回调，命中 create 事件和指定路径后通知 CMS 整理"
            },
            {
                "path": "/mount_notify",
                "endpoint": self.handle_mount_notify,
                "methods": ["POST"],
                "summary": "接收 CloudDrive2 挂载事件",
                "description": "接收 CD2 mount_point_watcher 回调，仅记录事件"
            },
            {
                "path": "/webhook/cd2",
                "endpoint": self.handle_file_notify,
                "methods": ["POST"],
                "summary": "兼容旧版 CD2 Webhook 路径",
                "description": "兼容路径，处理逻辑与 /file_notify 一致"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VCard",
                        "props": {"variant": "outlined", "class": "mt-3"},
                        "content": [
                            {
                                "component": "VCardText",
                                "content": [
                                    {
                                        "component": "VRow",
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VSwitch",
                                                    "props": {
                                                        "model": "enabled",
                                                        "label": "启用插件"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "cms_timeout",
                                                        "label": "CMS超时秒数",
                                                        "type": "number"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "history_limit",
                                                        "label": "历史保留条数",
                                                        "type": "number"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VSwitch",
                                                    "props": {
                                                        "model": "ignore_when_busy",
                                                        "label": "整理中跳过"
                                                    }
                                                }]
                                            }
                                        ]
                                    },
                                    {
                                        "component": "VRow",
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "monitor_path",
                                                        "label": "监听路径",
                                                        "placeholder": "/mnt/115/upload/inbox"
                                                    }
                                                }]
                                            }
                                        ]
                                    },
                                    {
                                        "component": "VRow",
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 6},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "monitor_actions",
                                                        "label": "监听动作",
                                                        "placeholder": "create"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 6},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "webhook_token",
                                                        "label": "Webhook令牌",
                                                        "placeholder": "留空则不校验"
                                                    }
                                                }]
                                            }
                                        ]
                                    },
                                    {
                                        "component": "VRow",
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 6},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "cms_domain",
                                                        "label": "CMS地址",
                                                        "placeholder": "http://127.0.0.1:9527"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 6},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "cms_api_token",
                                                        "label": "CMS_API_TOKEN",
                                                        "placeholder": "cloud_media_sync"
                                                    }
                                                }]
                                            }
                                        ]
                                    },
                                    {
                                        "component": "VRow",
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 6},
                                                "content": [{
                                                    "component": "VSelect",
                                                    "props": {
                                                        "model": "cms_notify_type",
                                                        "label": "CMS通知类型",
                                                        "items": [
                                                            {"title": "增量同步", "value": "lift_sync"},
                                                            {"title": "增量同步+自动整理", "value": "auto_organize"}
                                                        ]
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 6},
                                                "content": [{
                                                    "component": "VAlert",
                                                    "props": {
                                                        "type": "info",
                                                        "variant": "tonal"
                                                    },
                                                    "text": "CMS通知将直接调用 /api/sync/lift_by_token 接口，和 cmsnotify 插件的调用方式保持一致。"
                                                }]
                                            }
                                        ]
                                    },
                                    {
                                        "component": "VRow",
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12},
                                                "content": [{
                                                    "component": "VAlert",
                                                    "props": {
                                                        "type": "info",
                                                        "variant": "tonal"
                                                    },
                                                    "text": "当前流程：CD2云上传 -> 插件收到 create webhook -> 命中监听路径且 MP 当前无整理任务 -> 调用 CMS 增量同步接口。接口路径需带 MoviePilot API apikey，例如 /api/v1/plugin/CloudDrive2Webhook/file_notify?apikey=你的MP令牌。"
                                                }]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "monitor_path": "",
            "monitor_actions": "create",
            "webhook_token": "",
            "cms_domain": "http://127.0.0.1:9527",
            "cms_api_token": "cloud_media_sync",
            "cms_notify_type": "lift_sync",
            "cms_timeout": 10,
            "history_limit": 20,
            "ignore_when_busy": True
        }

    def get_page(self) -> List[dict]:
        history = self.get_data("history") or []
        rows = []
        for item in reversed(history):
            matched_count = len(item.get("matched_items") or [])
            rows.append({
                "component": "VTimelineItem",
                "props": {
                    "dot-color": "primary",
                    "size": "small"
                },
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "text-subtitle-2"},
                        "text": f"{item.get('time', '-')} | {item.get('status', '-')}"
                    },
                    {
                        "component": "div",
                        "props": {"class": "text-body-2"},
                        "text": f"识别事件: {item.get('event_name', '-')}"
                    },
                    {
                        "component": "div",
                        "props": {"class": "text-body-2"},
                        "text": f"命中项目数: {matched_count}"
                    }
                ]
            })

        return [{
            "component": "VCard",
            "props": {"variant": "outlined", "class": "mt-3"},
            "content": [
                {
                    "component": "VCardTitle",
                    "text": "最近处理记录"
                },
                {
                    "component": "VDivider"
                },
                {
                    "component": "VCardText",
                    "content": [{
                        "component": "VTimeline",
                        "props": {"density": "compact", "side": "end"},
                        "content": rows or [{
                            "component": "div",
                            "text": "暂无记录"
                        }]
                    }]
                }
            ]
        }]

    def stop_service(self):
        return

    async def handle_file_notify(self, request: Request):
        if not self._enabled:
            logger.info("CD2 file webhook 已收到请求，但插件未启用")
            return {"success": False, "message": "plugin disabled"}

        if not self._check_token(request):
            logger.warning("CD2 file webhook token 校验失败")
            return {"success": False, "message": "unauthorized"}

        payload = await self._read_payload(request)
        event_name = self._extract_event_name(payload)
        change_items = self._extract_change_items(payload)
        logger.info(
            "CD2 file webhook 已收到请求: device=%s user=%s event_category=%s event_name=%s changes=%s remote=%s",
            payload.get("device_name"),
            payload.get("user_name"),
            payload.get("event_category"),
            event_name,
            len(change_items),
            request.client.host if request.client else "unknown"
        )
        if change_items:
            logger.info(
                "CD2 file webhook 原始变更项: %s",
                json.dumps(change_items, ensure_ascii=False)
            )

        if not self._is_file_notify_event(payload):
            logger.info(
                "CD2 file webhook 已忽略: event_category=%s event_name=%s",
                payload.get("event_category"),
                event_name
            )
            self._save_history({
                "time": self._now(),
                "status": "ignored",
                "event_name": event_name or "unknown",
                "matched_items": [],
                "reason": "not_file_notify"
            })
            return {"success": True, "message": "ignored", "event_name": event_name}

        matched_items = self._match_change_items(change_items)
        if not matched_items:
            logger.info(
                "CD2 file webhook 未命中监听条件，已忽略: monitor_path=%s actions=%s changes=%s",
                self._monitor_path,
                self._monitor_actions,
                self._summarize_change_items(change_items)
            )
            self._save_history({
                "time": self._now(),
                "status": "ignored",
                "event_name": event_name or "notify",
                "matched_items": [],
                "reason": "no_matched_change"
            })
            return {"success": True, "message": "ignored", "event_name": event_name, "matched": 0}

        logger.info(
            "CD2 file webhook 已命中变更项: %s",
            json.dumps(matched_items, ensure_ascii=False)
        )

        try:
            logger.info("CD2 file webhook 进入后续处理: matched=%s ignore_when_busy=%s", len(matched_items), self._ignore_when_busy)
            if self._ignore_when_busy:
                busy = self._has_running_organize_task()
                logger.info("CD2 file webhook 整理任务检测结果: busy=%s", busy)
                if busy:
                    logger.info("CD2 file webhook 命中但当前存在整理任务，跳过 CMS 通知")
                    self._save_history({
                        "time": self._now(),
                        "status": "skipped_busy",
                        "event_name": event_name or "notify",
                        "matched_items": matched_items,
                        "reason": "organize_task_running"
                    })
                    return {
                        "success": True,
                        "message": "skipped_busy",
                        "event_name": event_name,
                        "matched": len(matched_items)
                    }

            logger.info("CD2 file webhook 准备通知 CMS: matched=%s ignore_when_busy=%s", len(matched_items), self._ignore_when_busy)
            cms_result = self._notify_cms(
                payload=payload,
                event_name=event_name,
                matched_items=matched_items
            )
            logger.info(
                "CD2 file webhook 处理完成: matched=%s cms_success=%s",
                len(matched_items),
                cms_result.get("success")
            )
            self._save_history({
                "time": self._now(),
                "status": "notified" if cms_result.get("success") else "notify_failed",
                "event_name": event_name or "notify",
                "matched_items": matched_items,
                "cms_result": cms_result
            })
            return {
                "success": True,
                "message": "notified" if cms_result.get("success") else "notify_failed",
                "event_name": event_name,
                "matched": len(matched_items),
                "cms_result": cms_result
            }
        except Exception as exc:
            logger.error("CD2 file webhook 后续处理异常: %s", exc, exc_info=True)
            self._save_history({
                "time": self._now(),
                "status": "failed",
                "event_name": event_name or "notify",
                "matched_items": matched_items,
                "error": str(exc)
            })
            return {
                "success": False,
                "message": str(exc),
                "event_name": event_name,
                "matched": len(matched_items)
            }

    async def handle_mount_notify(self, request: Request):
        if not self._enabled:
            logger.info("CD2 mount webhook 已收到请求，但插件未启用")
            return {"success": False, "message": "plugin disabled"}

        if not self._check_token(request):
            logger.warning("CD2 mount webhook token 校验失败")
            return {"success": False, "message": "unauthorized"}

        payload = await self._read_payload(request)
        event_name = self._extract_event_name(payload)
        logger.info(
            "CD2 mount webhook 已收到请求: device=%s user=%s event_name=%s remote=%s",
            payload.get("device_name"),
            payload.get("user_name"),
            event_name,
            request.client.host if request.client else "unknown"
        )
        self._save_history({
            "time": self._now(),
            "status": "mount_event",
            "event_name": event_name or "unknown",
            "matched_items": []
        })
        return {"success": True, "message": "recorded", "event_name": event_name}

    async def _read_payload(self, request: Request) -> Dict[str, Any]:
        try:
            return await request.json()
        except Exception:
            raw_body = await request.body()
            if not raw_body:
                return {}
            return {"raw_body": raw_body.decode("utf-8", errors="ignore")}

    def _check_token(self, request: Request) -> bool:
        if not self._webhook_token:
            return True

        auth_header = request.headers.get("Authorization", "")
        bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        basic_token = auth_header[6:].strip() if auth_header.lower().startswith("basic ") else ""
        header_token = request.headers.get("X-Webhook-Token", "") or request.headers.get("X-CD2-Token", "")
        query_token = request.query_params.get("token", "")
        return self._webhook_token in {auth_header, bearer_token, basic_token, header_token, query_token}

    def _extract_event_name(self, payload: Dict[str, Any]) -> str:
        candidates = [
            payload.get("event"),
            payload.get("event_name"),
            payload.get("eventName"),
            payload.get("type"),
            payload.get("name"),
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def _is_file_notify_event(self, payload: Dict[str, Any]) -> bool:
        event_category = str(payload.get("event_category") or "").strip().lower()
        event_name = str(payload.get("event_name") or "").strip().lower()
        return event_category == "file" and event_name == "notify"

    def _extract_change_items(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _summarize_change_items(self, change_items: List[Dict[str, Any]]) -> List[str]:
        summary = []
        for item in change_items[:5]:
            summary.append(
                f"{item.get('action')}:{item.get('source_file')}->{item.get('destination_file')}"
            )
        return summary

    def _match_change_items(self, change_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self._monitor_path:
            return []

        monitor_path = Path(self._monitor_path).expanduser()
        allowed_actions = {
            item.strip().lower() for item in self._monitor_actions.split(",") if item.strip()
        }

        matched_items = []
        for item in change_items:
            action = str(item.get("action") or "").strip().lower()
            if allowed_actions and action not in allowed_actions:
                logger.info(
                    "CD2 file webhook 忽略变更项: action=%s 不在监听动作中 allowed=%s source=%s destination=%s",
                    action,
                    sorted(allowed_actions),
                    item.get("source_file"),
                    item.get("destination_file")
                )
                continue

            source_file = item.get("source_file")
            destination_file = item.get("destination_file")
            candidates = [path for path in [source_file, destination_file] if isinstance(path, str) and path.strip()]
            if any(self._path_in_dir(path, monitor_path) for path in candidates):
                matched_items.append(item)
            else:
                logger.info(
                    "CD2 file webhook 忽略变更项: 路径未命中 monitor_path=%s source=%s destination=%s",
                    self._normalize_path(str(monitor_path)),
                    source_file,
                    destination_file
                )

        return matched_items

    @staticmethod
    def _path_in_dir(raw_path: str, base_dir: Path) -> bool:
        try:
            path_text = CloudDrive2Webhook._normalize_path(str(Path(raw_path).expanduser()))
            base_text = CloudDrive2Webhook._normalize_path(str(base_dir))
            return base_text == "/" or path_text == base_text or path_text.startswith(f"{base_text}/")
        except Exception:
            return False

    @staticmethod
    def _normalize_path(raw_path: str) -> str:
        normalized = re.sub(r"/+", "/", raw_path.strip())
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return posixpath.normpath(normalized)

    def _has_running_organize_task(self) -> bool:
        progress = {}
        try:
            progress = ProgressHelper().get(ProgressKey.FileTransfer) or {}
        except TypeError:
            try:
                helper = ProgressHelper(ProgressKey.FileTransfer)
                if hasattr(helper, "get"):
                    progress = helper.get() or {}
                elif hasattr(helper, "get_process"):
                    progress = helper.get_process() or {}
            except Exception as exc:
                logger.warning("读取整理任务进度失败，按无整理任务处理: %s", exc)
                return False
        except Exception as exc:
            logger.warning("读取整理任务进度失败，按无整理任务处理: %s", exc)
            return False

        if progress.get("enable"):
            logger.info("检测到 MP 当前存在整理任务: %s", progress.get("text"))
            return True
        return False

    def _notify_cms(
        self,
        payload: Dict[str, Any],
        event_name: str,
        matched_items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not self._cms_domain or not self._cms_api_token:
            return {"success": False, "message": "cms_domain or cms_api_token not configured"}

        query = urllib.parse.urlencode({
            "token": self._cms_api_token,
            "type": self._cms_notify_type or "lift_sync"
        })
        endpoint = f"{self._cms_domain.rstrip('/')}/api/sync/lift_by_token"
        request_url = f"{endpoint}?{query}"
        request = urllib.request.Request(url=request_url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._cms_timeout) as response:
                # 响应可能回显凭据，不写入历史或 Webhook 响应。
                response.read()
                logger.info(
                    "CD2 webhook CMS通知成功，状态码: %s，event=%s，matched=%s",
                    response.status,
                    event_name,
                    len(matched_items)
                )
                return {
                    "success": True,
                    "endpoint": endpoint,
                    "status_code": response.status,
                    "device_name": payload.get("device_name"),
                    "user_name": payload.get("user_name")
                }
        except urllib.error.HTTPError as exc:
            exc.close()
            logger.error("CD2 webhook CMS通知失败，状态码: %s", exc.code)
            return {
                "success": False,
                "endpoint": endpoint,
                "status_code": exc.code,
                "message": "CMS 返回 HTTP 错误"
            }
        except Exception as exc:
            logger.error("CD2 webhook CMS通知异常，未保存请求地址或凭据")
            return {
                "success": False,
                "endpoint": endpoint,
                "message": "CMS 连接失败或超时"
            }

    def _save_history(self, record: Dict[str, Any]):
        history = self.get_data("history") or []
        history.append(record)
        limit = max(self._history_limit, 1)
        if len(history) > limit:
            history = history[-limit:]
        self.save_data(key="history", value=history)

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
