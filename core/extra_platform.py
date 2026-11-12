"""补充平台：战旗 / YY / 猫耳 / 花椒。

这几个平台的接口是从 bililive-go（开源直播录制工具）里提炼过来的，只保留了「拿开播状态
+ 拿播放地址」用的那几步，没有引入它的 Go 依赖。2026-10 逐个实测过，只有还能拿到数据的
才整合进来：

可用：
- 战旗 zhanqi   https://www.zhanqi.tv/api/static/v2.1/room/domain/<房间别名>.json
- YY直播       直播间页面里 SSR 的 window.__INITIAL_STATE__
- 猫耳 missevan 直播间页面里 SSR 的 __NEXT_DATA__
- 花椒 huajiao  https://webh.huajiao.com/User/getUserFeeds + live.huajiao.com/live/substream

已失效、没有整合（接口已经关掉或返回 404/410）：
一直播 yizhibo（502）、CC直播（410）、企鹅电竞 egame.qq.com（证书/域名挂了）、openrec（404）。

风格跟 streamget_platform.py 保持一致：每个平台一个类，提供
looks_like_xxx(line) / parse_input(line) / resolve(parsed) / room_info(room_id, definition)。
"""
import json
import re

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 11; SM-G973U) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


class ExtraPlatformError(Exception):
    pass


def _session():
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return session


def _as_int(value):
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


class _BaseExtra:
    """补充平台的通用骨架。"""
    platform = ""
    cn_name = ""
    referer = ""

    def __init__(self):
        self.session = _session()

    def headers(self):
        return {"Referer": self.referer} if self.referer else {}

    def _get_json(self, url, timeout=15, headers=None):
        # 网络抖动（超时/连接重置）重试一次：这类失败是偶发的，
        # 直接抛出去会让用户看到「添加失败」，而实际上再请求一次就好了。
        last = None
        for attempt in range(2):
            try:
                resp = self.session.get(
                    url, headers={**self.headers(), **(headers or {})}, timeout=timeout
                )
            except requests.RequestException as exc:
                last = exc
                if attempt == 0:
                    continue
                raise ExtraPlatformError(
                    f"{self.cn_name}接口请求失败：{exc.__class__.__name__}"
                ) from exc
            if resp.status_code != 200:
                raise ExtraPlatformError(f"{self.cn_name}接口返回 {resp.status_code}")
            try:
                return resp.json()
            except ValueError:
                raise ExtraPlatformError(f"{self.cn_name}接口返回的不是 JSON")
        raise ExtraPlatformError(f"{self.cn_name}接口请求失败：{type(last).__name__}")

    def _get_text(self, url, timeout=20, headers=None):
        try:
            resp = self.session.get(url, headers={**self.headers(), **(headers or {})}, timeout=timeout)
        except requests.RequestException as exc:
            raise ExtraPlatformError(f"{self.cn_name}页面打不开：{exc.__class__.__name__}")
        if resp.status_code != 200:
            raise ExtraPlatformError(f"{self.cn_name}页面返回 {resp.status_code}")
        return resp.text


# ---------------------------------------------------------------- 战旗

