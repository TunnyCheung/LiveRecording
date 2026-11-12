"""基于 streamget 的多平台直播接口适配：B站 / 虎牙 / 斗鱼 / Acfun。

streamget 是 async 库，本项目 engine 是同步多线程，所以每次检测调用都在
独立的 event loop 里跑完一次完整流程，并新建一个 client 实例，避免跨
event loop 复用 aiohttp 连接池。

接口契约与 core/douyin.py / kuaishou.py / xiaohongshu.py 对齐：
  - looks_like_<platform>(line)
  - <Platform>Client.parse_input(line) -> dict
  - <Platform>Client.resolve(parsed)    加主播时解析出主播基本信息
  - <Platform>Client.room_info(room_id, definition) 每轮检测拉开播状态+流地址
  - <Platform>Client.pick_stream_url(info, definition, stream_type) 选流地址
  - <Platform>Client.home_url(user)
"""
import asyncio
import re

import requests
from streamget import AcfunLiveStream, BilibiliLiveStream, DouyuLiveStream, HuyaLiveStream


# 本项目"原画/蓝光/高清/标清/流畅" -> streamget 的档位
QUALITY_MAP = {
    "原画": "OD",
    "蓝光": "BD",
    "高清": "HD",
    "标清": "SD",
    "流畅": "LD",
}


class StreamGetError(Exception):
    pass


class _SafeDict(dict):
    def __missing__(self, key):
        return "xxx"


def _friendly(exc):
    """把 streamget 底层抛出的 JSON 解析报错翻译成能看懂的话。"""
    text = str(exc)
    if isinstance(exc, KeyError):
        return "平台页面结构暂时读不到，可能房间号不存在或接口已调整"
    if isinstance(exc, (TypeError, AttributeError)):
        return "平台接口返回了暂时不认识的格式，稍后自动重试"
    if ("Expecting value" in text or "Expecting property name" in text
            or "Expecting ','" in text or "Extra data" in text
            or "Unterminated string" in text):
        return "接口没返回有效数据（可能被风控或需要登录），稍后自动重试"
    return text


# ---------------- 链接识别 ----------------

def looks_like_bilibili(line):
    return "bilibili.com" in (line or "") or "b23.tv" in (line or "")


def looks_like_huya(line):
    return "huya.com" in (line or "")


def looks_like_douyu(line):
    return "douyu.com" in (line or "")


def looks_like_acfun(line):
    return "acfun.cn" in (line or "")


# ---------------- URL 正则 ----------------

BILI_ROOM_RE = re.compile(r"live\.bilibili\.com/(\d+)")
HUYA_ROOM_RE = re.compile(r"huya\.com/([A-Za-z0-9_]+)")
DOUYU_RID_RE = re.compile(r"rid=(\d+)")
DOUYU_ROOM_RE = re.compile(r"douyu\.com/([A-Za-z0-9_]+)")
ACFUN_ROOM_RE = re.compile(r"(?:live|m)\.acfun\.cn/live/(\d+)")


def _extract_room(platform, line):
    line = (line or "").strip()
    if platform == "bilibili":
        m = BILI_ROOM_RE.search(line)
        return m.group(1) if m else None
    if platform == "huya":
        m = HUYA_ROOM_RE.search(line)
        return m.group(1) if m else None
    if platform == "douyu":
        m = DOUYU_RID_RE.search(line)
        if m:
            return m.group(1)
        m = DOUYU_ROOM_RE.search(line)
        return m.group(1) if m else None
    if platform == "acfun":
        m = ACFUN_ROOM_RE.search(line)
        return m.group(1) if m else None
    return None


# ---------------- 异步核心 ----------------

async def _fetch_all(cls, url, quality, platform):
    """在一个 event loop 里跑完取房间数据 + 取流地址。

    返回 (data, stream_data)：
      - data: 房间原始 dict（含主播名、标题、开播状态）
      - stream_data: StreamData 对象（含 flv_url/m3u8_url），虎牙 app 模式下为 None
        因为 data 里已经直接带流地址了。
    """
    client = cls()
    if platform == "huya":
        # 虎牙 app 接口对“英雄联盟”等赛事会转回 web 结构，先统一过一遍
        # fetch_stream_url，免得把带 gameStreamInfoList 的在播房间误判成未开播。
        data = await client.fetch_app_stream_data(url)
        sd = await client.fetch_stream_url(data, quality)
        return data, sd
    if platform == "douyu":
        # 斗鱼 get_room_id() 能把 yyf 这类别名换成数字房间号，拿到别名后
        # fetch_web_stream_data() 只会返回 room_id，后面的取流要的是数字号。
        url = "https://www.douyu.com/" + str(await client.get_room_id(url))
    data = await client.fetch_web_stream_data(url)
    is_live = bool(data.get("live_status") or data.get("is_live"))
    if not is_live:
        return data, None
    try:
        sd = await client.fetch_stream_url(data, quality)
        return data, sd
    except Exception as exc:  # noqa: BLE001
        return data, ("ERROR", repr(exc))


