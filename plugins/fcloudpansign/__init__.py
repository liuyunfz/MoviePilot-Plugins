"""F-Cloudpan daily check-in using per-user OAuth application authorization."""

import copy
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import ClassVar, Literal

import pytz
from app.core.config import settings
from app.db.user_oper import get_current_active_superuser
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Body, Depends, HTTPException

from .client import DEVICE_GRANT, CloudClient, CloudError, origin_url
from .ui import authorization_ready, build_form, build_page


class FCloudpanSign(_PluginBase):
    plugin_name = "F-Cloudpan 签到"
    plugin_desc = "通过应用授权自动签到，读取积分、VIP 与签到记录"
    plugin_icon = "https://raw.githubusercontent.com/liuyunfz/MoviePilot-Plugins/main/icons/fcloudpansign.png"
    plugin_version = "1.1.2"
    plugin_author = "liuyunfz"
    author_url = "https://github.com/liuyunfz"
    plugin_config_prefix = "fcloudpansign_"
    plugin_order = 26
    auth_level = 2
    # Covers scheduler jobs, device authorization and plugin reloads in one MP process.
    _lock = threading.RLock()
    _scheduler = None
    _generation = ""
    _config: ClassVar[dict] = {}
    _config_error = ""
    # Public release identity, set by the maintainer after production registration.
    # These identifiers are public; never add a Client Secret or local test ID here.
    FCLOUDPAN_ORIGIN = "https://fcloudpan.com"
    OAUTH_CLIENT_ID = "app_TobngQ3HIC-XGWODMEDA40k4zO9nPElvzFyh1a9QzpM"
    _issuer = ""
    _client_id = ""
    DEFAULTS: ClassVar[dict] = {
        "enabled": False,
        "use_proxy": True,
        "notify": False,
        "onlyonce": False,
        "refresh_now": False,
        "prepare_auth": False,
        "revoke_auth": False,
        "cancel_auth": False,
        "cron": "30 9 * * *",
        "mode": "STANDARD",
        "history_days": 30,
        "timeout": 20,
        "jitter": 300,
    }

    def _read(self):
        try:
            value = self.get_data("session")
        except Exception:  # noqa: BLE001 -- database exceptions may embed credential values
            raise CloudError("插件授权状态读取失败，请检查 MoviePilot 存储") from None
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def _save(self, state):
        # One persisted record keeps rotated tokens and refresh-in-flight marker together.
        try:
            self.save_data("session", copy.deepcopy(state))
        except Exception:  # noqa: BLE001 -- never expose SQL parameters containing tokens
            raise CloudError("插件授权状态保存失败，请检查 MoviePilot 存储") from None

    def _client(self):
        if self._config_error:
            raise CloudError(self._config_error)
        return CloudClient(
            self._issuer,
            self._client_id,
            self._config["timeout"],
            proxies=getattr(settings, "PROXY", None)
            if self._config.get("use_proxy", True)
            else None,
        )

    def init_plugin(self, config=None):
        self.stop_service()
        with self._lock:
            self._generation = secrets.token_hex(16)
            self._config = {
                key: (config or {}).get(key, value)
                for key, value in self.DEFAULTS.items()
            }
            self._config_error = ""
            self._issuer = ""
            self._client_id = ""
            try:
                if not self.FCLOUDPAN_ORIGIN or not self.OAUTH_CLIENT_ID:
                    raise CloudError(
                        "当前开发版本尚未内置正式应用，请等待维护者完成应用配置"
                    )
                self._issuer = origin_url(self.FCLOUDPAN_ORIGIN)
                self._client_id = self.OAUTH_CLIENT_ID.strip()
                if not self._client_id or len(self._client_id) > 256:
                    raise CloudError("内置应用标识无效，请联系插件维护者")
                for key, low, high in (
                    ("history_days", 1, 365),
                    ("timeout", 5, 60),
                    ("jitter", 0, 1800),
                ):
                    self._config[key] = max(
                        low, min(high, int(self._config.get(key, self.DEFAULTS[key])))
                    )
                if self._config["mode"] not in ("STANDARD", "LAS_VEGAS"):
                    raise CloudError("请选择普通签到或随机签到")
            except (CloudError, ValueError, TypeError) as exc:
                self._config_error = (
                    str(exc)
                    if isinstance(exc, CloudError)
                    else "数值配置无效，请检查超时、延迟和保留天数"
                )

            actions = [
                key
                for key in (
                    "prepare_auth",
                    "cancel_auth",
                    "revoke_auth",
                    "onlyonce",
                    "refresh_now",
                )
                if self._config.get(key)
            ]
            # One-shot settings are reset before any network call or scheduling.
            for key in actions:
                self._config[key] = False
            if (
                actions
                or "use_proxy" not in (config or {})
                or any(
                    key in (config or {})
                    for key in ("mp_url", "client_secret", "issuer", "client_id")
                )
            ):
                self.update_config(dict(self._config))
            if self._config_error:
                return

            fingerprint = hashlib.sha256(
                (f"public-device-v1\0{self._issuer}\0{self._client_id}").encode()
            ).hexdigest()
            state = self._read()
            if state.get("binding") != fingerprint:
                state = {
                    "binding": fingerprint,
                    "status": "请连接公共应用；从 1.0 升级需重新授权，并在云盘撤销旧应用授权",
                    "history": [],
                }
                self._save(state)
            if state.get("tokens") and "grant_expiry_verified" not in state:
                # Old plugin versions estimated a new 30-day window per device.
                # That estimate is invalid now that all devices share one app expiry.
                deadline = state["tokens"].get("authorization_expires_at")
                state.update(
                    grant_expires_at=deadline,
                    grant_expiry_verified=deadline is not None,
                )
                self._save(state)
            if "revoke_auth" in actions:
                self._revoke()
                return
            if "cancel_auth" in actions:
                state = self._read()
                state.pop("pending", None)
                state["status"] = "本次连接已取消；已有授权保持不变"
                self._save(state)
            elif "prepare_auth" in actions:
                self._prepare_device()

            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._schedule_poll()
            self._schedule_daily()
            if authorization_ready(self._read()) and (
                "onlyonce" in actions or "refresh_now" in actions
            ):
                self._scheduler.add_job(
                    self._scheduled,
                    trigger="date",
                    id="once",
                    name="云盘立即执行",
                    run_date=datetime.now(pytz.timezone(settings.TZ))
                    + timedelta(seconds=3),
                    args=[self._generation, "onlyonce" in actions],
                    misfire_grace_time=600,
                )
            if self._scheduler.get_jobs():
                self._scheduler.start()

    def _schedule_daily(self):
        if not self._scheduler or not authorization_ready(self._read()):
            return
        if self._config["enabled"]:
            try:
                trigger = CronTrigger.from_crontab(
                    self._config["cron"], timezone=settings.TZ
                )
                trigger.jitter = self._config["jitter"]
                self._scheduler.add_job(
                    self._scheduled,
                    trigger=trigger,
                    id="daily",
                    replace_existing=True,
                    name=self.plugin_name,
                    args=[self._generation, True],
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=600,
                )
            except (ValueError, TypeError):
                state = self._read()
                state["status"] = "Cron 表达式无效，定时任务未启动"
                self._save(state)

    def _scheduled(self, generation, sign):
        with self._lock:
            if generation != self._generation or not authorization_ready(self._read()):
                return
            self.run(sign=sign)

    def _revoke(self):
        state = self._read()
        token = (state.get("tokens") or {}).get("refresh_token")
        if token:
            try:
                self._client().request("POST", "/oauth/revoke", form={"token": token})
            except CloudError as exc:
                state["status"] = (
                    "撤销未确认：" + str(exc) + "；请在云盘个人中心撤销或再次执行"
                )
                state["blocked"] = True
                state.pop("pending", None)
                self._save(state)
                return
        self._save(
            {"binding": state.get("binding"), "status": "已解除授权", "history": []}
        )

    def _prepare_device(self):
        state = self._read()
        # A new code is only requested by an explicit user action.
        state.pop("pending", None)
        state["status"] = "正在申请设备授权"
        self._save(state)
        try:
            device = self._client().device_authorization()
        except CloudError as exc:
            state["status"] = "连接未开始：" + str(exc)
            self._save(state)
            return
        now = time.time()
        state["pending"] = {
            **device,
            "expires_at": now + device["expires_in"],
            "next_poll_at": now + device["interval"],
        }
        state["status"] = "等待确认，请在当前数据页或重新打开设置页查看授权链接与确认码"
        self._save(state)

    def _schedule_poll(self):
        state = self._read()
        pending = state.get("pending") or {}
        if not pending.get("device_code") or not self._scheduler:
            return
        now = time.time()
        if pending["expires_at"] <= now:
            state.pop("pending", None)
            state["status"] = "设备授权码已过期，请重新连接"
            self._save(state)
            return
        if pending.get("in_flight"):
            # Process may have stopped after exchange: never immediately replay on restart.
            pending["interval"] = max(
                pending["interval"], min(120, pending["interval"] * 2)
            )
            pending["next_poll_at"] = max(
                pending["next_poll_at"], now + pending["interval"]
            )
            pending.pop("in_flight", None)
            self._save(state)
        self._scheduler.add_job(
            self._poll_device,
            trigger="date",
            id="device_auth",
            name="云盘设备授权确认",
            replace_existing=True,
            run_date=datetime.fromtimestamp(
                min(pending["expires_at"], max(now + 1, pending["next_poll_at"])),
                pytz.timezone(settings.TZ),
            ),
            args=[self._generation],
            max_instances=1,
            misfire_grace_time=600,
        )

    def _poll_device(self, generation):
        with self._lock:
            if generation != self._generation:
                return
            state = self._read()
            pending = state.get("pending") or {}
            if not pending.get("device_code"):
                return
            now = time.time()
            if now >= pending["expires_at"]:
                state.pop("pending", None)
                state["status"] = "设备授权码已过期，请重新连接"
                self._save(state)
                return
            if now < pending["next_poll_at"]:
                self._schedule_poll()
                return
            pending["in_flight"] = True
            pending["next_poll_at"] = now + pending["interval"]
            self._save(state)
            try:
                tokens = self._client().tokens(
                    {
                        "grant_type": DEVICE_GRANT,
                        "device_code": pending["device_code"],
                    }
                )
            except CloudError as exc:
                pending.pop("in_flight", None)
                if (
                    exc.code in ("authorization_pending", "slow_down")
                    and exc.status == 400
                ):
                    if exc.code == "slow_down":
                        pending["interval"] += 5
                    state["status"] = str(exc)
                elif not exc.code and (
                    exc.status == 0 or exc.status == 429 or exc.status >= 500
                ):
                    pending["interval"] = max(
                        pending["interval"],
                        min(120, pending["interval"] * 2),
                        exc.retry_after,
                    )
                    state["status"] = "授权查询暂未确认，已降低频率；请勿重复连接"
                else:
                    state.pop("pending", None)
                    state["status"] = "连接已停止：" + str(exc)
                pending["next_poll_at"] = time.time() + pending["interval"]
                self._save(state)
                self._schedule_poll()
                return
            now = time.time()
            tokens["expires_at"] = now + tokens["expires_in"]
            # Store a new grant before reading its identity. Persistence failures leave the
            # old in-flight marker intact, never overwrite a successfully issued pair.
            state = {
                "binding": state.get("binding"),
                "tokens": tokens,
                "grant_expires_at": tokens.get("authorization_expires_at"),
                "grant_expiry_verified": "authorization_expires_at" in tokens,
                "status": "授权成功，等待读取资料",
                "history": [],
            }
            self._save(state)
            try:
                self._sync(state, tokens["access_token"])
                state["status"] = "已授权"
            except CloudError:
                state["status"] = "授权成功；资料暂未读取，可选择立即刷新资料"
            self._save(state)
            self._schedule_daily()

    def _access(self, state, force=False):
        tokens = state.get("tokens") or {}
        if not tokens or state.get("blocked"):
            raise CloudError("尚未授权或授权已停止，请在配置页连接账号")
        if state.get("refresh_in_flight"):
            raise CloudError("上次刷新结果未知，请重新授权；已停止重试旧刷新令牌")
        deadline = state.get("grant_expires_at")
        if deadline is not None and deadline <= time.time():
            raise CloudError("应用授权已到期，请重新授权")
        margin = min(90, max(0.1, tokens.get("expires_in", 3600) / 10))
        if not force and tokens.get("expires_at", 0) > time.time() + margin:
            return tokens["access_token"]
        state["refresh_in_flight"] = True
        self._save(state)
        new_tokens = self._client().tokens(
            {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
        )
        new_tokens["expires_at"] = time.time() + new_tokens["expires_in"]
        state.update(tokens=new_tokens, refresh_in_flight=False)
        if "authorization_expires_at" in new_tokens:
            state.update(
                grant_expires_at=new_tokens["authorization_expires_at"],
                grant_expiry_verified=True,
            )
        self._save(state)
        return new_tokens["access_token"]

    def _authenticated(self, state, operation):
        token = self._access(state)
        try:
            return operation(token)
        except CloudError as exc:
            if exc.status != 401:
                raise
            return operation(self._access(state, force=True))

    def _sync(self, state, token):
        account = self._client().account(token)
        previous = state.get("account") or {}
        if previous.get("sub") and previous["sub"] != account["sub"]:
            state["blocked"] = True
            state.pop("account", None)
            state.pop("overview", None)
            state["history"] = []
            self._save(state)
            raise CloudError("授权用户发生变化，已停止操作，请重新授权")
        overview = self._client().check_in(token)
        account["points"] = overview["points"]
        state.update(account=account, overview=overview, updated_at=time.time())
        self._save(state)
        return overview

    def run(self, sign=True):
        with self._lock:
            started = time.monotonic()
            state = self._read()
            result = None
            status = "资料已刷新"
            detail = ""
            try:
                self._client()
                overview = self._authenticated(
                    state, lambda token: self._sync(state, token)
                )
                if sign:
                    if overview["checkedInToday"]:
                        result = {
                            "alreadyCheckedIn": True,
                            "reward": overview.get("todayReward"),
                            "balanceAfter": overview["points"],
                        }
                        status = "今日已签到"
                    else:
                        try:
                            result = self._authenticated(
                                state,
                                lambda token: self._client().check_in(
                                    token, self._config["mode"]
                                ),
                            )
                        except CloudError as exc:
                            # POST may have committed before timeout. Read back, never blindly repeat it.
                            if exc.status == 0 or exc.status >= 500:
                                confirmed = self._authenticated(
                                    state, lambda token: self._sync(state, token)
                                )
                                if confirmed["checkedInToday"]:
                                    result = {
                                        "alreadyCheckedIn": True,
                                        "reward": confirmed.get("todayReward"),
                                        "balanceAfter": confirmed["points"],
                                    }
                                    detail = (
                                        "提交响应未确认，已从服务端记录核实今日签到"
                                    )
                                else:
                                    raise CloudError(
                                        "签到结果未确认，本次未重复提交；请稍后再次执行"
                                    ) from None
                            else:
                                raise
                        status = (
                            "今日已签到" if result["alreadyCheckedIn"] else "签到成功"
                        )
                        # Preserve confirmed settlement even if the subsequent read fails.
                        state["account"]["points"] = result["balanceAfter"]
                        state["overview"].update(
                            checkedInToday=True,
                            todayReward=result["reward"],
                            points=result["balanceAfter"],
                        )
                        self._save(state)
                        try:
                            self._authenticated(
                                state, lambda token: self._sync(state, token)
                            )
                        except CloudError:
                            detail = "签到已确认；资料刷新失败，余额为本次签到结算余额"
                state["status"] = status
            except CloudError as exc:
                status = "签到未完成" if sign else "资料刷新失败"
                detail = str(exc)
                state["status"] = detail
            except Exception:  # noqa: BLE001 -- third-party exceptions may contain credentials
                # Do not serialize exception/request objects that may include credentials.
                logger.error("F-Cloudpan 插件内部处理失败，请检查本地插件存储与配置")
                return {"success": False, "status": "插件内部处理失败"}
            record = {
                "time": time.time(),
                "action": "签到" if sign else "读取资料",
                "status": status,
                "detail": detail,
                "reward": result.get("reward") if result else None,
                "balance": (state.get("account") or {}).get("points"),
                "mode": self._config["mode"]
                if result and not result.get("alreadyCheckedIn")
                else None,
                "seconds": round(time.monotonic() - started, 2),
            }
            cutoff = time.time() - self._config["history_days"] * 86400
            state["history"] = (
                [record]
                + [
                    row
                    for row in state.get("history", [])
                    if row.get("time", 0) >= cutoff
                ]
            )[:500]
            self._save(state)
            logger.info(f"F-Cloudpan {status}，耗时 {record['seconds']:.2f} 秒")
            if sign and self._config["notify"]:
                account = state.get("account") or {}
                overview = state.get("overview") or {}
                text = (
                    f"👤 {account.get('name', '未读取')}\n"
                    f"🎁 今日奖励：{result.get('reward') if result else '未确认'}\n"
                    f"💰 {overview.get('pointName', '积分')}余额：{record['balance'] if record['balance'] is not None else '未读取'}\n"
                    f"🔥 连续签到：{overview.get('currentStreak', '—')} 天\n"
                    f"📅 累计签到：{overview.get('totalDays', '—')} 天\n{detail}"
                )
                try:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title=f"F-Cloudpan · {status}",
                        text=text,
                    )
                except Exception:  # noqa: BLE001 -- notification errors must not expose credentials
                    logger.warning("F-Cloudpan 签到结果已保存，通知投递失败")
            return {
                "success": status in ("签到成功", "今日已签到", "资料已刷新"),
                "status": status,
            }

    def get_state(self):
        with self._lock:
            return bool(self._config.get("enabled")) and authorization_ready(
                self._read()
            )

    @staticmethod
    def get_command():
        return []

    def get_service(self):
        return []

    def get_api(self):
        return [
            {
                "path": "/action",
                "endpoint": self.action,
                "methods": ["POST"],
                "summary": "F-Cloudpan 连接管理",
                "auth": "bear",
                "dependencies": [Depends(get_current_active_superuser)],
            }
        ]

    def action(
        self,
        action: Literal["connect", "cancel", "revoke"] = Body(..., embed=True),
        confirmed: bool = Body(False),
    ):
        with self._lock:
            if self._config_error:
                raise HTTPException(400, self._config_error)
            if action == "revoke" and confirmed is not True:
                raise HTTPException(400, "请先确认断开本设备授权")
            try:
                state = self._read()
                pending = state.get("pending") or {}
                # Repeated clicks must not replace a live code or revoke twice.
                if action == "connect" and pending.get("expires_at", 0) > time.time():
                    return {"success": True, "status": state.get("status", "等待确认")}
                if action == "cancel" and not pending:
                    return {
                        "success": True,
                        "status": state.get("status", "没有待取消的连接"),
                    }
                if action == "revoke" and not state.get("tokens") and not pending:
                    return {"success": True, "status": "本设备未连接"}
                config = {**self._config}
                for key in (
                    "prepare_auth",
                    "cancel_auth",
                    "revoke_auth",
                    "onlyonce",
                    "refresh_now",
                ):
                    config[key] = False
                config[
                    {
                        "connect": "prepare_auth",
                        "cancel": "cancel_auth",
                        "revoke": "revoke_auth",
                    }[action]
                ] = True
                self.init_plugin(config)
                state = self._read()
                success = (
                    bool(state.get("pending"))
                    if action == "connect"
                    else not state.get("pending")
                    if action == "cancel"
                    else not state.get("tokens")
                )
                return {"success": success, "status": state.get("status", "")}
            except CloudError as exc:
                raise HTTPException(503, str(exc)) from None

    def get_form(self):
        with self._lock:
            return build_form(
                bool(self.FCLOUDPAN_ORIGIN and self.OAUTH_CLIENT_ID),
                {} if self._config_error else self._read(),
                self._config_error,
            ), dict(self.DEFAULTS)

    def get_page(self):
        with self._lock:
            next_run = None
            if self._scheduler and self._scheduler.running:
                job = self._scheduler.get_job("daily")
                next_run = (
                    job.next_run_time.timestamp() if job and job.next_run_time else None
                )
            return build_page(
                {} if self._config_error else self._read(),
                self._issuer,
                self._config_error,
                next_run,
            )

    def stop_service(self):
        with self._lock:
            self._generation = ""
            if self._scheduler:
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