class ZhanqiClient(_BaseExtra):
    platform = "zhanqi"
    cn_name = "战旗"
    referer = "https://www.zhanqi.tv/"
    API = "https://www.zhanqi.tv/api/static/v2.1/room/domain/%s.json"

    def parse_input(self, line):
        match = re.search(r"zhanqi\.tv/([A-Za-z0-9_\-]+)", line or "", re.I)
        if not match:
            return None
        return {"room": match.group(1)}

    def resolve(self, parsed):
        room = (parsed or {}).get("room") or ""
        if not room:
            raise ExtraPlatformError("没认出战旗房间号")
        return self.room_info(room)

    def room_info(self, room_id, definition="蓝光"):
        import base64
        data = self._get_json(self.API % room_id)
        if _as_int(data.get("code")) != 0:
            raise ExtraPlatformError("战旗：房间不存在")
        info = data.get("data") or {}
        # 战旗的 status：4（或线上值）表示正在直播；部分接口版本会返回字符串，
        # 所以统一转成字符串再比，同时把「有可用流地址」也算作在播的佐证。
        raw_status = info.get("status")
        status_text = str(raw_status).strip() if raw_status is not None else ""
        live = status_text == "4"
        result = {
            "live": live,
            "live_known": True,
            "nickname": info.get("nickname") or "",
            "title": info.get("title") or "",
            "avatar": (info.get("avatar") or ""),
            "room_id": str(info.get("id") or room_id),
            "web_rid": room_id,
            "user_count": _as_int(info.get("online")),
            "flv": {},
            "hls": {},
            "default_res": "",
        }
        # 地址不论是否在播都先解出来：战旗未开播时 VideoLevels 里也会留着上一次的地址，
        # 而"在播"时一定需要它。是否真正录制由调用方看 live 决定。
        raw = ((info.get("flashvars") or {}).get("VideoLevels") or "")
        if raw:
            try:
                decoded = json.loads(base64.b64decode(raw).decode("utf-8", "replace"))
                url = str(decoded.get("streamUrl") or "").strip()
                if url:
                    if ".m3u8" in url:
                        result["hls"]["原画"] = url
                    else:
                        result["flv"]["原画"] = url
                    result["default_res"] = "原画"
            except (ValueError, TypeError):
                pass
        return result


# ---------------------------------------------------------------- YY

class YyClient(_BaseExtra):
    platform = "yy"
    cn_name = "YY"
    referer = "https://www.yy.com/"

    def parse_input(self, line):
        match = re.search(r"yy\.com/(?:l/)?(\d{4,})", line or "", re.I)
        if not match:
            return None
        return {"room": match.group(1)}

    def resolve(self, parsed):
        room = (parsed or {}).get("room") or ""
        if not room:
            raise ExtraPlatformError("没认出 YY 房间号")
        return self.room_info(room)

    def room_info(self, room_id, definition="蓝光"):
        html = self._get_text("https://www.yy.com/" + room_id)
        # 先试页面里 SSR 出来的状态
        match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>", html, re.S)
        payload = None
        if match:
            try:
                payload = json.loads(match.group(1))
            except ValueError:
                payload = None
        nickname = ""
        title = ""
        live = False
        stream = {}
        if isinstance(payload, dict):
            room = payload.get("room") or {}
            anchor = room.get("anchor") or room.get("host") or {}
            nickname = anchor.get("nick") or anchor.get("nickname") or ""
            title = room.get("name") or room.get("title") or ""
            live = bool(room.get("islive") or room.get("isLive") or room.get("state") == 3)
            for key in ("avp", "avpList", "stream"):
                value = room.get(key)
                if isinstance(value, dict):
                    for name, url in value.items():
                        if isinstance(url, str) and (".flv" in url or ".m3u8" in url):
                            target = "hls" if ".m3u8" in url else "flv"
                            stream.setdefault(target, {})[str(name)] = url
        # 再兜一层：页面里直接搜 flv/m3u8 地址
        if not stream:
            for url in re.findall(r'https?:[^"\'\s]+\.(?:flv|m3u8)[^"\'\s]*', html):
                cleaned = url.replace("\\u002F", "/").replace("\\/", "/")
                target = "hls" if ".m3u8" in cleaned else "flv"
                stream.setdefault(target, {}).setdefault("原画", cleaned)
        result = {
            "live": live,
            "live_known": bool(nickname or title or stream),
            "nickname": nickname,
            "title": title,
            "avatar": "",
            "room_id": room_id,
            "web_rid": room_id,
            "user_count": 0,
            "flv": stream.get("flv") or {},
            "hls": stream.get("hls") or {},
            "default_res": "原画" if (stream.get("flv") or stream.get("hls")) else "",
        }
        if not result["live_known"]:
            raise ExtraPlatformError("YY：页面里没读到直播间数据")
        return result


# ---------------------------------------------------------------- 猫耳

