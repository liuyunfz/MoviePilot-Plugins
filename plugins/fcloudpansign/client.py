"""F-Cloudpan OAuth and check-in protocol. Never include upstream bodies in errors."""

import ipaddress
import math
import re
import socket
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

SCOPES = "account:read account:write"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
OAUTH_ERRORS = {
    "authorization_pending": "等待用户在云盘确认",
    "slow_down": "云盘要求降低授权查询频率",
    "access_denied": "用户已拒绝授权",
    "expired_token": "设备授权码已过期，请重新连接",
    "invalid_grant": "本次授权已失效，请重新连接",
    "invalid_client": "公共应用不存在或不可用，请检查 Client ID",
    "invalid_scope": "应用未开放账户读取和签到所需权限",
    "unauthorized_client": "此应用不支持公共设备授权",
}


class CloudError(Exception):
    def __init__(self, message, status=0, code="", retry_after=0):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after


def origin_url(value):
    """HTTPS in production; plain HTTP only for loopback development."""
    value = str(value or "").strip()
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port
        loopback = host == "localhost"
        if host and not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        if (
            not host
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value
            or (url.scheme != "https" and not (url.scheme == "http" and loopback))
        ):
            raise ValueError()
        netloc = f"[{host}]" if ":" in host else host
        if port and not (
            (url.scheme == "https" and port == 443)
            or (url.scheme == "http" and port == 80)
        ):
            netloc += f":{port}"
        return urlunsplit((url.scheme, netloc, "", "", ""))
    except ValueError:
        raise CloudError(
            "请填写 HTTPS 站点根地址（本机开发可用 http://localhost:端口）"
        ) from None


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def network_error(exc):
    """Classify without rendering exception strings, URLs or proxy credentials."""
    if isinstance(exc, requests.exceptions.ProxyError):
        return "连接代理失败"
    if isinstance(exc, requests.exceptions.SSLError):
        return "TLS 握手或证书校验失败"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "建立连接超时"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "等待云盘响应超时"
    if isinstance(exc, requests.exceptions.Timeout):
        return "云盘请求超时"
    # requests wraps urllib3 errors, which may wrap the socket resolver error.
    seen = set()
    pending = [exc]
    while pending and len(seen) < 32:
        error = pending.pop()
        if not isinstance(error, BaseException) or id(error) in seen:
            continue
        seen.add(id(error))
        if isinstance(error, socket.gaierror):
            return "DNS 域名解析失败"
        pending.extend(error.args)
        pending.extend(
            [error.__cause__, error.__context__, getattr(error, "reason", None)]
        )
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "云盘连接中断或无法建立连接"
    return "云盘网络请求失败"


