"""
NodeSeek 签到插件
版本: 1.2.4
作者: liuyunfz
功能:
- 自动完成 NodeSeek 每日签到
- 支持随机/固定签到模式
- 支持签到失败重试
- 保存签到历史记录
- 提供详细的签到通知
- 集成 MoviePilot PlaywrightHelper 绕过 Cloudflare
- 支持 FlareSolverr 模式
"""
import requests
import re
import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit, unquote

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.helper.cloudflare import under_challenge
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType


class NodeSeekVerificationError(RuntimeError):
    """登录表单的站点验证尚未就绪，尚未提交账号密码。"""


class NodeSeekSign(_PluginBase):
    # 插件名称
    plugin_name = "NodeSeek签到"
    # 插件描述
    plugin_desc = "自动完成 NodeSeek 论坛每日签到，支持随机签到、失败重试，集成CF绕过"
    # 插件图标
    plugin_icon = "https://www.nodeseek.com/favicon.ico"
    # 插件版本
    plugin_version = "1.2.4"
    # 插件作者
    plugin_author = "liuyunfz"
    # 作者主页
    author_url = "https://github.com/liuyunfz"
    # 插件配置项ID前缀
    plugin_config_prefix = "nodeseeksign_"
    # 加载顺序
    plugin_order = 2
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    _cookie = None
    _notify = False
    _random_sign = True
    _onlyonce = False
    _cron = None
    _max_retries = 3
    _retry_interval = 30
    _history_days = 30
    # CF 绕过模式: direct / playwright / flaresolverr
    _cf_mode = "direct"
    # 用户名密码登录
    _username = ""
    _password = ""

    @staticmethod
    def _to_bool(value, default=False):
        """兼容 MoviePilot 前端/旧配置可能传入的 bool、字符串、数字。"""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是", "启用", "开启")
        return default

    @staticmethod
    def _to_int(value, default):
        try:
            if value in (None, ""):
                return default
            return int(value)
        except (ValueError, TypeError):
            return default

    def _cfg(self, config: dict, key: str, default=None, *aliases):
        """兼容未加前缀、nodeseeksign_ 前缀，以及旧配置别名。"""
        if not config:
            return default
        keys = (key, *aliases)
        for item in keys:
            if item in config:
                return config.get(item, default)
            prefixed_key = f"{self.plugin_config_prefix}{item}"
            if prefixed_key in config:
                return config.get(prefixed_key, default)
        return default

    def _save_current_config(self, **overrides):
        """集中保存完整配置，避免 onlyonce 等场景把已有配置覆盖丢失。"""
        data = {
            "onlyonce": self._onlyonce,
            "enabled": self._enabled,
            "cookie": self._cookie or "",
            "notify": self._notify,
            "random_sign": self._random_sign,
            # 兼容旧版本 random_choice 配置字段，避免从旧插件迁移后状态丢失
            "random_choice": self._random_sign,
            "cron": self._cron or "30 9 * * *",
            "max_retries": self._max_retries,
            "retry_interval": self._retry_interval,
            "history_days": self._history_days,
            "cf_mode": self._cf_mode,
            "username": self._username or "",
            "password": self._password or "",
        }
        data.update(overrides)
        self.update_config(data)

    # 站点配置
    _base_url = "https://www.nodeseek.com"
    _sign_api = f"{_base_url}/api/attendance"

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        self.stop_service()

        if config:
            logger.info(f"NodeSeek签到 - 收到配置字段: {sorted(list(config.keys()))}")
            self._enabled = self._to_bool(self._cfg(config, "enabled", self._enabled), False)
            self._cookie = (self._cfg(config, "cookie", self._cookie) or "").strip()
            self._notify = self._to_bool(self._cfg(config, "notify", self._notify), False)
            self._random_sign = self._to_bool(
                self._cfg(config, "random_sign", self._random_sign, "random_choice"), True
            )
            self._cron = self._cfg(config, "cron", self._cron) or "30 9 * * *"
            self._onlyonce = self._to_bool(self._cfg(config, "onlyonce", self._onlyonce), False)
            self._max_retries = self._to_int(self._cfg(config, "max_retries", self._max_retries), 3)
            self._retry_interval = self._to_int(self._cfg(config, "retry_interval", self._retry_interval), 30)
            self._history_days = self._to_int(self._cfg(config, "history_days", self._history_days), 30)
            self._cf_mode = self._cfg(config, "cf_mode", self._cf_mode) or "direct"
            self._username = (self._cfg(config, "username", self._username) or "").strip()
            self._password = self._cfg(config, "password", self._password) or ""
            if self._max_retries < 0:
                self._max_retries = 3
            if self._retry_interval < 1:
                self._retry_interval = 30
            if self._history_days < 1:
                self._history_days = 30
            logger.info(
                f"NodeSeek签到 - 配置: enabled={self._enabled}, notify={self._notify}, "
                f"cron={self._cron}, random_sign={self._random_sign}, cf_mode={self._cf_mode}, "
                f"history_days={self._history_days}, max_retries={self._max_retries}, "
                f"retry_interval={self._retry_interval}, username={'已设置' if self._username else '未设置'}, "
                f"password={'已设置' if self._password else '未设置'}, cookie={'已设置' if self._cookie else '未设置'}"
            )

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            logger.info("NodeSeek签到 - 执行一次性签到")
            self._scheduler.add_job(
                func=self.sign,
                trigger='date',
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="NodeSeek签到"
            )
            self._onlyonce = False
            self._save_current_config(onlyonce=False)
        if self._enabled and self._cron:
            logger.info(f"NodeSeek签到 - 周期任务启动: {self._cron}")
            self._scheduler.add_job(
                func=self.sign,
                trigger=CronTrigger.from_crontab(self._cron, timezone=settings.TZ),
                name="NodeSeek签到"
            )

        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def _build_headers(self) -> dict:
        """
        构建请求头
        """
        return {
            "User-Agent": settings.USER_AGENT or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cookie": self._cookie,
            "Referer": f"{self._base_url}/board",
            "Origin": self._base_url,
        }

    @staticmethod
    def _decode_response(status: int, body: str) -> Tuple[Optional[dict], Optional[str]]:
        """保留非 2xx 的业务响应，区别站点风控与 Cloudflare HTML。"""
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            if str(data.get("message", "")).lower() == "high risk action":
                return None, "browser_required"
            if isinstance(data.get("success"), bool):
                return data, None
        if under_challenge(body or "") or "Just a moment..." in (body or ""):
            return None, "cf_challenge"
        return None, f"HTTP {status}：签到接口未返回有效 JSON 业务结果"

    def _sign_direct(self, sign_url: str) -> Tuple[Optional[dict], Optional[str]]:
        try:
            response = requests.post(
                url=sign_url, headers=self._build_headers(),
                proxies=settings.PROXY, timeout=30
            )
            return self._decode_response(response.status_code, response.text)
        except Exception as e:
            return None, f"直连请求失败: {type(e).__name__}"

    # 让站点自己的 Service Worker 接管请求，不能只搬运 cf_clearance 后用 requests POST。
    # 同一段脚本也用于本地已登录浏览器的实站验证，不包含 Cookie 或账号信息。
    _browser_sign_script = """async (signUrl) => {
        if (location.origin !== "https://www.nodeseek.com") {
            throw new Error("未进入 NodeSeek 页面");
        }
        const deadline = Date.now() + 30000;
        while (!navigator.serviceWorker?.controller) {
            if (Date.now() >= deadline) throw new Error("站点 Service Worker 未就绪");
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        const response = await fetch(signUrl, {
            method: "POST",
            credentials: "include",
            signal: AbortSignal.timeout(30000)
        });
        return {status: response.status, body: await response.text()};
    }"""

    def _browser_cookie_list(self) -> list:
        cookies = {}
        for part in (self._cookie or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name:
                cookies[name] = value
        return [{"name": name, "value": value, "url": self._base_url}
                for name, value in cookies.items()]

    def _sign_in_page(self, page, sign_url: str):
        # Cookie 必须在 context 的 cookie jar 内，Service Worker 发起的请求才会携带会话。
        cookies = self._browser_cookie_list()
        # 首页可能刚生成了新的 CF 通行 Cookie，不要用配置里的旧值覆盖它。
        fresh_names = {c["name"] for c in page.context.cookies()
                       if c.get("name") == "cf_clearance"
                       and c.get("domain", "").lstrip(".") in ("nodeseek.com", "www.nodeseek.com")}
        page.context.add_cookies([c for c in cookies if c["name"] not in fresh_names])
        had_cookie = bool(self._cookie)
        if not had_cookie:
            self._login_in_page(page)
        else:
            page.goto(f"{self._base_url}/board", wait_until="domcontentloaded", timeout=60000)
        if had_cookie and self._username and self._password and page.query_selector('a[href="/signIn.html"]'):
            logger.info("NodeSeek 登录会话已失效，在浏览器中重新登录")
            self._login_in_page(page)
        result = page.evaluate(self._browser_sign_script, sign_url)
        return self._decode_response(result["status"], result["body"])

    @staticmethod
    def _browser_proxy():
        """MoviePilot 的 requests 代理字典需转换为浏览器的 server 格式。"""
        proxy = settings.PROXY
        if not proxy:
            return None
        if isinstance(proxy, dict) and proxy.get("server"):
            return dict(proxy)
        address = (proxy.get("https") or proxy.get("http")) if isinstance(proxy, dict) else proxy
        if not address:
            return None
        parsed = urlsplit(address if "://" in address else f"http://{address}")
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        result = {"server": f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "")}
        if parsed.username is not None:
            result["username"] = unquote(parsed.username)
        if parsed.password is not None:
            result["password"] = unquote(parsed.password)
        return result

    def _run_browser(self, callback):
        try:
            from cloakbrowser import launch_context
        except ModuleNotFoundError as e:
            if e.name != "cloakbrowser":
                raise
            # 兼容尚未迁移 CloakBrowser 的旧版 MoviePilot。
            from app.helper.browser import PlaywrightHelper
            return PlaywrightHelper().action(
                url=self._base_url, callback=callback,
                proxies=self._browser_proxy(), headless=True, timeout=60
            )
        # 复用主程序已安装的运行时，避免全局 FlareSolverr 选项强制调用可选服务。
        configured_proxy = self._browser_proxy()
        routes = [configured_proxy, None] if configured_proxy else [None]
        for proxy in routes:
            context = launch_context(
                headless=True, proxy=proxy,
                humanize=getattr(settings, "CLOAKBROWSER_HUMANIZE", False),
                human_preset=getattr(settings, "CLOAKBROWSER_HUMAN_PRESET", "default"),
            )
            try:
                page = context.new_page()
                def request_failed(request):
                    host = urlsplit(request.url).hostname
                    if host == "challenges.cloudflare.com":
                        logger.info(f"NodeSeek 验证资源请求失败: {host}, {request.failure}")
                page.on("requestfailed", request_failed)
                page.goto(self._base_url, wait_until="domcontentloaded", timeout=60000)
                result = callback(page)
                if result and result[1] == "login_verification" and proxy:
                    logger.info("NodeSeek 登录验证未加载，尝试不使用代理的浏览器连接（尚未提交账号密码）")
                    continue
                return result
            finally:
                context.close()


    def _sign_with_playwright(self, sign_url: str) -> Tuple[Optional[dict], Optional[str]]:
        try:
            from importlib.metadata import version, PackageNotFoundError
            components = []
            for name in ("playwright", "cf_clearance", "cloakbrowser"):
                try:
                    installed = version(name.replace("_", "-"))
                except PackageNotFoundError:
                    installed = "未安装"
                components.append(f"{name}={installed}")
            logger.info("NodeSeek 浏览器环境: " + ", ".join(components)
                        + f", browser_emulation={getattr(settings, 'BROWSER_EMULATION', '默认')}")

            def _do_sign(page):
                try:
                    return self._sign_in_page(page, sign_url)
                except NodeSeekVerificationError:
                    logger.info("NodeSeek 登录页验证未完成，账号密码尚未提交")
                    return None, "login_verification"
                except Exception as e:
                    logger.warning(f"NodeSeek 浏览器签到失败: {type(e).__name__}, "
                                   f"页面={page.url.split('?')[0]}, title={page.title()}")
                    return None, "浏览器签到失败，请检查登录会话、站点验证及浏览器运行日志"

            # 不把认证信息放进全局 HTTP Header，避免第三方资源收到 Cookie；
            # 由回调向 NodeSeek 域名注入，再访问签到页。
            result = self._run_browser(_do_sign)
            return result or (None, "MoviePilot 浏览器未能打开 NodeSeek，请检查浏览器组件及网络")
        except ImportError as e:
            return None, f"MoviePilot 浏览器依赖不可用: {getattr(e, 'name', None) or type(e).__name__}"
        except Exception as e:
            return None, f"浏览器异常: {type(e).__name__}"

    def _sign_with_flaresolverr(self, sign_url: str) -> Tuple[Optional[dict], Optional[str]]:
        """
        通过 FlareSolverr 绕过 CF 后签到
        """
        if not settings.FLARESOLVERR_URL:
            return None, "未配置 FLARESOLVERR_URL 环境变量"

        try:
            from app.helper.browser import PlaywrightHelper
            helper = PlaywrightHelper()
            # 先通过 FlareSolverr 获取清除后的 cookie
            solution = helper._PlaywrightHelper__flaresolverr_request(
                url=self._base_url,
                cookies=self._cookie,
                proxy_config=self._browser_proxy(),
                timeout=60
            )
            if not solution:
                return None, "FlareSolverr 未能获取有效会话"

            # 合并 cookie
            fs_cookies = solution.get("cookies", [])
            merged = {c["name"]: c["value"] for c in self._browser_cookie_list()}
            merged.update({c["name"]: c.get("value", "") for c in fs_cookies if c.get("name")})
            merged_cookie = "; ".join(f"{name}={value}" for name, value in merged.items())

            fs_ua = solution.get("userAgent") or settings.USER_AGENT

            headers = {
                "User-Agent": fs_ua,
                "Accept": "application/json, text/plain, */*",
                "Cookie": merged_cookie,
                "Referer": f"{self._base_url}/board",
                "Origin": self._base_url,
            }

            response = requests.post(url=sign_url, headers=headers, proxies=settings.PROXY, timeout=30)
            return self._decode_response(response.status_code, response.text)

        except Exception as e:
            return None, f"FlareSolverr 异常: {str(e)}"

    def _solver_turnstile_token(self) -> str:
        """用独立 FlareSolverr 会话取得令牌，不移植浏览器指纹或 CF Cookie。"""
        import uuid
        from html.parser import HTMLParser

        class TokenParser(HTMLParser):
            token = ""

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "input" and values.get("name") == "cf-turnstile-response":
                    self.token = values.get("value", "")

        base = (getattr(settings, "FLARESOLVERR_URL", "") or "").rstrip("/")
        if not base:
            raise NodeSeekVerificationError("未配置 FlareSolverr")
        api = base + "/v1"
        sid = "nodeseek-" + uuid.uuid4().hex
        create = {"cmd": "sessions.create", "session": sid}
        proxy = self._browser_proxy()
        if proxy:
            create["proxy"] = {"url": proxy["server"]}
            for key in ("username", "password"):
                if proxy.get(key):
                    create["proxy"][key] = proxy[key]
        with requests.Session() as session:
            # 服务连接不走站点代理；代理由 FlareSolverr 浏览器使用。
            session.trust_env = False
            def call(payload, timeout=75):
                response = session.post(api, json=payload, timeout=timeout)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or result.get("status") != "ok":
                    raise NodeSeekVerificationError("FlareSolverr 请求未成功")
                return result
            try:
                call(create, 30)
                request = {"cmd": "request.get", "url": self._base_url + "/signIn.html",
                           "session": sid, "maxTimeout": 60000}
                call({**request, "waitInSeconds": 5})
                # NodeSeek 当前桌面登录页：24 个可聚焦控件后为验证框。
                solution = call({**request, "tabs_till_verify": 25}).get("solution") or {}
                parser = TokenParser()
                parser.feed(solution.get("response") or "")
                token = solution.get("turnstile_token") or parser.token
                if not isinstance(token, str) or not token:
                    raise NodeSeekVerificationError("FlareSolverr 未返回验证令牌")
                return token
            finally:
                try:
                    session.post(api, json={"cmd": "sessions.destroy", "session": sid}, timeout=15)
                except Exception:
                    logger.warning("NodeSeek FlareSolverr 临时会话清理失败")

    _solver_login_script = """async ({username, password, token}) => {
        if (location.origin !== 'https://www.nodeseek.com') throw new Error('登录页面域名不匹配');
        const moduleUrl = name => {
            const element = Array.from(document.querySelectorAll('link[rel="modulepreload"]'))
                .find(e => new URL(e.href).origin === location.origin &&
                    new URL(e.href).pathname.startsWith('/assets/' + name + '-'));
            if (!element) throw new Error('站点登录模块未找到');
            return element.href;
        };
        const pre = await import(moduleUrl('preLogin'));
        const post = await import(moduleUrl('postLogin'));
        const response = await fetch('/api/account/signIn', {
            method: 'POST', credentials: 'include', signal: AbortSignal.timeout(30000),
            headers: {'content-type': 'application/json', ...await pre.g(),
                      'x-captcha-token': token, 'x-captcha-source': 'turnstile'},
            body: JSON.stringify({username, password})
        });
        await post.p(response);
        let data = {};
        try { data = await response.json(); } catch {}
        return {status: response.status, success: data.success === true, need2FA: !!data.need2FA};
    }"""

    def _login_in_page(self, page):
        """复用 MoviePilot 浏览器，登录后在同一个上下文内签到。"""
        if not self._username or not self._password:
            raise RuntimeError("未配置登录账号")
        solver = bool(getattr(settings, "FLARESOLVERR_URL", ""))
        token = None
        if solver:
            logger.info("NodeSeek 自动登录: 使用 FlareSolverr 完成登录页验证")
            token = self._solver_turnstile_token()
        page.goto(f"{self._base_url}/signIn.html", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#stacked-password", state="visible", timeout=30000)
        if token:
            # 保持 CloakBrowser 原生 UA / 指纹，等待站点初始化后提交新令牌。
            page.wait_for_timeout(10000)
            result = page.evaluate(self._solver_login_script, {
                "username": self._username, "password": self._password, "token": token,
            })
            if result.get("need2FA"):
                raise RuntimeError("账号要求二次验证，无法仅使用账号密码登录")
            if not result.get("success"):
                raise RuntimeError(f"账号登录未成功（HTTP {result.get('status')}）")
            page.goto(self._base_url + "/", wait_until="domcontentloaded", timeout=60000)
        else:
            page.fill("#stacked-email", self._username)
            page.fill("#stacked-password", self._password)
            try:
                page.wait_for_function("""() => {
                    const field = document.querySelector('[name="cf-turnstile-response"]');
                    return !field || !!field.value;
                }""", timeout=45000)
            except Exception as e:
                raise NodeSeekVerificationError("登录页验证未完成") from e
            page.click("form:has(#stacked-password) button[type='submit']")
        # 退出入口可能只有图标，不能仅依赖“登出”文字。
        page.wait_for_function("""() => !!document.querySelector('a[href="/api/account/signOut"]') ||
            Array.from(document.querySelectorAll('a')).some(a => a.textContent.trim() === '登出')""",
            timeout=30000)
        if token:
            page.wait_for_timeout(10000)
        # 不能把仅含 cf_clearance 的未登录上下文误记为登录成功。
        cookies = [c for c in page.context.cookies()
                   if c.get("domain", "").lstrip(".") in ("nodeseek.com", "www.nodeseek.com")]
        if not cookies:
            raise RuntimeError("登录后没有站点会话")
        self._cookie = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        self._save_current_config()
        logger.info("NodeSeek 自动登录成功：已验证登录状态并更新 Cookie，继续在当前浏览器签到")

    def sign(self, retry_count=0):
        """
        执行签到
        """
        logger.info("开始 NodeSeek 签到")

        random_param = "true" if self._random_sign else "false"
        sign_url = f"{self._sign_api}?random={random_param}"
        if not self._cookie and not (self._username and self._password):
            self._save_history("签到失败", "未配置 Cookie 或用户名密码")
            self._send_notification("NodeSeek签到失败", "❗ 请填写 Cookie 或用户名密码")
            return

        logger.info(f"NodeSeek 签到 - 模式: {self._cf_mode}")
        if not self._cookie or self._cf_mode == "playwright":
            data, error = self._sign_with_playwright(sign_url)
        elif self._cf_mode == "flaresolverr":
            data, error = self._sign_with_flaresolverr(sign_url)
            if error in ("cf_challenge", "browser_required"):
                data, error = self._sign_with_playwright(sign_url)
        else:
            data, error = self._sign_direct(sign_url)
            if error in ("cf_challenge", "browser_required"):
                logger.info("NodeSeek 需要浏览器会话，切换浏览器签到")
                data, error = self._sign_with_playwright(sign_url)

        if error == "login_verification":
            error = "登录页 Cloudflare 验证未完成，请检查 FlareSolverr 版本、连接和站点代理"
        elif error == "browser_required":
            error = "站点拒绝签到（high risk action），请更新 Cookie 并确认浏览器验证已完成"
        elif error == "cf_challenge":
            error = "Cloudflare 验证未通过，请检查 MoviePilot 浏览器环境"

        # 处理结果
        if error:
            logger.error(f"NodeSeek 签到失败: {error}")
            self._handle_retry(retry_count, error)
            return

        if not data:
            logger.error("NodeSeek 签到失败: 无响应数据")
            self._handle_retry(retry_count, "无响应数据")
            return

        success = data.get("success", False)
        message = str(data.get("message") or "未知状态")

        if success:
            logger.info(f"NodeSeek 签到成功: {message}")
            self._save_history("签到成功", message)
            self._send_notification("NodeSeek签到成功", f"✅ {message}")
        elif any(text in message for text in ("已经签到", "已签到", "重复签到", "今天已完成签到")):
            logger.info(f"NodeSeek 已签到: {message}")
            self._save_history("已签到", message)
            self._send_notification("NodeSeek已签到", f"ℹ️ {message}")
        else:
            logger.error(f"NodeSeek 签到失败: {message}")
            self._handle_retry(retry_count, message)

    def _handle_retry(self, retry_count: int, error_msg: str):
        """
        处理重试逻辑
        """
        if retry_count < self._max_retries:
            logger.info(f"NodeSeek 签到将在 {self._retry_interval} 秒后重试 (第{retry_count + 1}次)")
            self._scheduler.add_job(
                func=self.sign,
                trigger='date',
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=self._retry_interval),
                kwargs={"retry_count": retry_count + 1},
                name="NodeSeek签到重试"
            )
        else:
            logger.error(f"NodeSeek 签到重试次数已用完: {error_msg}")
            self._save_history("签到失败", error_msg)
            self._send_notification("NodeSeek签到失败", f"❗ 重试{self._max_retries}次后仍失败: {error_msg}")

    def _save_history(self, status: str, message: str):
        """
        保存签到历史
        """
        history = self.get_data("history") or []
        history.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "message": message,
        })
        cutoff = (datetime.now() - timedelta(days=self._history_days)).strftime("%Y-%m-%d %H:%M:%S")
        history = [h for h in history if h.get("time", "") >= cutoff]
        self.save_data("history", history)

    def _send_notification(self, title: str, text: str):
        """
        发送通知
        """
        if self._notify:
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title=title,
                text=text
            )

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        # 周期任务由插件自己的 scheduler 管理，避免被 MP 重复调度。
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        返回配置表单
        """
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VCard",
                        "props": {"variant": "outlined", "class": "mt-3"},
                        "content": [
                            {
                                "component": "VCardTitle",
                                "text": "NodeSeek签到配置"
                            },
                            {"component": "VDivider"},
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
                                                    "component": "VSwitch",
                                                    "props": {
                                                        "model": "notify",
                                                        "label": "发送通知"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VSwitch",
                                                    "props": {
                                                        "model": "random_sign",
                                                        "label": "随机签到"
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
                                                        "model": "cookie",
                                                        "label": "Cookie",
                                                        "placeholder": "从浏览器开发者工具复制完整的Cookie（可选，留空则通过用户名密码自动登录）",
                                                        "hint": "登录 NodeSeek 后，从浏览器 F12 -> Network 中复制请求的 Cookie"
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
                                                        "model": "username",
                                                        "label": "用户名/邮箱（可选，用于自动登录）",
                                                        "placeholder": "NodeSeek 登录用户名或邮箱"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 6},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "password",
                                                        "label": "密码（可选，用于自动登录）",
                                                        "type": "password",
                                                        "placeholder": "NodeSeek 登录密码"
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
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VSelect",
                                                    "props": {
                                                        "model": "cf_mode",
                                                        "label": "CF绕过模式",
                                                        "items": [
                                                            {"title": "自动（先直连，失败自动切换）", "value": "direct"},
                                                            {"title": "浏览器会话（MoviePilot 浏览器组件）", "value": "playwright"},
                                                            {"title": "FlareSolverr（需配置服务地址）", "value": "flaresolverr"}
                                                        ],
                                                        "hint": "自动模式遇到站点风控或CF挑战会切换浏览器；推荐浏览器会话模式"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "cron",
                                                        "label": "签到周期",
                                                        "placeholder": "30 9 * * *"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "max_retries",
                                                        "label": "最大重试次数",
                                                        "type": "number"
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
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "retry_interval",
                                                        "label": "重试间隔(秒)",
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
                                                        "model": "onlyonce",
                                                        "label": "立即运行一次"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [{
                                                    "component": "VTextField",
                                                    "props": {
                                                        "model": "history_days",
                                                        "label": "历史保留天数",
                                                        "type": "number"
                                                    }
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
            "cookie": "",
            "notify": False,
            "random_sign": True,
            "random_choice": True,
            "cf_mode": "direct",
            "cron": "30 9 * * *",
            "onlyonce": False,
            "max_retries": 3,
            "retry_interval": 30,
            "history_days": 30,
            "username": "",
            "password": "",
        }

    def get_page(self) -> List[dict]:
        """
        返回插件详情页
        """
        history = self.get_data("history") or []
        rows = []
        for item in reversed(history[-20:]):
            status = item.get("status", "")
            rows.append({
                "component": "VListItem",
                "content": [
                    {
                        "component": "VListItemTitle",
                        "text": f"{item.get('time', '-')} | {status}"
                    },
                    {
                        "component": "VListItemSubtitle",
                        "text": item.get("message", "")
                    }
                ]
            })

        return [{
            "component": "VCard",
            "props": {"variant": "outlined", "class": "mt-3"},
            "content": [
                {
                    "component": "VCardTitle",
                    "text": "签到历史"
                },
                {"component": "VDivider"},
                {
                    "component": "VCardText",
                    "content": [{
                        "component": "VList",
                        "props": {"density": "compact"},
                        "content": rows or [{
                            "component": "VListItem",
                            "content": [{
                                "component": "VListItemTitle",
                                "text": "暂无签到记录"
                            }]
                        }]
                    }]
                }
            ]
        }]

    def stop_service(self):
        """
        停止服务
        """
        if self._scheduler:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            self._scheduler = None