def _run_async(cls, url, quality, platform):
    return asyncio.run(_fetch_all(cls, url, quality, platform))


# ---------------- 通用 Client ----------------

class StreamGetClient:
    """B站 / 虎牙 / 斗鱼 / Acfun 共用的 client。"""

    # 这个平台的原始数据里到底有没有在线人数。
    # 实测：streamget 给 B站 / 虎牙 / Acfun 的返回没有稳定的在线人数
    # （B站的人气值要走另一个房间接口），所以这些平台不显示在线人数。
    supports_user_count = False

    def __init__(self, platform_key, cls, room_url_template, home_url_template):
        self.platform = platform_key
        self._cls = cls
        self._room_url_tpl = room_url_template
        self._home_url_tpl = home_url_template

    def bootstrap(self):
        pass

    # ---- 链接解析 ----
    def parse_input(self, line):
        line = (line or "").strip()
        if self.platform == "bilibili" and "b23.tv" in line:
            room = self._expand_bilibili_short(line)
            if not room:
                return None
        else:
            room = _extract_room(self.platform, line)
        if not room and self.platform == "huya" and "huya.com" in (line or ""):
            match = re.search(r"(?:https?://)?[^/\s]+/([A-Za-z0-9_]+)", line)
            if match:
                room = match.group(1)
        if not room:
            return None
        return {"room_id": room}

    def _expand_bilibili_short(self, line):
        """展开 b23.tv 短链；视频链不是直播间，直接拒绝而不是拿 av 当房间号。"""
        match = re.search(r"(?:https?://)?b23\.tv/[A-Za-z0-9]+", line)
        if not match:
            return ""
        url = match.group(0)
        if not url.startswith("http"):
            url = "https://" + url
        try:
            resp = requests.get(
                url, timeout=20, allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        except requests.RequestException:
            return ""
        target = resp.url or ""
        room = BILI_ROOM_RE.search(target)
        if room:
            return room.group(1)
        # b23 偶尔不跳转只回中间页，页面里仍会带直播间地址
        room = BILI_ROOM_RE.search(resp.text[:200000])
        return room.group(1) if room else ""

    # ---- 加主播时解析 ----
    def resolve(self, parsed):
        room_id = parsed["room_id"]
        url = self._room_url_tpl.format(room_id=room_id)
        try:
            data, sd = _run_async(self._cls, url, "OD", self.platform)
        except Exception as exc:  # noqa: BLE001
            raise StreamGetError(f"读取直播间失败：{_friendly(exc)}")
        nickname = self._extract_nickname(data)
        avatar = self._extract_avatar(data)
        return {
            "nickname": nickname or f"{self.platform}{room_id[-6:]}",
            "userid": str(room_id),
            "sec_uid": "",
            "web_rid": str(room_id),
            "unique_id": str(room_id),
            "avatar": avatar,
            "title": self._extract_title(data),
        }

    # ---- 每轮检测 ----
    def room_info(self, room_id, definition="蓝光"):
        url = self._room_url_tpl.format(room_id=room_id)
        quality = QUALITY_MAP.get(definition, "BD")
        try:
            data, sd = _run_async(self._cls, url, quality, self.platform)
        except Exception as exc:  # noqa: BLE001
            raise StreamGetError(f"检测失败：{_friendly(exc)}")

        nickname = self._extract_nickname(data)
        title = self._extract_title(data)
        user_count = self._extract_user_count(data)
        is_live = bool(data.get("live_status") or data.get("is_live"))
        if sd is not None and not isinstance(sd, tuple):
            is_live = is_live or bool(getattr(sd, "is_live", False))

        info = {
            "live": is_live,
            "title": title,
            "user_count": user_count,
            "nickname": nickname,
            "avatar": self._extract_avatar(data),
            "web_rid": str(room_id),
            "unique_id": str(room_id),
            "flv": {},
            "hls": {},
            "note": "",
            "live_known": True,
        }

        if not is_live:
            return info

        # 统一使用 StreamData；虎牙 app 返回的原始结构也已在上游转好
        if sd is None:
            return info
        if isinstance(sd, tuple) and sd[0] == "ERROR":
            info["note"] = f"正在直播，但取流失败：{sd[1]}"
            return info

        flv_url = getattr(sd, "flv_url", "") or ""
        m3u8_url = getattr(sd, "m3u8_url", "") or ""
        record_url = getattr(sd, "record_url", "") or ""
        if not flv_url and record_url:
            flv_url = record_url
        if not m3u8_url and record_url and ".m3u8" in record_url:
            m3u8_url = record_url
        if flv_url:
            info["flv"][definition] = flv_url
        if m3u8_url:
            info["hls"][definition] = m3u8_url
        return info

    # ---- 选流地址 ----
    def pick_stream_url(self, info, definition, stream_type):
        table = info.get("hls" if stream_type == "m3u8" else "flv") or {}
        if not table:
            table = info.get("flv") or info.get("hls") or {}
        if definition in table:
            return table[definition]
        return next(iter(table.values()), "")

    # ---- 打开主页 ----
    def home_url(self, user):
        room_id = (user or {}).get("web_rid") or (user or {}).get("userid") or ""
        if not room_id:
            return ""
        if self.platform == "huya":
            room_id = re.sub(r"\D", "", str(room_id)) or room_id
        return self._home_url_tpl.format_map(_SafeDict(room_id=room_id))

    # ---- 字段提取（各平台差异封装） ----
    def _extract_nickname(self, data):
        if not isinstance(data, dict):
            return ""
        if data.get("anchor_name"):
            return data["anchor_name"]
        # 虎牙 web 模式：data[0].gameLiveInfo.nick
        try:
            return data["data"][0]["gameLiveInfo"].get("nick", "")
        except (KeyError, IndexError, AttributeError, TypeError):
            return ""

    def _extract_avatar(self, data):
        if not isinstance(data, dict):
            return ""
        try:
            return data["data"][0]["gameLiveInfo"].get("avatar180", "")
        except (KeyError, IndexError, AttributeError, TypeError):
            return ""

    def _extract_title(self, data):
        if not isinstance(data, dict):
            return ""
        title = data.get("title") or ""
        if title:
            return title
        try:
            return data["data"][0]["gameLiveInfo"].get("introduction", "") or ""
        except (KeyError, IndexError, AttributeError, TypeError):
            return ""

    def _extract_user_count(self, data):
        if not self.supports_user_count or not isinstance(data, dict):
            return 0
        try:
            return int(data["data"][0]["gameLiveInfo"].get("attendeeCount", 0) or 0)
        except (KeyError, IndexError, AttributeError, TypeError, ValueError):
            return 0


# ---------------- 对外暴露的四个 client ----------------

class BilibiliClient(StreamGetClient):
    def __init__(self):
        super().__init__(
            platform_key="bilibili",
            cls=BilibiliLiveStream,
            room_url_template="https://live.bilibili.com/{room_id}",
            home_url_template="https://live.bilibili.com/{room_id}",
        )


class HuyaClient(StreamGetClient):
    def __init__(self):
        super().__init__(
            platform_key="huya",
            cls=HuyaLiveStream,
            room_url_template="https://www.huya.com/{room_id}",
            home_url_template="https://www.huya.com/{room_id}",
        )


class DouyuClient(StreamGetClient):
    # 斗鱼返回里有 gameLiveInfo.attendeeCount，是真实的人数
    supports_user_count = True

    def __init__(self):
        super().__init__(
            platform_key="douyu",
            cls=DouyuLiveStream,
            room_url_template="https://www.douyu.com/{room_id}",
            home_url_template="https://www.douyu.com/{room_id}",
        )


class AcfunClient(StreamGetClient):
    def __init__(self):
        super().__init__(
            platform_key="acfun",
            cls=AcfunLiveStream,
            room_url_template="https://live.acfun.cn/live/{room_id}",
            home_url_template="https://live.acfun.cn/live/{room_id}",
        )