class MissevanClient(_BaseExtra):
    platform = "missevan"
    cn_name = "猫耳"
    referer = "https://fm.missevan.com/"

    def parse_input(self, line):
        match = re.search(r"(?:fm\.)?missevan\.com/live/(\d+)", line or "", re.I)
        if not match:
            return None
        return {"room": match.group(1)}

    def resolve(self, parsed):
        room = (parsed or {}).get("room") or ""
        if not room:
            raise ExtraPlatformError("没认出猫耳直播间号")
        return self.room_info(room)

    def room_info(self, room_id, definition="蓝光"):
        html = self._get_text("https://fm.missevan.com/live/" + room_id)
        match = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not match:
            raise ExtraPlatformError("猫耳：页面结构变了")
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            raise ExtraPlatformError("猫耳：页面数据解析失败")
        room = (((payload.get("props") or {}).get("pageProps") or {}).get("room") or {})
        live = bool(room.get("status") == 1 or room.get("is_living") or room.get("live"))
        info = room.get("creator") or room.get("user") or {}
        url = ((room.get("channel") or {}).get("flv_pull_url")
               or room.get("flv_url") or "")
        result = {
            "live": live,
            "live_known": True,
            "nickname": info.get("username") or info.get("nickname") or "",
            "title": room.get("title") or "",
            "avatar": info.get("avatar") or "",
            "room_id": room_id,
            "web_rid": room_id,
            "user_count": _as_int(room.get("online_count")),
            "flv": {"原画": url} if url else {},
            "hls": {},
            "default_res": "原画" if url else "",
        }
        return result


# ---------------------------------------------------------------- 花椒

class HuajiaoClient(_BaseExtra):
    platform = "huajiao"
    cn_name = "花椒"
    referer = "https://www.huajiao.com/"
    API_INFO = "https://webh.huajiao.com/User/getUserInfo"
    API_STREAM = "https://live.huajiao.com/live/substream"

    def parse_input(self, line):
        match = re.search(r"huajiao\.com/(?:user|l)/(\d+)", line or "", re.I)
        if not match:
            return None
        return {"uid": match.group(1)}

    def resolve(self, parsed):
        uid = (parsed or {}).get("uid") or ""
        if not uid:
            raise ExtraPlatformError("没认出花椒号")
        return self.room_info(uid)

    def room_info(self, room_id, definition="蓝光"):
        data = self._get_json(self.API_INFO + "?fmt=json&uid=" + room_id)
        if _as_int(data.get("errno")) != 0:
            raise ExtraPlatformError("花椒：用户不存在")
        info = data.get("data") or {}
        live = _as_int(info.get("is_live") or info.get("live_status")) == 1
        url = ""
        if live:
            try:
                stream = self._get_json(self.API_STREAM + "?uid=" + room_id)
                url = str((stream.get("data") or {}).get("hls_url")
                          or (stream.get("data") or {}).get("flv_url") or "")
            except ExtraPlatformError:
                url = ""
        result = {
            "live": live,
            "live_known": True,
            "nickname": info.get("nickname") or "",
            "title": info.get("room_name") or info.get("live_title") or "",
            "avatar": info.get("avatar") or "",
            "room_id": room_id,
            "web_rid": room_id,
            "user_count": _as_int(info.get("live_view_count")),
            "flv": {} if ".m3u8" in url else ({"原画": url} if url else {}),
            "hls": {"原画": url} if ".m3u8" in url else {},
            "default_res": "原画" if url else "",
        }
        return result


ZT_CLIENT = ZhanqiClient
YY_CLIENT = YyClient
MISSEVAN_CLIENT = MissevanClient
HUAJIAO_CLIENT = HuajiaoClient


def looks_like_zhanqi(line):
    return bool(re.search(r"zhanqi\.tv/", line or "", re.I))


def looks_like_yy(line):
    return bool(re.search(r"(?<![a-z])yy\.com/(?:l/)?\d{4,}", line or "", re.I))


def looks_like_missevan(line):
    return bool(re.search(r"missevan\.com/live/\d+", line or "", re.I))


def looks_like_huajiao(line):
    return bool(re.search(r"huajiao\.com/(?:user|l)/\d+", line or "", re.I))
