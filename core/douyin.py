"""抖音网页接口：链接解析、开播状态、直播源地址。

主页链接只有 ``sec_uid``，抖音官方主页接口 ``profile/other`` 在这台机器上会
返回空 body（强度风控）。因此检测按下面的顺序降级，任何一层给出明确结果就返回：

1. 已知 ``web_rid``：``live.douyin.com/webcast/room/web/enter/``（最稳定）
2. 主页 ``sec_uid``：streamget 的官方 reflow 链路（与原小绿点一致）
3. 主页 ``sec_uid``：官方主页接口，补资料或取 room_data
4. 可选的第三方 API（配置了 api key 才走）

没有任何一层能确认状态时返回 ``live_known=False``，界面显示“无法确认”，绝不
把被风控当成“未开播”。
"""
import json
import random
import re
import string
import time
import urllib.parse

import requests

from .count import parse_count
from .douyin_sign import LIVE_WINDOW_ENV, PROFILE_WINDOW_ENV, ab_sign

try:  # streamget 已在 requirements 里，源码/打包版都随程序带上
    from streamget import DouyinLiveStream as _StreamGetDouyin
except Exception:  # pragma: no cover - 打包缺包时仍可用本文件的原生实现
    _StreamGetDouyin = None

UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
UA_MOB = (
    "Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 "
    "(KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile Safari/537.36"
)

QUALITY_ORDER = {
    "原画": ["FULL_HD1", "HD1", "SD1", "SD2"],
    "蓝光": ["FULL_HD1", "HD1", "SD1", "SD2"],
    "高清": ["HD1", "SD1", "SD2", "FULL_HD1"],
    "标清": ["SD1", "SD2", "HD1", "FULL_HD1"],
    "流畅": ["SD2", "SD1", "HD1", "FULL_HD1"],
}

FEED_PARAMS = {
    "aid": "6383", "app_name": "douyin_web", "live_id": "1", "device_platform": "web",
    "language": "zh-CN", "browser_language": "zh-CN",
    "browser_platform": "Win32", "browser_name": "Chrome", "browser_version": "126.0.0.0",
}

LIVE_URL_RE = re.compile(r"live\.douyin\.com/([0-9]{6,})")
ROOM_ID_RE = re.compile(r"/webcast/(?:reflow/)?(\d{6,})")
SHORT_URL_RE = re.compile(r"v\.douyin\.com/[A-Za-z0-9_\-]+")
SEC_UID_RE = re.compile(r"(?:user/|sec_uid=)(MS4wLjAB[A-Za-z0-9_\-]+)")
BARE_RID_RE = re.compile(r"^\d{6,}$")
URL_RE = re.compile(
    r"(?:https?://)?[A-Za-z0-9\-.]*douyin\.com/[^\s\u3000，。！？、；：\"'）】》〕」]+", re.I)
TRAIL_RE = re.compile(r"[，。！？、；：）】》\"']+$")

# 小绿点同款：没有 Cookie 时官方 UI 也会带一个匿名 ttwid。它不绑定账号，
# 只用来让 live.douyin.com 的 room/web/enter 不把请求当成空手脚本。
ANON_TTWID = (
    "ttwid=1%7CmDcInbJ7AJ-2PGtsgrG4xj7SOiNMzePqQBF1LMO2Qkg%7C1761107324%7C"
    "bbf97c2cd9f8eae8e8c36db4ef50c323deaa4b161179170aaf659590867c162d"
)
STREAMGET_RETRY_SECONDS = 180
# live.douyin.com 上的开播状态接口：只吃数字 uid，一次一个。它和 www.douyin.com 的主页
# 接口不是一条链路 —— 主页接口返回空 body 时，未开播的主播就靠它给出明确结论。
LIVE_STATUS_URL = "https://live.douyin.com/webcast/distribution/check_user_live_status/"
LIVE_STATUS_CACHE_SECONDS = 8
IDENTITY_CACHE_SECONDS = 1800


class DouyinError(Exception):
    pass


def _first_url(node):
    if isinstance(node, dict):
        urls = node.get("url_list") or []
        if urls:
            return urls[0]
    return ""


def _as_int(value):
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _grab_url(text):
    match = URL_RE.search(text or "")
    if not match:
        return ""
    url = TRAIL_RE.sub("", match.group(0).strip())
    return url if url.startswith("http") else "https://" + url