class CloudClient:
    def __init__(self, issuer, client_id, timeout=20, proxies=None):
        self.issuer = origin_url(issuer)
        self.client_id = client_id
        self.timeout = timeout
        self.proxies = dict(proxies) if proxies else {}

    def request(self, method, path, *, token=None, form=None, payload=None):
        headers = {
            "Accept": "application/json",
            "User-Agent": "MoviePilot-FCloudpanSign/1.1.2",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if form is not None:
            form = {**form, "client_id": self.client_id}
        try:
            # A fresh session avoids cookies, implicit .netrc credentials and connection retries.
            with requests.Session() as session:
                session.trust_env = False
                with session.request(
                    method,
                    self.issuer + path,
                    headers=headers,
                    data=form,
                    json=payload,
                    timeout=(5, self.timeout),
                    allow_redirects=False,
                    proxies=self.proxies,
                ) as response:
                    status = response.status_code
                    if not 200 <= status < 300:
                        # Never expose upstream descriptions, bodies, or unknown error names.
                        code = ""
                        if form is not None:
                            try:
                                error = response.json()
                                candidate = (
                                    error.get("error")
                                    if isinstance(error, dict)
                                    else None
                                )
                                if (
                                    isinstance(candidate, str)
                                    and candidate in OAUTH_ERRORS
                                ):
                                    code = candidate
                            except ValueError:
                                pass
                        retry_after = response.headers.get("Retry-After", "")
                        retry_after = (
                            min(int(retry_after), 600)
                            if re.fullmatch(r"[0-9]{1,8}", retry_after)
                            else 0
                        )
                        messages = {
                            400: "请求或授权已失效，请检查配置或重新授权",
                            401: "应用或用户授权已失效",
                            403: "缺少账户权限，或应用/用户当前不可用",
                            429: "调用频率受限，请稍后执行",
                        }
                        raise CloudError(
                            OAUTH_ERRORS.get(code)
                            or messages.get(status, f"云盘接口异常（HTTP {status}）"),
                            status,
                            code,
                            retry_after,
                        )
                    try:
                        result = response.json()
                    except ValueError:
                        raise CloudError(
                            "云盘未返回有效 JSON，请检查站点地址和反向代理",
                            code="invalid_response",
                        ) from None
        except requests.RequestException as exc:
            route = "MoviePilot 代理" if self.proxies else "直连"
            reason = network_error(exc)
            raise CloudError(
                f"{reason}（{route}）；请检查 NAS / 容器网络及 MP 代理设置，本次请求结果未知"
            ) from None
        if not isinstance(result, dict):
            raise CloudError("云盘响应格式不正确", code="invalid_response")
        return result

    def device_authorization(self):
        result = self.request("POST", "/oauth/device/code", form={"scope": SCOPES})
        interval = result.get("interval", 5)
        if (
            not isinstance(result.get("device_code"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", result["device_code"])
            or not isinstance(result.get("user_code"), str)
            or not re.fullmatch(r"[A-Z0-9-]{4,32}", result["user_code"])
            or not number(result.get("expires_in"))
            or not 30 <= result["expires_in"] <= 1800
            or not number(interval)
            or not 1 <= interval <= 120
        ):
            raise CloudError("设备授权响应不完整，请重新连接")
        for key in ("verification_uri", "verification_uri_complete"):
            link = result.get(key)
            if key == "verification_uri_complete" and link is None:
                continue
            try:
                url = urlsplit(link) if isinstance(link, str) else None
                valid = (
                    url
                    and len(link) <= 2048
                    and not url.username
                    and not url.password
                    and not url.fragment
                    and url.path == "/oauth/device"
                    and (
                        not url.query
                        if key == "verification_uri"
                        else parse_qs(url.query, keep_blank_values=True)
                        == {"user_code": [result["user_code"]]}
                    )
                    and not any(c.isspace() or ord(c) < 32 for c in link)
                    and "\\" not in link
                    and origin_url(urlunsplit((url.scheme, url.netloc, "", "", "")))
                    == self.issuer
                )
            except (ValueError, CloudError):
                valid = False
            if not valid:
                raise CloudError("云盘确认地址无效或与站点不匹配")
        return {
            key: result[key]
            for key in (
                "device_code",
                "user_code",
                "verification_uri",
                "verification_uri_complete",
                "expires_in",
            )
            if key in result
        } | {"interval": max(5, interval)}

    def tokens(self, form):
        result = self.request("POST", "/oauth/token", form=form)
        if (
            not re.fullmatch(
                r"fco_access_[A-Za-z0-9_-]{43}", str(result.get("access_token", ""))
            )
            or not re.fullmatch(
                r"fco_refresh_[A-Za-z0-9_-]{43}", str(result.get("refresh_token", ""))
            )
            or str(result.get("token_type", "")).lower() != "bearer"
            or not number(result.get("expires_in"))
            or not 0 <= result["expires_in"] <= 86400
            or not set(SCOPES.split()).issubset(str(result.get("scope", "")).split())
            or (
                "authorization_expires_at" in result
                and (
                    type(result["authorization_expires_at"]) is not int
                    or not 0 < result["authorization_expires_at"] <= 253402300799
                )
            )
        ):
            raise CloudError(
                "令牌响应或授权权限不完整，请重新授权", code="invalid_response"
            )
        if result["expires_in"] == 0:
            raise CloudError("应用授权已到期，请重新授权", code="expired_token")
        return result

    def account(self, token):
        data = self.request("GET", "/oauth/account", token=token)
        if (
            not isinstance(data.get("sub"), str)
            or not data["sub"]
            or not isinstance(data.get("name"), str)
            or not number(data.get("points"))
            or not number(data.get("vip_level"))
        ):
            raise CloudError("账户响应缺少必要字段")
        return {
            key: data.get(key)
            for key in (
                "sub",
                "name",
                "avatar",
                "points",
                "vip_level",
                "vip_expires_at",
            )
        }

    def check_in(self, token, mode=None):
        data = self.request(
            "POST" if mode else "GET",
            "/api/check-in",
            token=token,
            payload={"mode": mode} if mode else None,
        )
        if data.get("code") != 0 or not isinstance(data.get("data"), dict):
            raise CloudError("签到接口返回业务错误")
        result = data["data"]
        if mode:
            if (
                not isinstance(result.get("alreadyCheckedIn"), bool)
                or not number(result.get("reward"))
                or not number(result.get("balanceAfter"))
            ):
                raise CloudError("签到结果不完整，请刷新资料确认实际结果")
        elif (
            not isinstance(result.get("checkedInToday"), bool)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(result.get("today", "")))
            or not number(result.get("points"))
            or not number(result.get("currentStreak"))
            or not number(result.get("totalDays"))
            or not isinstance(result.get("recent"), list)
        ):
            raise CloudError("签到状态响应不完整")
        return result
