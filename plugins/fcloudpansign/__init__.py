"""F-Cloudpan daily check-in using per-user OAuth application authorization."""

import copy
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import ClassVar
from urllib.parse import urlencode

import pytz
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .client import SCOPES, CloudClient, CloudError, challenge, origin_url
from .ui import build_form, build_page


class FCloudpanSign(_PluginBase):
    plugin_name = "F-Cloudpan 签到"
    plugin_desc = "通过应用授权自动签到，读取积分、VIP 与签到记录"
    plugin_icon = "https://raw.githubusercontent.com/liuyunfz/MoviePilot-Plugins/main/icons/fcloudpansign.svg"
    plugin_version = "1.0.0"
    plugin_author = "liuyunfz"
    author_url = "https://github.com/liuyunfz"
    plugin_config_prefix = "fcloudpansign_"
    plugin_order = 26
    auth_level = 2
    # Covers scheduler jobs, API callbacks and plugin reloads in one MP process.
    _lock = threading.RLock()
    _scheduler = None
    _generation = ""
    _config: ClassVar[dict] = {}
    _config_error = ""
    _cookie_name = "fcloudpan_oauth"
    _api_path = "/api/v1/plugin/FCloudpanSign"
    DEFAULTS: ClassVar[dict] = {
        "enabled": False,
        "notify": False,
        "onlyonce": False,
        "refresh_now": False,
        "prepare_auth": False,
        "revoke_auth": False,
        "issuer": "",
        "mp_url": "",
        "client_id": "",
        "client_secret": "",
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
            self._config["issuer"],
            self._config["client_id"],
            self._config["client_secret"],
            self._config["timeout"],
        )

    def _redirect_uri(self):
        return self._config["mp_url"] + self._api_path + "/oauth/callback"

    def init_plugin(self, config=None):
        self.stop_service()
        with self._lock:
            self._generation = secrets.token_hex(16)
            self._config = {**self.DEFAULTS, **(config or {})}
            self._config_error = ""
            try:
                for key in ("issuer", "mp_url"):
                    self._config[key] = origin_url(self._config[key])
                for key in ("client_id", "client_secret"):
                    self._config[key] = str(self._config.get(key) or "").strip()
                    if not self._config[key]:
                        raise CloudError(
                            "请先填写云盘地址、MoviePilot 地址及应用 Client ID / Secret"
                        )
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
                for key in ("prepare_auth", "revoke_auth", "onlyonce", "refresh_now")
                if self._config.get(key)
            ]
            # One-shot settings are reset before any network call or scheduling.
            for key in actions:
                self._config[key] = False
            if actions:
                self.update_config(dict(self._config))
            if self._config_error:
                return

            fingerprint = hashlib.sha256(
                "\0".join(
                    self._config[key]
                    for key in ("issuer", "mp_url", "client_id", "client_secret")
                ).encode()
            ).hexdigest()
            state = self._read()
            if state.get("binding") != fingerprint:
                state = {
                    "binding": fingerprint,
                    "status": "配置已更新，请授权",
                    "history": [],
                }
                self._save(state)
            if "revoke_auth" in actions:
                self._revoke()
                return
            if "prepare_auth" in actions:
                state = self._read()
                state["pending"] = {
                    "ticket": secrets.token_urlsafe(32),
                    "state": secrets.token_urlsafe(32),
                    "verifier": secrets.token_urlsafe(48),
                    "expires_at": time.time() + 600,
                    "redirect_uri": self._redirect_uri(),
                }
                state["status"] = "授权链接已生成，请打开插件详情继续"
                self._save(state)

            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
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
            if "onlyonce" in actions or "refresh_now" in actions:
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

    def _scheduled(self, generation, sign):
        with self._lock:
            if generation != self._generation:
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

    @staticmethod
    def _html(message, status=200):
        # All messages are fixed local strings, never authorization parameters or upstream HTML.
        response = HTMLResponse(
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>F-Cloudpan 授权</title><body><h2>F-Cloudpan × MoviePilot</h2><p>"
            + message
            + "</p><p>请关闭此页，返回 MoviePilot 插件详情。</p></body></html>",
            status_code=status,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )
        return response

    def oauth_start(self, request: Request):
        with self._lock:
            state = self._read()
            pending = state.get("pending") or {}
            tickets = request.query_params.getlist("ticket")
            if (
                self._config_error
                or len(tickets) != 1
                or not pending.get("ticket")
                or not secrets.compare_digest(
                    tickets[0].encode(), pending["ticket"].encode()
                )
                or pending.get("expires_at", 0) <= time.time()
                or pending.get("started")
            ):
                return self._html("授权入口无效或已使用，请在配置页重新生成。", 400)
            browser_secret = secrets.token_urlsafe(32)
            pending.update(
                started=True,
                browser_hash=hashlib.sha256(browser_secret.encode()).hexdigest(),
            )
            self._save(state)
            url = (
                self._config["issuer"]
                + "/oauth/authorize?"
                + urlencode(
                    {
                        "client_id": self._config["client_id"],
                        "redirect_uri": pending["redirect_uri"],
                        "response_type": "code",
                        "scope": SCOPES,
                        "state": pending["state"],
                        "code_challenge": challenge(pending["verifier"]),
                        "code_challenge_method": "S256",
                    }
                )
            )
            response = RedirectResponse(
                url,
                status_code=302,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
            response.set_cookie(
                self._cookie_name,
                browser_secret,
                httponly=True,
                samesite="lax",
                secure=self._config["mp_url"].startswith("https://"),
                max_age=600,
                path=self._api_path + "/oauth/callback",
            )
            return response

    def oauth_callback(self, request: Request):
        with self._lock:
            state = self._read()
            pending = state.get("pending") or {}
            params = request.query_params
            cookie = request.cookies.get(self._cookie_name, "")
            valid = (
                not self._config_error
                and pending.get("started")
                and cookie
                and pending.get("expires_at", 0) > time.time()
                and all(len(params.getlist(key)) == 1 for key in ("state", "iss"))
                and all(len(params.getlist(key)) <= 1 for key in ("code", "error"))
                and secrets.compare_digest(
                    params.get("state", "").encode(), pending.get("state", "").encode()
                )
                and params.get("iss") == self._config["issuer"]
                and secrets.compare_digest(
                    hashlib.sha256(cookie.encode()).hexdigest(),
                    pending.get("browser_hash", ""),
                )
            )
            if not valid:
                return self._html(
                    "授权校验失败或已过期，请使用同一浏览器重新发起。", 400
                )
            state.pop("pending", None)
            self._save(
                state
            )  # Consume locally before the one-shot token exchange, including on crashes.
            if (
                params.get("error")
                or not params.get("code")
                or len(params["code"]) > 1024
            ):
                state["status"] = "授权未完成；已有授权保持不变"
                self._save(state)
                response = self._html("授权未完成，未更改已有账号。", 400)
            else:
                try:
                    tokens = self._client().tokens(
                        {
                            "grant_type": "authorization_code",
                            "code": params["code"],
                            "redirect_uri": pending["redirect_uri"],
                            "code_verifier": pending["verifier"],
                        }
                    )
                    now = time.time()
                    tokens["expires_at"] = now + tokens["expires_in"]
                    # New grant: drop old identity/history before loading the newly authorized account.
                    state = {
                        "binding": state.get("binding"),
                        "tokens": tokens,
                        "grant_expires_at": now + 30 * 86400,
                        "status": "授权成功，等待读取资料",
                        "history": [],
                    }
                    self._save(state)
                    try:
                        self._sync(state, tokens["access_token"])
                        state["status"] = "已授权"
                        self._save(state)
                        message = "授权成功，已读取账号和签到资料。"
                    except CloudError:
                        message = (
                            "授权成功；资料暂未读取，可在配置页选择“立即刷新资料”。"
                        )
                    response = self._html(message)
                except CloudError:
                    response = self._html(
                        "令牌交换未成功或结果未知，请重新发起授权。", 400
                    )
                    state["status"] = "本次授权交换未确认，请重新发起"
                    self._save(state)
            response.delete_cookie(
                self._cookie_name, path=self._api_path + "/oauth/callback"
            )
            return response

    def _access(self, state, force=False):
        tokens = state.get("tokens") or {}
        if not tokens or state.get("blocked"):
            raise CloudError("尚未授权或授权已停止，请在配置页生成授权链接")
        if state.get("refresh_in_flight"):
            raise CloudError("上次刷新结果未知，请重新授权；已停止重试旧刷新令牌")
        if state.get("grant_expires_at", 0) <= time.time():
            raise CloudError("30 天授权已到期，请重新授权")
        if not force and tokens.get("expires_at", 0) > time.time() + 90:
            return tokens["access_token"]
        state["refresh_in_flight"] = True
        self._save(state)
        new_tokens = self._client().tokens(
            {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
        )
        new_tokens["expires_at"] = time.time() + new_tokens["expires_in"]
        state.update(tokens=new_tokens, refresh_in_flight=False)
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
                        title=f"🐝 F-Cloudpan · {status}",
                        text=text,
                    )
                except Exception:  # noqa: BLE001 -- notification errors must not expose credentials
                    logger.warning("F-Cloudpan 签到结果已保存，通知投递失败")
            return {
                "success": status in ("签到成功", "今日已签到", "资料已刷新"),
                "status": status,
            }

    def get_state(self):
        return bool(self._config.get("enabled"))

    @staticmethod
    def get_command():
        return []

    def get_service(self):
        return []

    def get_api(self):
        return [
            {
                "path": "/oauth/start",
                "endpoint": self.oauth_start,
                "methods": ["GET"],
                "allow_anonymous": True,
                "summary": "使用一次性入口开始云盘授权",
            },
            {
                "path": "/oauth/callback",
                "endpoint": self.oauth_callback,
                "methods": ["GET"],
                "allow_anonymous": True,
                "summary": "云盘授权回调（校验 state、issuer、浏览器绑定与 PKCE）",
            },
        ]

    def get_form(self):
        with self._lock:
            return build_form(
                self._config or self.DEFAULTS,
                self._redirect_uri() if not self._config_error and self._config else "",
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
                self._read(), self._config, self._config_error, next_run, self._api_path
            )

    def stop_service(self):
        with self._lock:
            self._generation = ""
            if self._scheduler:
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
