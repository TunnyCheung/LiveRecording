"""配置读写：字段与小绿点 config.json 保持一致，便于沿用同一套习惯。"""
import json
import sys
from pathlib import Path


def _base_dir():
    """数据目录：打包后是 exe 所在目录，开发时是项目根目录。

    config.json / 录像 / kaibo.mp3 这些都放这里，打包后不会跟着临时解包目录一起消失。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _res_dir():
    """随程序发布的资源目录（ui/ 等）：onefile 打包时在临时解包目录里。"""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else _base_dir()


APP_DIR = _base_dir()
RES_DIR = _res_dir()
CONFIG_PATH = APP_DIR / "config.json"
WINDOW_PATH = APP_DIR / "window.json"

DEFAULT_CONFIG = {
    "intervalTime": 15,          # 检测频率（秒）
    "definition": "蓝光",         # 清晰度：原画/蓝光/高清/标清/流畅
    "streamType": "flv",         # 直播源：flv / m3u8
    "outFormat": "ts",           # 录制格式：ts / mp4 / flv
    "checkPK": False,            # 自动分段（PK 切换时另起一段）
    "fileSize": 0,               # 限制大小（MB，0=不限制）
    "fileTime": 0,               # 限制时长（分钟，0=不限制）
    "recTime": 0,                # 切片时长（分钟，0=不限制）
    "recFileSize": 0,            # 大小分段（MB，0=不分段）
    "save": str(APP_DIR / "录像"),
    "mp3play": False,            # 开播提示（播放 kaibo.mp3）
    "hideTime": False,           # 隐藏时长（录制中不显示已录制时长）
    "hideFileSize": False,       # 隐藏大小（录制中不显示文件大小）
    "showUserCount": False,      # 在线人数（显示并写入 txt 记录）
    "closeToTray": True,         # 点右上角 × 收进托盘（不退出程序）——默认打开
    "autoCheck": True,           # 启动自动检测：开软件就自动开始检测录制
    "autoStart": False,          # 开机启动：登录系统就自动打开本软件
    "silentStart": False,        # 静默启动：启动时直接最小化到托盘，不弹窗
    "theme": "dark",             # 界面主题：dark 深色 / light 浅色
}


def _clean_user(raw):
    return {
        "type": raw.get("type", "douyin"),
        "nickname": raw.get("nickname", ""),
        "userid": str(raw.get("userid", "")),
        "sec_uid": raw.get("sec_uid", ""),
        "web_rid": str(raw.get("web_rid", "")),
        "unique_id": raw.get("unique_id", ""),
        "avatar": raw.get("avatar", ""),
        "disable": bool(raw.get("disable", False)),
        "mode": raw.get("mode", "video"),
        # 自定义备注：列表优先显示它，长昵称就不用在那一行里挤成一团
        "remark": str(raw.get("remark", "") or ""),
        # 快手分享短链：检测时每轮都重新打开它，拿开播状态和清晰度地址
        "share_url": raw.get("share_url", ""),
    }


class Store:
    def __init__(self, path=CONFIG_PATH):
        self.path = Path(path)
        self.config = dict(DEFAULT_CONFIG)
        self.users = []
        self.load()

    def load(self):
        if not self.path.exists():
            self.save()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        cfg = data.get("config") or {}
        for key, default in DEFAULT_CONFIG.items():
            value = cfg.get(key, default)
            self.config[key] = value
        if self.config.get("theme") not in ("dark", "light"):
            self.config["theme"] = DEFAULT_CONFIG["theme"]
        self.users = [_clean_user(u) for u in (data.get("user") or [])]

    def save(self):
        payload = {"config": self.config, "user": self.users}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent="\t"),
            encoding="utf-8",
        )

    def apply_config(self, incoming):
        for key, default in DEFAULT_CONFIG.items():
            if key not in incoming:
                continue
            value = incoming[key]
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int) and not isinstance(default, bool):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = default
            self.config[key] = value
        if self.config.get("theme") not in ("dark", "light"):
            self.config["theme"] = DEFAULT_CONFIG["theme"]   # 只认这两个值，别让它写坏
        self.save()
        return self.config

    def add_user(self, user):
        """同一个主播只留一条：链接换个写法、平台链接混着粘都不该重复添加。

        抖音有 sec_uid / web_rid，快手只有快手号和数字 user_id，所以这里把身份字段
        挨个比一遍，任意一个对上就算已经在列表里了。只有同一个平台才比：斗鱼、虎牙、
        B站等平台的房间号可能撞数字，跨平台不能当成同一个主播。
        """
        for existing in self.users:
            if self.same_user(existing, user):
                return False
        self.users.append(_clean_user(user))
        self.save()
        return True

    @staticmethod
    def same_user(left, right):
        left_type = str((left or {}).get("type") or "douyin")
        right_type = str((right or {}).get("type") or "douyin")
        if left_type != right_type:
            return False
        for key in ("userid", "sec_uid", "web_rid", "unique_id", "share_url"):
            mine = str((left or {}).get(key) or "").strip()
            yours = str((right or {}).get(key) or "").strip()
            if mine and mine == yours:
                return True
        return False

    def remove_user(self, userid):
        before = len(self.users)
        self.users = [u for u in self.users if u["userid"] != str(userid)]
        if len(self.users) != before:
            self.save()
            return True
        return False

    def find(self, user_id):
        for user in self.users:
            if user["userid"] == str(user_id):
                return user
        return None

    def update_user(self, user_id, **fields):
        user = self.find(user_id)
        if not user:
            return None
        user.update(fields)
        self.save()
        return user


class WindowState:
    """窗口位置记忆。

    单独存 window.json，不去动 config.json：那边是和小绿点对齐的字段，
    多塞私货会破坏「配置能互相对照」这一点。
    """

    def __init__(self, path=WINDOW_PATH):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            result = {key: int(data[key]) for key in ("x", "y", "width", "height")}
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        # moved=true 表示用户手动拖过/缩放过窗口，位置才值得还原；
        # 没这个字段（老版本写的）一律当作「没拖过」，启动时居中。
        result["moved"] = bool(data.get("moved"))
        return result

    def save(self, x, y, width, height, moved=False):
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            return
        payload = {"x": int(x), "y": int(y), "width": width, "height": height}
        if moved:
            payload["moved"] = True
        self.path.write_text(json.dumps(payload), encoding="utf-8")
