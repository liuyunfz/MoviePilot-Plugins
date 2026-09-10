"""
NodeSeek 签到插件
版本: 1.1.0
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
import time
import requests
import re
import json
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.helper.cloudflare import under_challenge
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType


class NodeSeekSign(_PluginBase):
    # 插件名称
    plugin_name = "NodeSeek签到"
    # 插件描述
    plugin_desc = "自动完成 NodeSeek 论坛每日签到，支持随机签到、失败重试，集成CF绕过"
    # 插件图标
    plugin_icon = "https://www.nodeseek.com/favicon.ico"
    # 插件版本
    plugin_version = "1.2.1"
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
            self._password = (self._cfg(config, "password", self._password) or "").strip()
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
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cookie": self._cookie,
            "Referer": f"{self._base_url}/board",
            "Origin": self._base_url,
        }

    def _sign_direct(self, sign_url: str) -> Tuple[Optional[dict], Optional[str]]:
        """
        直接请求签到（不绕 CF）
        """
        try:
            headers = self._build_headers()
            response = requests.post(url=sign_url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data, None
            # 检测 CF 挑战
            if response.status_code == 403 or under_challenge(response.text):
                return None, "cf_challenge"
            return None, f"HTTP {response.status_code}"
        except Exception as e:
            return None, str(e)

    def _sign_with_playwright(self, sign_url: str) -> Tuple[Optional[dict], Optional[str]]:
        """
        通过 Playwright 绕过 CF 后签到
        """
        try:
            from app.helper.browser import PlaywrightHelper
            helper = PlaywrightHelper()

            result_data = [None]
            error_msg = [None]

            def _do_sign(page):
                """在 Playwright 页面中执行签到"""
                # 先访问首页获取有效 cookie/session
                page.goto(self._base_url, wait_until="networkidle", timeout=30000)
                time.sleep(2)

                # 在页面上下文中执行 fetch 签到
                random_param = "true" if self._random_sign else "false"
                api_url = f"{self._sign_api}?random={random_param}"

                js_code = f"""
                async () => {{
                    const resp = await fetch("{api_url}", {{
                        method: "POST",
                        headers: {{
                            "Accept": "application/json",
                            "Referer": "{self._base_url}/board"
                        }},
                        credentials: "include"
                    }});
                    return await resp.json();
                }}
                """
                try:
                    data = page.evaluate(js_code)
                    result_data[0] = data
                except Exception as e:
                    error_msg[0] = f"JS执行失败: {str(e)}"

            helper.action(
                url=self._base_url,
                callback=_do_sign,
                cookies=self._cookie,
                proxies=settings.PROXY,
                headless=True,
                timeout=60
            )

            if result_data[0]:
                return result_data[0], None
            return None, error_msg[0] or "Playwright签到失败"

        except ImportError:
            return None, "Playwright未安装，请先安装: pip install playwright && playwright install chromium"
        except Exception as e:
            return None, f"Playwright异常: {str(e)}"

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
                proxy_config=settings.PROXY,
                timeout=60
            )
            if not solution:
                return None, "FlareSolverr 未能获取有效会话"

            # 合并 cookie
            fs_cookies = solution.get("cookies", [])
            fs_cookie_str = "; ".join([f"{c.get('name')}={c.get('value')}" for c in fs_cookies if c.get('name')])
            merged_cookie = f"{self._cookie}; {fs_cookie_str}" if self._cookie else fs_cookie_str

            fs_ua = solution.get("userAgent") or settings.USER_AGENT

            headers = {
                "User-Agent": fs_ua,
                "Accept": "application/json, text/plain, */*",
                "Cookie": merged_cookie,
                "Referer": f"{self._base_url}/board",
                "Origin": self._base_url,
            }

            response = requests.post(url=sign_url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json(), None
            return None, f"FlareSolverr 签到失败: HTTP {response.status_code}"

        except Exception as e:
            return None, f"FlareSolverr 异常: {str(e)}"

    def _auto_login(self) -> Optional[str]:
        """
        通过 Playwright 浏览器自动化登录 NodeSeek 获取 Cookie
        NS 无公开登录 API，只能通过浏览器表单提交
        """
        if not self._username or not self._password:
            logger.warning("未配置用户名或密码，无法自动登录")
            return None

        try:
            from playwright.sync_api import sync_playwright
            from cf_clearance import sync_cf_retry, sync_stealth
            logger.info("NodeSeek 自动登录: 启动 Playwright")

            proxy = None
            try:
                pxy = settings.PROXY or {}
                server = pxy.get('http') or pxy.get('https')
                if server:
                    proxy = {"server": server}
            except Exception:
                proxy = None

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, proxy=proxy) if proxy else pw.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()

                # 过 CF
                sync_stealth(page, pure=True)
                login_url = f"{self._base_url}/login"
                page.goto(login_url, wait_until="domcontentloaded", timeout=30000)

                try:
                    cf_ok, _ = sync_cf_retry(page)
                    if cf_ok:
                        logger.info("NodeSeek 自动登录: CF 验证通过")
                except Exception:
                    pass

                time.sleep(2)

                # CF 通过后必须确认真正进入登录表单；sync_cf_retry 有时会误报成功但仍停在
                # Cloudflare/Turnstile 的 "Just a moment..." 页，此时页面里只有隐藏的
                # cf-turnstile-response，继续填表必然 username/password 都失败。
                login_form_ready = False
                last_inputs = []
                for attempt in range(1, 5):
                    try:
                        title = page.title()
                        url = page.url
                        last_inputs = page.evaluate("""
                        () => Array.from(document.querySelectorAll('input')).map((i, idx) => ({
                            idx,
                            type: i.type || '',
                            name: i.name || '',
                            placeholder: i.placeholder || '',
                            autocomplete: i.autocomplete || '',
                            visible: !!(i.offsetWidth || i.offsetHeight || i.getClientRects().length)
                        }))
                        """)
                        has_password = page.locator("input[type='password']").count() > 0
                        logger.info(f"NodeSeek 自动登录: 登录页检查 {attempt}/4 title={title}, url={url}, has_password={has_password}")
                        if has_password:
                            login_form_ready = True
                            break

                        # 如果仍是 CF 页，不要直接提交；等待/重试，让浏览器完成挑战跳转。
                        input_names = [i.get("name") for i in (last_inputs or [])]
                        still_cf = (
                            "just a moment" in (title or "").lower()
                            or "cf-turnstile-response" in input_names
                            or not last_inputs
                        )
                        if still_cf:
                            logger.warning(f"NodeSeek 自动登录: 仍在 CF 验证页或登录表单未出现，输入框: {last_inputs}")
                            try:
                                cf_ok, _ = sync_cf_retry(page)
                                if cf_ok:
                                    logger.info("NodeSeek 自动登录: CF 重试返回通过")
                            except Exception as e:
                                logger.debug(f"NodeSeek 自动登录: CF 重试异常: {e}")
                            page.wait_for_timeout(5000)
                            if "/login" not in page.url:
                                page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
                            else:
                                page.reload(wait_until="domcontentloaded", timeout=30000)
                            continue

                        # 不是 CF 页但也没密码框，尝试重新打开登录页。
                        if "/login" not in url:
                            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
                        else:
                            page.wait_for_timeout(3000)
                    except Exception as e:
                        logger.debug(f"NodeSeek 自动登录: 登录页检查失败: {e}")
                        try:
                            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
                        except Exception:
                            pass

                if not login_form_ready:
                    logger.error(f"NodeSeek 自动登录失败: CF 未真正放行或登录表单未加载，最后输入框: {last_inputs}")
                    try:
                        browser.close()
                    except Exception:
                        pass
                    return None

                # 等待登录表单出现。NodeSeek 前端的 username 输入框可能只是 type=text，未必带 name/placeholder。
                try:
                    page.wait_for_selector("input[type='password']", timeout=15000)
                except Exception:
                    try:
                        input_info = page.evaluate("""
                        () => Array.from(document.querySelectorAll('input')).map((i, idx) => ({
                            idx,
                            type: i.type || '',
                            name: i.name || '',
                            placeholder: i.placeholder || '',
                            autocomplete: i.autocomplete || '',
                            visible: !!(i.offsetWidth || i.offsetHeight || i.getClientRects().length)
                        }))
                        """)
                        logger.warning(f"NodeSeek 自动登录: 未发现密码输入框，当前输入框: {input_info}")
                    except Exception:
                        pass

                username_filled = False
                password_filled = False

                # 填写用户名/邮箱：先精确选择，再兜底选择第一个可见的非 password/hidden 输入框
                username_selectors = [
                    "input[name='username']", "input[name='email']", "input[name='account']",
                    "input[name='login']", "input[type='email']", "input[autocomplete='username']",
                    "input[placeholder*='邮箱']", "input[placeholder*='email']", "input[placeholder*='Email']",
                    "input[placeholder*='用户']", "input[placeholder*='账号']", "input[type='text']",
                    "input:not([type])"
                ]
                for sel in username_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            loc.fill(self._username)
                            username_filled = True
                            logger.info(f"NodeSeek 自动登录: 已填写用户名 ({sel})")
                            break
                    except Exception:
                        continue

                if not username_filled:
                    try:
                        filled = page.evaluate("""
                        (username) => {
                            const inputs = Array.from(document.querySelectorAll('input'));
                            const target = inputs.find(i => {
                                const type = (i.type || '').toLowerCase();
                                const visible = !!(i.offsetWidth || i.offsetHeight || i.getClientRects().length);
                                return visible && !['password','hidden','checkbox','radio','submit','button'].includes(type) && !i.disabled && !i.readOnly;
                            });
                            if (!target) return false;
                            target.focus();
                            target.value = username;
                            target.dispatchEvent(new Event('input', {bubbles: true}));
                            target.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        }
                        """, self._username)
                        if filled:
                            username_filled = True
                            logger.info("NodeSeek 自动登录: 已通过 JS 兜底填写用户名")
                    except Exception as e:
                        logger.debug(f"NodeSeek 自动登录: JS 兜底填写用户名失败: {e}")

                # 填写密码
                password_selectors = [
                    "input[name='password']", "input[type='password']", "input[autocomplete='current-password']",
                    "input[placeholder*='密码']", "input[placeholder*='Password']", "input[placeholder*='password']"
                ]
                for sel in password_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            loc.fill(self._password)
                            password_filled = True
                            logger.info(f"NodeSeek 自动登录: 已填写密码 ({sel})")
                            break
                    except Exception:
                        continue

                if not password_filled:
                    logger.warning("NodeSeek 自动登录: 未能填写密码，登录表单可能未加载或选择器变化")

                if not username_filled or not password_filled:
                    logger.warning(f"NodeSeek 自动登录: 表单填写不完整 username_filled={username_filled}, password_filled={password_filled}")

                # 点击登录；如果按钮选择器变化，最后用 Enter 提交
                clicked = False
                submit_selectors = [
                    "button[type='submit']", "input[type='submit']", "button:has-text('登录')",
                    "button:has-text('Login')", "button:has-text('Sign in')", "button:has-text('Log in')",
                    "button:has-text('提交')", "form button", "[role='button']:has-text('登录')",
                    "[role='button']:has-text('Login')"
                ]
                for sel in submit_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            loc.click()
                            clicked = True
                            logger.info(f"NodeSeek 自动登录: 已点击登录 ({sel})")
                            break
                    except Exception:
                        continue
                if not clicked:
                    try:
                        page.keyboard.press("Enter")
                        clicked = True
                        logger.info("NodeSeek 自动登录: 已通过 Enter 提交登录表单")
                    except Exception as e:
                        logger.warning(f"NodeSeek 自动登录: 提交登录表单失败: {e}")

                # 等待跳转/接口完成
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    time.sleep(5)
                try:
                    page.wait_for_timeout(3000)
                    logger.info(f"NodeSeek 自动登录: 提交后 title={page.title()}, url={page.url}")
                except Exception:
                    pass

                # 某些 SPA 登录成功后停在原页，主动访问 board 促使会话 cookie 写入/校验
                try:
                    if "/login" in page.url:
                        page.goto(f"{self._base_url}/board", wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_timeout(2000)
                except Exception:
                    pass

                # 提取所有 cookies
                all_cookies = context.cookies()
                cookie_parts = []
                cookie_names = []
                for c in all_cookies:
                    domain = c.get('domain', '')
                    name = c.get('name', '')
                    if domain.endswith('nodeseek.com') or domain.endswith('.nodeseek.com'):
                        cookie_names.append(name)
                        cookie_parts.append(f"{name}={c.get('value', '')}")

                browser.close()

                if cookie_parts:
                    cookie_str = "; ".join(cookie_parts)
                    # NodeSeek 的会话 cookie 名称可能随后端框架变化，不强制要求叫 session/token；
                    # 只要登录后有站点 cookie，就交给后续签到接口验证是否有效。
                    logger.info(f"NodeSeek 自动登录成功，获取到 {len(cookie_parts)} 个 cookie: {cookie_names}")
                    return cookie_str
                else:
                    try:
                        logger.warning(f"NodeSeek 自动登录: 未获取到任何 nodeseek.com cookie，浏览器 cookie 名称: {[c.get('name') for c in all_cookies]}")
                    except Exception:
                        logger.warning("NodeSeek 自动登录: 未获取到任何 cookie")

        except ImportError:
            logger.error("Playwright 未安装，无法使用浏览器自动登录")
        except Exception as e:
            logger.error(f"NodeSeek 自动登录异常: {e}")

        return None

    def sign(self, retry_count=0):
        """
        执行签到
        """
        logger.info("开始 NodeSeek 签到")

        if not self._cookie:
            if self._username and self._password:
                logger.info("NodeSeek 签到: 未配置Cookie，尝试自动登录")
                new_cookie = self._auto_login()
                if new_cookie:
                    self._cookie = new_cookie
                    self.update_config({
                        "enabled": self._enabled,
                        "cookie": self._cookie,
                        "notify": self._notify,
                        "random_sign": self._random_sign,
                        "cron": self._cron,
                        "max_retries": self._max_retries,
                        "retry_interval": self._retry_interval,
                        "history_days": self._history_days,
                        "cf_mode": self._cf_mode,
                        "username": self._username,
                        "password": self._password,
                    })
                else:
                    logger.error("NodeSeek 签到失败: 自动登录失败")
                    self._save_history("签到失败", "自动登录失败，请检查用户名和密码")
                    self._send_notification("NodeSeek签到失败", "❗ 自动登录失败，请检查用户名和密码")
                    return
            else:
                logger.error("NodeSeek 签到失败: 未配置Cookie")
                self._save_history("签到失败", "未配置Cookie")
                self._send_notification("NodeSeek签到失败", "❗ 未配置Cookie，请填写Cookie或用户名密码")
                return

        random_param = "true" if self._random_sign else "false"
        sign_url = f"{self._sign_api}?random={random_param}"

        # 按优先级尝试签到方式
        data = None
        error = None

        # 1. 先尝试直接请求
        logger.info(f"NodeSeek 签到 - 模式: {self._cf_mode}")
        data, error = self._sign_direct(sign_url)

        # 2. 如果遇到 CF 挑战，根据配置选择绕过方式
        if error == "cf_challenge":
            logger.info("NodeSeek 签到 - 检测到 Cloudflare 挑战，尝试绕过...")
            self._save_history("CF挑战", "检测到Cloudflare防护，尝试绕过")

            if self._cf_mode == "playwright":
                data, error = self._sign_with_playwright(sign_url)
            elif self._cf_mode == "flaresolverr":
                data, error = self._sign_with_flaresolverr(sign_url)
            else:
                # direct 模式下遇到 CF，自动尝试 Playwright
                logger.info("NodeSeek 签到 - 自动切换到 Playwright 模式")
                data, error = self._sign_with_playwright(sign_url)
                # 如果 Playwright 也失败，尝试 FlareSolverr
                if error and settings.FLARESOLVERR_URL:
                    logger.info("NodeSeek 签到 - Playwright 失败，尝试 FlareSolverr")
                    data, error = self._sign_with_flaresolverr(sign_url)

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
        message = data.get("message", "未知状态")

        if success:
            logger.info(f"NodeSeek 签到成功: {message}")
            self._save_history("签到成功", message)
            self._send_notification("NodeSeek签到成功", f"✅ {message}")
        elif "已经签到" in message or "重复" in message:
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
                                                            {"title": "Playwright（需安装浏览器）", "value": "playwright"},
                                                            {"title": "FlareSolverr（需配置服务地址）", "value": "flaresolverr"}
                                                        ],
                                                        "hint": "自动模式下优先直连，遇到CF挑战自动用Playwright/FlareSolverr"
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
