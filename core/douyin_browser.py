"""用本机 WebView2 读取抖音主页里的直播间入口。

抖音的主页 JSON 接口会对部分家宽 IP 返回空响应，但正常浏览器打开主页时
仍能拿到完整页面。原版也是先在主页里找到 ``live.douyin.com/<web_rid>``，
再把 web_rid 存起来，后续检测只走稳定的直播间接口。

这里只在添加主页链接时开一个隐藏窗口；拿到结果立刻销毁。失败结果按账号
冷却一段时间，避免用户连续点添加时反复弹 WebView2 进程。
"""
import json
import re
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import APP_DIR

LIVE_URL_RE = re.compile(r"live\.douyin\.com/([0-9]{6,})")
RETRY_SECONDS = 180
_LOCK = threading.Lock()
_NEXT_TRY = {}
_CACHE = {}
_CACHE_TTL = 60
_CACHE_PATH = Path(APP_DIR) / "douyin_rooms.json"
_HARD_TIMEOUT = 24


def _load_cache():
    try:
        payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    now = time.time()
    for sec_uid, item in (payload or {}).items():
        rid = str((item or {}).get("web_rid") or "")
        saved_at = float((item or {}).get("saved_at") or 0)
        if rid and now - saved_at < 7 * 86400:
            _CACHE[sec_uid] = {"web_rid": rid, "saved_at": saved_at}


def _save_cache():
    try:
        _CACHE_PATH.write_text(
            json.dumps(_CACHE, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


_load_cache()


def cached_web_rid(sec_uid):
    with _LOCK:
        item = _CACHE.get(str(sec_uid or "")) or {}
    if not item:
        return ""
    return str(item.get("web_rid") or "")


def resolve_web_rid(sec_uid, timeout=30):
    """返回主页当前直播间的 web_rid；未开播或读取失败返回空串。"""
    sec_uid = str(sec_uid or "").strip()
    if not sec_uid:
        return ""
    with _LOCK:
        cached = _CACHE.get(sec_uid) or {}
        if cached and time.time() - float(cached.get("saved_at") or 0) < _CACHE_TTL:
            return str(cached.get("web_rid") or "")
        if time.time() < _NEXT_TRY.get(sec_uid, 0):
            return ""

    rid = _resolve_in_child(sec_uid, min(timeout + 2, _HARD_TIMEOUT))
    with _LOCK:
        if rid:
            _CACHE[sec_uid] = {"web_rid": rid, "saved_at": time.time()}
            _save_cache()
        else:
            _NEXT_TRY[sec_uid] = time.time() + RETRY_SECONDS
    return rid


def _resolve_in_child(sec_uid, timeout):
    """在主程序之外跑 WebView2，超时后只结束这个探针子进程。"""
    result_path = Path(APP_DIR) / f"douyin_probe_{os.getpid()}_{int(time.time() * 1000)}.json"
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / "app.py")]
    cmd += ["--douyin-probe", sec_uid, str(result_path), str(timeout)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        proc.wait(timeout=timeout + 4)
        if result_path.exists():
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            return str((payload or {}).get("web_rid") or "")
        return ""
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    finally:
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
