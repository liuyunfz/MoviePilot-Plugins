"""Native Vuetify cards; page rendering uses only a safe subset of cached data."""

import base64
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytz


def node(component, text=None, content=None, **props):
    result = {"component": component}
    if props:
        result["props"] = {
            key.rstrip("_").replace("_", "-"): value for key, value in props.items()
        }
    if text is not None:
        result["text"] = str(text)
    if content is not None:
        result["content"] = content
    return result


def section(title, icon, children):
    return node(
        "VCard",
        content=[
            node(
                "VCardTitle",
                content=[
                    node("VIcon", icon, size=22, color="primary", class_="mr-2"),
                    node("span", title, class_="text-subtitle-1 font-weight-bold"),
                ],
                class_="d-flex align-center pa-4",
            ),
            node("VDivider"),
            node("VCardText", content=children, class_="pa-4"),
        ],
        variant="outlined",
        rounded="xl",
        class_="mb-4 overflow-hidden",
    )


def column(child, md=6):
    return node(
        "VCol",
        content=[child],
        cols=6 if md == 3 else 12,
        sm=6 if md in (3, 6) else 12,
        md=md,
    )


def field(model, label, **props):
    return node(
        "VTextField",
        model=model,
        label=label,
        variant="outlined",
        density="comfortable",
        **props,
    )


def switch(model, label, **props):
    return node("VSwitch", model=model, label=label, **{"color": "primary", **props})