def _mstoken(length=184):
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits
    rng = random.SystemRandom()
    return "".join(chars[rng.randrange(len(chars))] for _ in range(length))


def _verify_fp():
    e = list("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    n36 = ""
    val = int(time.time() * 1000)
    while val:
        n36 = digits[val % 36] + n36
        val //= 36
    rng = random.SystemRandom()
    r = [""] * 36
    r[8] = "_"
    r[13] = "_"
    r[18] = "_"
    r[23] = "_"
    r[14] = "4"
    for i in range(36):
        if not r[i]:
            o = rng.randrange(len(e))
            r[i] = e[(3 & o | 8) if i == 19 else o]
    return "verify_" + n36 + "_" + "".join(r)


class DouyinClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA_PC, "Accept-Language": "zh-CN,zh;q=0.9"})
        self.last_bootstrap = 0.0
        self._ttwid = ANON_TTWID.split("=", 1)[1]
        self._ac_nonce = ""
        self._last_room = {}
        self._cache = {}
        self._streamget_next = {}
        self._live_status = {}   # uid -> (取到的时间, True/False)
        self._identity = {}      # sec_uid -> (取到的时间, 分享接口资料)

    def bootstrap(self):
        """注册一次 ttwid 设备指纹，后续接口不再每次空手去撞风控。"""
        if time.time() - self.last_bootstrap < 1800:
            return
        try:
            resp = self.session.get("https://www.douyin.com/", timeout=20)
            for part in resp.headers.get("set-cookie", "").split(","):
                part = part.strip()
                if part.lower().startswith("__ac_nonce="):
                    self._ac_nonce = part.split(";", 1)[0].split("=", 1)[1]
            reg = self.session.post(
                "https://ttwid.bytedance.com/ttwid/register/",
                json={"aid": 6383, "service": "www.douyin.com"},
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://www.douyin.com",
                    "Referer": "https://www.douyin.com/",
                },
                timeout=20,
            )
            for part in reg.headers.get("set-cookie", "").split(","):
                part = part.strip()
                if part.lower().startswith("ttwid="):
                    self._ttwid = part.split(";", 1)[0].split("=", 1)[1]
                    break
            self.last_bootstrap = time.time()
        except requests.RequestException:
            pass

    def _cookie(self):
        parts = []
        if self._ttwid:
            parts.append("ttwid=" + self._ttwid)
        if self._ac_nonce:
            parts.append("__ac_nonce=" + self._ac_nonce)
        return "; ".join(parts)

    # ---------- 开播状态（uid） ----------

    def live_status_by_uid(self, uid):
        """按 uid 问一次开播状态。返回 True（在播）/ False（明确未开播）/ None（未知）。

        None 必须当成「这轮没拿到结论」继续走别的链路，绝不能当成未开播：
        uid 不是数字、接口非 0、JSON 坏了都算未知。主页接口（www.douyin.com）在
        这台机器上返回空 body，未开播的主播就靠这条链路给结论。
        """
        uid = str(uid or "").strip()
        if not uid.isdigit():
            return None
        now = time.time()
        hit = self._live_status.get(uid)
        if hit and now - hit[0] < LIVE_STATUS_CACHE_SECONDS:
            return hit[1]
        params = {
            "user_ids": uid,
            "aid": "6383",
            "app_name": "douyin_web",
            "live_id": "1",
            "device_platform": "web",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "126.0.0.0",
        }
        try:
            resp = self.session.get(
                LIVE_STATUS_URL + "?" + urllib.parse.urlencode(params),
                headers={"Referer": "https://live.douyin.com/", "Cookie": self._cookie()},
                timeout=15,
            )
            if resp.status_code != 200 or not resp.content:
                return None
            data = resp.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict) or data.get("status_code") is None:
            return None
        if _as_int(data.get("status_code")) != 0:
            return None
        rows = data.get("data") or []
        if not rows or "user_live" not in rows[0]:
            return None
        live = any(
            _as_int(item.get("live_status")) == 1
            for item in (rows[0].get("user_live") or [])
        )
        self._live_status[uid] = (now, live)
        return live

    def _signed_url(self, base, params, page_id, rp_u, window_env, ua):
        query = urllib.parse.urlencode(params)
        bogus = ab_sign(query, ua, page_id=page_id, rp_u=rp_u, window_env_str=window_env)
        return base + "?" + query + "&a_bogus=" + urllib.parse.quote(bogus)

    # ---------- 直播间 ----------

    def room_by_web_rid(self, web_rid):
        data = None
        last_error = None
        # 小绿点同款匿名请求。主播记录里已经有 web_rid 时这是唯一稳定链路，
        # 不应再因为签名环境或其他参数变化把它拆掉。
        for spec in self._room_request_specs(web_rid):
            try:
                if spec["signed"]:
                    url = self._signed_url(
                        spec["url"], spec["params"], page_id=110624, rp_u="hwj",
                        window_env=LIVE_WINDOW_ENV, ua=UA_PC,
                    )
                else:
                    url = spec["url"] + "?" + urllib.parse.urlencode(spec["params"])
                resp = self.session.get(
                    url,
                    headers={
                        "Referer": f"https://live.douyin.com/{web_rid}",
                        "Cookie": spec["cookie"],
                        "User-Agent": spec["ua"],
                    },
                    timeout=20,
                )
                if resp.status_code != 200 or not resp.content:
                    last_error = f"HTTP {resp.status_code}, body {len(resp.content)} bytes"
                    continue
                data = resp.json()
                break
            except (requests.RequestException, ValueError) as exc:
                last_error = repr(exc)
        if data is None:
            return self._cached_room(
                web_rid, live_known=False,
                note=f"抖音直播间接口暂时没有返回（{last_error or '空响应'}），稍后自动重试",
            )
        rooms = ((data.get("data") or {}).get("data") or [])
        owner = ((data.get("data") or {}).get("user") or {})
        if not rooms:
            info = self._owner_only(owner, web_rid)
        else:
            info = self._normalize(rooms[0], owner, web_rid)
        if info.get("sec_uid") or info.get("live"):
            self._remember_room(web_rid, info)
        return info

    def _room_request_specs(self, web_rid):
        basic = {
            "aid": "6383",
            "app_name": "douyin_web",
            "live_id": "1",
            "device_platform": "web",
            "language": "zh-CN",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "126.0.0.0",
            "web_rid": str(web_rid),
            "is_need_double_stream": "false",
            "msToken": "",
        }
        full = dict(FEED_PARAMS)
        full.update({
            "web_rid": str(web_rid),
            "is_need_double_stream": "false",
            "msToken": "",
        })
        tiny = {
            "aid": "6383", "app_name": "douyin_web", "live_id": "1",
            "device_platform": "web", "web_rid": str(web_rid),
        }
        return [
            {
                "url": "https://live.douyin.com/webcast/room/web/enter/",
                "params": basic, "signed": False,
                "cookie": ANON_TTWID, "ua": UA_PC,
            },
            {
                "url": "https://live.douyin.com/webcast/room/web/enter/",
                "params": full, "signed": False,
                "cookie": ANON_TTWID, "ua": UA_PC,
            },
            {
                "url": "https://live.douyin.com/webcast/room/web/enter/",
                "params": tiny, "signed": False,
                "cookie": "", "ua": UA_PC,
            },
            {
                "url": "https://live.douyin.com/webcast/room/web/enter/",
                "params": basic, "signed": True,
                "cookie": ANON_TTWID, "ua": UA_PC,
            },
        ]

    def _cached_room(self, web_rid, live_known=False, note=""):
        """接口短暂失败时保留上一轮已知的房间资料，避免把主播从列表状态弄丢。"""
        old = dict(self._cache.get(str(web_rid)) or {})
        if old:
            old["live_known"] = live_known
            old["note"] = note or "抖音直播间接口暂时没有返回，沿用上一轮资料，稍后自动重试"
            return old
        return {"web_rid": str(web_rid), "live_known": False,
                "note": note or "抖音直播间接口暂时没有返回，稍后自动重试"}

    def _remember_room(self, web_rid, info):
        self._cache[str(web_rid)] = dict(info)

    # ---------- 主播主页 ----------

    def share_identity(self, sec_uid):
        """带缓存的分享接口资料（昵称 / 头像 / uid / 抖音号）。"""
        sec_uid = str(sec_uid or "").strip()
        if not sec_uid:
            return {}
        now = time.time()
        hit = self._identity.get(sec_uid)
        if hit and now - hit[0] < IDENTITY_CACHE_SECONDS:
            return dict(hit[1])
        info = self._share_identity(sec_uid)
        self._identity[sec_uid] = (now, dict(info))
        return info

    def uid_for(self, sec_uid):
        """sec_uid -> 数字 uid：分享接口的橱窗卡片里带 uid=（_uid_from_cards 已在解析）。

        没开橱窗的主播拿不到，返回空串，调用方继续走别的链路（拿到 web_rid 之后
        直播间接口返回的 owner.id_str 也是 uid，可以回填）。
        """
        return str(self.share_identity(sec_uid).get("userid") or "")

    def offline_info(self, sec_uid="", web_rid=""):
        """明确「未开播」的结论：不虚构流地址，只把拿得到的资料带上。"""
        info = dict(self.share_identity(sec_uid) or {})
        info.update({
            "live": False, "live_known": True,
            "web_rid": str(web_rid or info.get("web_rid") or ""),
        })
        info.pop("note", None)
        return info

    def profile(self, sec_uid, web_rid="", live_url="", hinted_web_rid="", uid=""):
        """主页 sec_uid -> 开播状态 + 流地址。

        主播之前用直播间链接加过、记录里已经有 web_rid 时，直接查直播间接口，
        不再去撞会返回空 body 的主页接口。没有 web_rid 时先用 live.douyin.com 的
        开播状态接口按 uid 判断：明确未开播就直接给结论（以前这里只能显示
        「无法确认」），在播或查不到才继续走 streamget / 主页接口这些降级链路。
        """
        for candidate in (web_rid, hinted_web_rid):
            if candidate:
                room = self.room_by_web_rid(candidate)
                if room and room.get("live_known"):
                    return room

        status = self.live_status_by_uid(uid or self.uid_for(sec_uid))
        if status is False:
            return self.offline_info(sec_uid, web_rid or hinted_web_rid)

        streamget = self._profile_by_streamget(sec_uid)
        if streamget and streamget.get("live_known"):
            return streamget

        if live_url:
            room = self._room_by_live_url(live_url)
            if room and room.get("live_known") and room.get("nickname"):
                return room

        self.bootstrap()
        params = self._profile_params(sec_uid)
        url = self._signed_url(
            "https://www.douyin.com/aweme/v1/web/user/profile/other/",
            params, page_id=6241, rp_u="", window_env=PROFILE_WINDOW_ENV, ua=UA_PC,
        )
        resp = self.session.get(
            url,
            headers={"Referer": f"https://www.douyin.com/user/{sec_uid}", "Cookie": self._cookie()},
            timeout=20,
        )
        data = None
        if resp.status_code == 200 and resp.content:
            try:
                data = resp.json()
            except ValueError:
                data = None
        user = (data or {}).get("user") or {}
        if user:
            return self._profile_result(user, sec_uid)
        if status is True:
            # 已经确认在播，只是这一轮没拿到流地址：别退回「无法确认」
            info = dict(self.share_identity(sec_uid) or {})
            info.update({
                "live": True, "live_known": True,
                "web_rid": str(web_rid or hinted_web_rid or info.get("web_rid") or ""),
            })
            return info
        info = dict(self.share_identity(sec_uid) or {})
        info.update({
            "live": False,
            "live_known": False,
            "web_rid": str(web_rid or hinted_web_rid or info.get("web_rid") or ""),
            "note": "抖音主页接口暂时被风控，未拿到开播状态，稍后自动重试",
        })
        return info

    def _profile_by_streamget(self, sec_uid):
        """调用 streamget 的 reflow 链路；只接受它明确确认的结果。

        这条链路和原版小绿点一致：手机 UA 打开主页，若在播，抖音会跳到
        ``/webcast/reflow/<room_id>?sec_user_id=...``，随后拿 room/reflow/info。
        非开播 / 验证码 / 风控都会抛异常，不能当成“未开播”。
        """
        if _StreamGetDouyin is None:
            return {}
        if time.time() < self._streamget_next.get(sec_uid, 0):
            return {}
        url = "https://www.douyin.com/user/" + sec_uid
        try:
            data = asyncio_run(
                _streamget_profile(url)
            )
        except Exception:
            self._streamget_next[sec_uid] = time.time() + STREAMGET_RETRY_SECONDS
            return {}
        if not isinstance(data, dict):
            self._streamget_next[sec_uid] = time.time() + STREAMGET_RETRY_SECONDS
            return {}
        owner = data.get("owner") or {}
        status = data.get("status")
        if status != 2:
            self._streamget_next[sec_uid] = time.time() + STREAMGET_RETRY_SECONDS
            return {}
        stream = data.get("stream_url") or {}
        info = {
            "live": True,
            "live_known": True,
            "title": data.get("title", ""),
            "user_count": parse_count(data.get("user_count_str") or data.get("user_count")),
            "nickname": owner.get("nickname", "") or owner.get("nickname") or data.get("anchor_name", ""),
            "userid": str(owner.get("id_str") or owner.get("id") or ""),
            "sec_uid": owner.get("sec_uid") or sec_uid,
            "unique_id": owner.get("unique_id", ""),
            "avatar": _first_url(owner.get("avatar_thumb")),
            "web_rid": str(owner.get("web_rid") or owner.get("webRid") or ""),
            "flv": dict(stream.get("flv_pull_url") or {}),
            "hls": dict(stream.get("hls_pull_url_map") or {}),
            "default_res": stream.get("default_resolution") or "",
        }
        live_url = data.get("live_url") or ""
        if not info["web_rid"]:
            match = LIVE_URL_RE.search(live_url)
            if match:
                info["web_rid"] = match.group(1)
        info["live_url"] = live_url
        if info["web_rid"]:
            self._remember_room(info["web_rid"], info)
        return info

    def _room_by_live_url(self, live_url):
        """分享链接里可能直接就带 live.douyin.com/<web_rid>。"""
        match = LIVE_URL_RE.search(live_url or "")
        if not match:
            return {}
        return self.room_by_web_rid(match.group(1))

    def _profile_params(self, sec_uid):
        fp = _verify_fp()
        return {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": "1",
            "cookie_enabled": "true",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "126.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "126.0.0.0",
            "os_name": "Windows",
            "os_version": "10",
            "cpu_core_num": "16",
            "device_memory": "8",
            "platform": "PC",
            "downlink": "10",
            "effective_type": "4g",
            "msToken": _mstoken(184),
            "verifyFp": fp,
            "fp": fp,
            "publish_video_strategy_type": "2",
            "source": "channel_pc_web",
            "sec_user_id": sec_uid,
            "personal_center_strategy": "1",
            "version_code": "170400",
            "version_name": "17.4.0",
            "screen_width": "1552",
            "screen_height": "970",
            "round_trip_time": "0",
            "webid": "7327957959955580467",
        }

    def _profile_result(self, user, sec_uid):
        info = {
            "nickname": user.get("nickname", ""),
            "userid": str(user.get("uid", "")),
            "unique_id": user.get("unique_id", ""),
            "sec_uid": user.get("sec_uid", sec_uid),
            "avatar": _first_url(user.get("avatar_thumb")) or _first_url(user.get("avatar_larger")),
            "follow_status": user.get("follow_status", 0),
            "live_status": user.get("live_status", 0),
            "room_id": str(user.get("room_id_str") or user.get("room_id") or "0"),
            "live_known": True,
        }
        info.update(self._from_room_data(user.get("room_data")))
        return info

    def _share_identity(self, sec_uid):
        """iesdouyin 分享接口不受主页风控影响，用来补齐昵称 / 抖音号 / uid。"""
        info = {
            "nickname": "", "userid": "", "unique_id": "", "sec_uid": sec_uid,
            "avatar": "", "live": False, "title": "", "user_count": 0,
            "web_rid": "", "flv": {}, "hls": {}, "default_res": "",
        }
        try:
            resp = self.session.get(
                "https://www.iesdouyin.com/web/api/v2/user/info/?sec_uid=" + sec_uid,
                headers={"Referer": "https://www.iesdouyin.com/", "Cookie": self._cookie()},
                timeout=20,
            )
            user = ((resp.json() if resp.status_code == 200 else {}) or {}).get("user_info") or {}
            info["nickname"] = user.get("nickname", "")
            info["unique_id"] = user.get("unique_id", "")
            info["avatar"] = _first_url(user.get("avatar_thumb")) or _first_url(user.get("avatar_medium"))
            info["userid"] = _uid_from_cards(user.get("card_entries"))
        except (requests.RequestException, ValueError):
            pass
        return info

    def _from_room_data(self, room_data):
        if isinstance(room_data, str) and room_data:
            try:
                room_data = json.loads(room_data)
            except ValueError:
                room_data = None
        if not isinstance(room_data, dict):
            return {}
        owner = room_data.get("owner") or {}
        normalized = self._normalize(room_data, owner, owner.get("web_rid", ""))
        normalized["live"] = True
        return {key: value for key, value in normalized.items() if value not in ("", None, {})}

    # ---------- 规范化 ----------

    def _owner_only(self, owner, web_rid):
        return {
            "live": False,
            "title": "",
            "user_count": 0,
            "nickname": owner.get("nickname", ""),
            "userid": str(owner.get("id_str", "")),
            "sec_uid": owner.get("sec_uid", ""),
            "unique_id": owner.get("unique_id", ""),
            "avatar": _first_url(owner.get("avatar_thumb")),
            "web_rid": str(web_rid),
            "live_known": True,
        }

    def _normalize(self, room, owner, web_rid):
        stream = room.get("stream_url") or {}
        return {
            "live": room.get("status") == 2,
            "title": room.get("title", ""),
            "user_count": parse_count(room.get("user_count_str") or room.get("user_count")),
            "nickname": owner.get("nickname", ""),
            "userid": str(owner.get("id_str", "")),
            "sec_uid": owner.get("sec_uid", ""),
            "unique_id": owner.get("unique_id", ""),
            "avatar": _first_url(owner.get("avatar_thumb")),
            "web_rid": str(owner.get("web_rid") or web_rid or ""),
            "flv": dict(stream.get("flv_pull_url") or {}),
            "hls": dict(stream.get("hls_pull_url_map") or {}),
            "default_res": stream.get("default_resolution") or "",
            "live_known": True,
        }

    # ---------- 链接解析 ----------

    def parse_input(self, line):
        line = line.strip()
        if not line:
            return None
        if BARE_RID_RE.match(line):
            return {"web_rid": line}
        if LIVE_URL_RE.search(line):
            return {"web_rid": LIVE_URL_RE.search(line).group(1)}
        sec = SEC_UID_RE.search(line)
        if sec:
            return {"sec_uid": sec.group(1)}
        if SHORT_URL_RE.search(line):
            target = self._expand_short(line)
            if target:
                return self.parse_input(target)
            return None
        return None

    def _expand_short(self, line):
        url = _grab_url(line) or (line if line.startswith("http") else "https://" + line)
        try:
            resp = self.session.get(url, timeout=20, allow_redirects=True)
        except requests.RequestException:
            return ""
        for candidate in (resp.url, resp.text[:2000]):
            match = LIVE_URL_RE.search(candidate) or SEC_UID_RE.search(candidate)
            if match:
                return match.group(0)
        return ""

    def resolve(self, parsed, hinted_web_rid=""):
        self.bootstrap()
        if "web_rid" in parsed:
            room = self.room_by_web_rid(parsed["web_rid"])
            if not room.get("sec_uid"):
                raise DouyinError("拿不到该直播间的主播资料")
            room.setdefault("live", False)
            return room
        info = self.profile(
            parsed["sec_uid"], live_url=parsed.get("live_url", ""),
            hinted_web_rid=hinted_web_rid,
        )
        return info

    # ---------- 取流 ----------

    def pick_stream_url(self, info, definition, stream_type):
        table = info.get("hls" if stream_type == "m3u8" else "flv") or {}
        if not table:
            table = info.get("flv") or info.get("hls") or {}
        for key in QUALITY_ORDER.get(definition, QUALITY_ORDER["蓝光"]):
            if table.get(key):
                return table[key]
        return next(iter(table.values()), "")


async def _streamget_profile(url):
    """在独立 event loop 中调用 streamget，避免和同步引擎共用 aiohttp 连接池。"""
    client = _StreamGetDouyin()
    return await client.fetch_app_stream_data(url)


def asyncio_run(coro):
    """兼容没有运行中的 event loop 的同步线程；有 loop 时另起线程。"""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {}
    error = {}

    def runner():
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001
            error["value"] = exc

    import threading

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=45)
    if error:
        raise error["value"]
    return result.get("value")


def _uid_from_cards(card_entries):
    for card in card_entries or []:
        url = card.get("goto_url") or ""
        match = re.search(r"uid=(\d+)", url)
        if match:
            return match.group(1)
    return ""