def stamp(value):
    if not value:
        return "—"
    try:
        date = (
            datetime.fromtimestamp(value, pytz.timezone("Asia/Shanghai"))
            if isinstance(value, (float, int))
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        return date.astimezone(pytz.timezone("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (ValueError, TypeError, OverflowError):
        return "—"


@lru_cache(maxsize=1)
def brand_icon():
    # Bundle the original TG artwork so local previews and installed pages do not
    # depend on a GitHub asset being published or reachable.
    return (
        "data:image/png;base64,"
        + base64.b64encode(Path(__file__).with_name("icon.png").read_bytes()).decode()
    )


def hero():
    return node(
        "VCard",
        content=[
            node(
                "VCardText",
                content=[
                    node(
                        "img",
                        src=brand_icon(),
                        alt="F-Cloudpan · Telegram 同款图标",
                        width=64,
                        height=64,
                        class_="mx-auto mb-3 rounded-xl",
                        style="display:block;object-fit:contain",
                    ),
                    node("div", "F-Cloudpan", class_="text-h5 font-weight-bold"),
                    node(
                        "div",
                        "每日签到 · 授权连接 · 积分随时掌握",
                        class_="text-body-2 text-medium-emphasis mt-2",
                    ),
                ],
                class_="pa-6 text-center",
            )
        ],
        rounded="xl",
        variant="tonal",
        color="primary",
        class_="mb-4",
    )


def authorization_ready(state):
    deadline = state.get("grant_expires_at")
    return (
        bool(state.get("tokens"))
        and not state.get("blocked")
        and not state.get("refresh_in_flight")
        and (deadline is None or deadline > time.time())
    )


def build_form(application_ready=True, state=None, config_error=""):
    state = state or {}
    ready = application_ready and not config_error and authorization_ready(state)
    sections = [
        hero(),
        section(
            "应用授权",
            "mdi-shield-key-outline",
            [
                *connection_cards(state, config_error),
                node(
                    "VAlert",
                    "插件已内置官方站点和应用。请先连接账号，授权成功后重新打开设置页，即可配置签到。已有签到设置会保留。"
                    if not ready
                    else "账号已连接。可调整签到设置，或在“查看数据”中管理本设备授权。",
                    type="info",
                    variant="tonal",
                    class_="mb-4",
                ),
                *(
                    []
                    if application_ready
                    else [
                        node(
                            "VAlert",
                            "正式应用配置尚未就绪，连接暂不可用。",
                            type="warning",
                            variant="tonal",
                        )
                    ]
                ),
                switch(
                    "prepare_auth",
                    "保存后连接 / 重新授权",
                    disabled=not application_ready,
                ),
                node(
                    "div",
                    "推荐点击底部“查看数据”→“连接云盘账号”，随后点击“前往 F-Cloudpan 授权”。也可打开上方开关并保存，再打开本页获取授权链接。",
                    class_="text-body-2 mt-2",
                ),
                node(
                    "div",
                    "取消连接、断开本设备授权均在“查看数据”中点击按钮立即执行，无需保存。",
                    class_="text-caption text-medium-emphasis mt-2",
                ),
            ],
        ),
        section(
            "网络设置",
            "mdi-network-outline",
            [
                switch(
                    "use_proxy",
                    "使用 MoviePilot 网络代理",
                    hint="默认开启；未配置 MP 代理时直连。修改后点击保存生效。",
                    persistent_hint=True,
                ),
                field(
                    "timeout",
                    "请求超时（秒）",
                    type="number",
                    min=5,
                    max=60,
                    class_="mt-4",
                ),
            ],
        ),
    ]
    if ready:
        sections.extend(
            [
                section(
                    "运行设置",
                    "mdi-calendar-clock",
                    [
                        node(
                            "VRow",
                            content=[
                                column(switch("enabled", "启用定时签到")),
                                column(switch("notify", "发送签到结果通知")),
                            ],
                        ),
                        node(
                            "VRow",
                            content=[
                                column(
                                    field(
                                        "cron",
                                        "签到周期（5 段 Cron）",
                                        placeholder="30 9 * * *",
                                        hint="按 MoviePilot 时区执行；云盘签到日按北京时间划分",
                                        persistent_hint=True,
                                    )
                                ),
                                column(
                                    node(
                                        "VSelect",
                                        model="mode",
                                        label="签到方式",
                                        variant="outlined",
                                        items=[
                                            {
                                                "title": "普通签到 · 固定奖励",
                                                "value": "STANDARD",
                                            },
                                            {
                                                "title": "随机签到 · 奖励可能为负",
                                                "value": "LAS_VEGAS",
                                            },
                                        ],
                                    )
                                ),
                            ],
                        ),
                        node(
                            "VRow",
                            content=[
                                column(switch("onlyonce", "保存后立即签到一次")),
                                column(switch("refresh_now", "保存后立即刷新资料")),
                            ],
                        ),
                    ],
                ),
                node(
                    "VExpansionPanels",
                    content=[
                        node(
                            "VExpansionPanel",
                            content=[
                                node("VExpansionPanelTitle", "高级设置"),
                                node(
                                    "VExpansionPanelText",
                                    content=[
                                        node(
                                            "VRow",
                                            content=[
                                                column(
                                                    field(
                                                        "jitter",
                                                        "随机延迟（秒）",
                                                        type="number",
                                                        min=0,
                                                        max=1800,
                                                    )
                                                ),
                                                column(
                                                    field(
                                                        "history_days",
                                                        "运行记录保留（天）",
                                                        type="number",
                                                        min=1,
                                                        max=365,
                                                    )
                                                ),
                                            ],
                                        ),
                                        node(
                                            "div",
                                            "默认随机延迟 0–300 秒，避免整点集中请求。令牌刷新与签到提交结果未知时不会盲目重试。",
                                            class_="text-caption text-medium-emphasis",
                                        ),
                                    ],
                                ),
                            ],
                        )
                    ],
                    class_="mb-4",
                ),
            ]
        )
    return [node("VForm", content=sections)]


def connection_cards(state, config_error=""):
    pending = state.get("pending") or {}
    now = time.time()
    deadline = state.get("grant_expires_at")
    ready = authorization_ready(state)
    message = state.get(
        "status",
        "在设置页打开“保存后连接 / 重新授权”并点击“保存”，再打开设置页获取授权链接",
    )
    if state.get("refresh_in_flight"):
        message = "上次令牌刷新结果未知，已停止重试，请重新授权"
    elif state.get("tokens") and deadline is not None and deadline <= now:
        message = "应用授权已到期，请重新授权"
    page = [
        node(
            "VAlert",
            config_error or message,
            type="info" if ready else "warning",
            variant="tonal",
            class_="mb-4",
        ),
    ]
    if pending.get("device_code") and not config_error:
        if pending.get("expires_at", 0) > now:
            link = (
                pending.get("verification_uri_complete") or pending["verification_uri"]
            )
            page.append(
                section(
                    "连接你的云盘账号",
                    "mdi-shield-account-outline",
                    [
                        node(
                            "div",
                            "点击下方按钮，在 F-Cloudpan 登录，核对应用名称与确认码后点击允许。",
                            class_="text-body-2 mb-3",
                        ),
                        node(
                            "VBtn",
                            "前往 F-Cloudpan 授权",
                            href=link,
                            target="_blank",
                            rel="noopener noreferrer",
                            color="primary",
                            prepend_icon="mdi-open-in-new",
                        ),
                        node(
                            "div",
                            "请核对云盘页面中的确认码",
                            class_="text-caption text-medium-emphasis mt-4",
                        ),
                        node(
                            "div",
                            pending["user_code"],
                            class_="text-h5 font-weight-bold my-2",
                            style="letter-spacing:0.12em;overflow-wrap:anywhere",
                        ),
                        node(
                            "div",
                            "也可访问 "
                            + pending["verification_uri"]
                            + " 并输入上方确认码。",
                            class_="text-body-2 mt-3",
                            style="overflow-wrap:anywhere",
                        ),
                        node(
                            "div",
                            "有效至 "
                            + stamp(pending["expires_at"])
                            + "；插件正在后台等待，完成后重新打开详情。取消连接请使用“查看数据”页的按钮。",
                            class_="text-caption text-medium-emphasis mt-3",
                        ),
                    ],
                )
            )
        else:
            page.append(
                node(
                    "VAlert",
                    "本次设备授权码已过期，请在配置页重新连接。",
                    type="warning",
                    variant="tonal",
                    class_="mb-4",
                )
            )
    return page


def action_button(label, action, *, confirmed=False, **props):
    button = node("VBtn", label, **props)
    button["events"] = {
        "click": {
            "api": "plugin/FCloudpanSign/action",
            "method": "POST",
            "params": {"action": action, "confirmed": confirmed},
        }
    }
    return button


def connection_actions(state):
    pending = bool(state.get("pending"))
    waiting = (state.get("pending") or {}).get("expires_at", 0) > time.time()
    connected = bool(state.get("tokens"))
    controls = [
        node(
            "div",
            "按钮点击后立即执行并刷新本页，无需保存设置。网络代理和超时使用已保存的配置。",
            class_="text-body-2 mb-3",
        ),
        node(
            "div",
            content=[
                action_button(
                    "重新连接账号" if connected else "连接云盘账号",
                    "connect",
                    color="primary",
                    disabled=waiting,
                    prepend_icon="mdi-link-variant",
                ),
                *(
                    [
                        action_button(
                            "取消本次连接",
                            "cancel",
                            variant="outlined",
                            prepend_icon="mdi-close-circle-outline",
                        )
                    ]
                    if pending
                    else []
                ),
            ],
            class_="d-flex flex-wrap ga-3",
        ),
    ]
    if connected:
        controls.append(
            node(
                "VExpansionPanels",
                content=[
                    node(
                        "VExpansionPanel",
                        content=[
                            node("VExpansionPanelTitle", "断开本设备授权…"),
                            node(
                                "VExpansionPanelText",
                                content=[
                                    node(
                                        "VAlert",
                                        "断开后，本设备停止使用当前授权，清除本地账号资料和运行记录。其他设备不受影响；以后使用需要重新授权。",
                                        type="warning",
                                        variant="tonal",
                                        class_="mb-3",
                                    ),
                                    action_button(
                                        "确认断开本设备授权",
                                        "revoke",
                                        confirmed=True,
                                        color="error",
                                        variant="outlined",
                                        prepend_icon="mdi-link-variant-off",
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
                class_="mt-4",
            )
        )
    return section("连接管理", "mdi-gesture-tap-button", controls)


def build_page(state, issuer, config_error, next_run):
    account = state.get("account") or {}
    overview = state.get("overview") or {}
    deadline = state.get("grant_expires_at")
    page = [hero(), *connection_cards(state, config_error)]
    if not config_error:
        page.append(connection_actions(state))
    if not authorization_ready(state):
        return page
    if account:
        avatar = node("VIcon", "mdi-account-outline", size=34)
        asset = urljoin(issuer + "/", str(account.get("avatar") or ""))
        parsed = urlsplit(asset)
        if (
            account.get("avatar")
            and parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
        ):
            avatar = node(
                "VImg", src=asset, alt="云盘头像", referrerpolicy="no-referrer"
            )
        today = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
        fresh = overview.get("today") == today
        checked = fresh and overview.get("checkedInToday")
        page.append(
            section(
                "我的云盘",
                "mdi-account-circle-outline",
                [
                    node(
                        "div",
                        content=[
                            node(
                                "VAvatar",
                                content=[avatar],
                                size=64,
                                color="surface-variant",
                                class_="mr-4",
                            ),
                            node(
                                "div",
                                content=[
                                    node(
                                        "div",
                                        str(account.get("name", "好心人"))[:80],
                                        class_="text-h6 font-weight-bold",
                                        style="overflow-wrap:anywhere",
                                    ),
                                    node(
                                        "div",
                                        f"UID {account.get('sub', '—')}",
                                        class_="text-caption text-medium-emphasis",
                                    ),
                                    node(
                                        "VChip",
                                        f"VIP {account.get('vip_level', 0)}",
                                        color="purple",
                                        size="small",
                                        variant="tonal",
                                        class_="mt-2 mr-2",
                                    ),
                                    node(
                                        "VChip",
                                        "今日已签到"
                                        if checked
                                        else "今日未签到"
                                        if fresh
                                        else "等待更新今日状态",
                                        color="success" if checked else "warning",
                                        size="small",
                                        variant="tonal",
                                        class_="mt-2",
                                    ),
                                ],
                            ),
                        ],
                        class_="d-flex align-center mb-5",
                    ),
                    node(
                        "VRow",
                        content=[
                            column(
                                node(
                                    "VCard",
                                    content=[
                                        node(
                                            "VCardText",
                                            content=[
                                                node(
                                                    "VIcon",
                                                    icon,
                                                    color=color,
                                                    size=22,
                                                    class_="mb-2",
                                                ),
                                                node(
                                                    "div",
                                                    value,
                                                    class_="text-h5 font-weight-bold",
                                                ),
                                                node(
                                                    "div",
                                                    label,
                                                    class_="text-caption text-medium-emphasis mt-1",
                                                ),
                                            ],
                                        )
                                    ],
                                    variant="tonal",
                                    color=color,
                                    rounded="lg",
                                ),
                                3,
                            )
                            for label, value, icon, color in [
                                (
                                    overview.get("pointName", "积分") + "余额",
                                    account.get("points", "—"),
                                    "mdi-wallet-outline",
                                    "primary",
                                ),
                                (
                                    "今日奖励" if fresh else "上次签到日奖励",
                                    overview.get("todayReward")
                                    if overview.get("todayReward") is not None
                                    else "—",
                                    "mdi-gift-outline",
                                    "success",
                                ),
                                (
                                    "连续签到（天）",
                                    overview.get("currentStreak", "—"),
                                    "mdi-fire",
                                    "orange",
                                ),
                                (
                                    "累计签到（天）",
                                    overview.get("totalDays", "—"),
                                    "mdi-calendar-check",
                                    "info",
                                ),
                            ]
                        ],
                    ),
                    node(
                        "div",
                        "VIP 到期：" + stamp(account.get("vip_expires_at")),
                        class_="text-caption text-medium-emphasis mt-4",
                    ),
                    node(
                        "div",
                        f"普通奖励：{overview.get('standardReward', '—')} · 随机范围：{overview.get('lasVegasMin', '—')} 至 {overview.get('lasVegasMax', '—')}",
                        class_="text-caption text-medium-emphasis mt-1",
                    ),
                ],
            )
        )
        recent = {
            item.get("checkedInOn"): item
            for item in overview.get("recent", [])
            if isinstance(item, dict)
        }
        try:
            end = datetime.strptime(overview["today"], "%Y-%m-%d").replace(
                tzinfo=pytz.UTC
            )
            tiles = []
            for offset in reversed(range(14)):
                date = end - timedelta(days=offset)
                row = recent.get(date.strftime("%Y-%m-%d"))
                tiles.append(
                    node(
                        "div",
                        content=[
                            node("div", date.strftime("%m/%d"), class_="text-caption"),
                            node(
                                "VIcon",
                                "mdi-check-circle-outline" if row else "mdi-minus",
                                size=22,
                                color="success" if row else "secondary",
                                class_="my-2",
                            ),
                            node(
                                "div",
                                f"{row['reward']:+g}"
                                if row and isinstance(row.get("reward"), (float, int))
                                else "—",
                                class_="text-caption",
                            ),
                        ],
                        class_="text-center pa-2 rounded-lg",
                        style="border:1px solid rgba(128,128,128,.2);min-width:0",
                    )
                )
            page.append(
                section(
                    "近 14 日签到",
                    "mdi-calendar-month-outline",
                    [
                        node(
                            "div",
                            content=tiles,
                            style="display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px",
                        ),
                        node(
                            "div",
                            "来自云盘签到记录，截至 "
                            + overview["today"]
                            + "；横线表示未读取到签到记录。",
                            class_="text-caption text-medium-emphasis mt-3",
                        ),
                    ],
                )
            )
        except (ValueError, KeyError):
            pass
    page.append(
        section(
            "运行状态",
            "mdi-clock-outline",
            [
                node(
                    "div",
                    "下次定时签到：" + (stamp(next_run) if next_run else "未安排"),
                    class_="text-body-2",
                ),
                node(
                    "div",
                    "最后资料同步：" + stamp(state.get("updated_at")),
                    class_="text-body-2 mt-2",
                ),
                node(
                    "div",
                    "应用授权到期："
                    + (
                        stamp(deadline)
                        if deadline is not None
                        else "云盘暂未返回到期时间"
                        if state.get("tokens")
                        else "—"
                    ),
                    class_="text-body-2 mt-2",
                ),
                node(
                    "div",
                    "时间显示为北京时间。页面读取本地缓存；在配置页选择“立即刷新资料”可更新。",
                    class_="text-caption text-medium-emphasis mt-3",
                ),
            ],
        )
    )
    history = state.get("history", [])[:50]
    rows = [
        node(
            "tr",
            content=[
                node("td", value)
                for value in (
                    stamp(row.get("time")),
                    row.get("action", ""),
                    row.get("status", ""),
                    row.get("reward") if row.get("reward") is not None else "—",
                    row.get("balance") if row.get("balance") is not None else "—",
                    f"{row.get('seconds', 0)}s",
                    row.get("detail", ""),
                )
            ],
        )
        for row in history
    ]
    page.append(
        section(
            "执行记录",
            "mdi-history",
            [
                node(
                    "div",
                    content=[
                        node(
                            "VTable",
                            content=[
                                node(
                                    "thead",
                                    content=[
                                        node(
                                            "tr",
                                            content=[
                                                node("th", title)
                                                for title in (
                                                    "时间",
                                                    "操作",
                                                    "结果",
                                                    "奖励",
                                                    "余额",
                                                    "耗时",
                                                    "说明",
                                                )
                                            ],
                                        )
                                    ],
                                ),
                                node("tbody", content=rows),
                            ],
                            density="compact",
                        )
                    ],
                    style="overflow-x:auto",
                ),
                node(
                    "div",
                    "最多展示最近 50 次执行",
                    class_="text-caption text-medium-emphasis mt-3",
                ),
            ]
            if rows
            else [
                node(
                    "div",
                    "暂无执行记录。授权后可在配置页立即签到或刷新资料。",
                    class_="text-body-2 text-medium-emphasis",
                )
            ],
        )
    )
    return page
