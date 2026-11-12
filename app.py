"""锋芒直播录屏：抖音 / 快手 / 小红书直播开播监听 + 自动录制（pywebview 桌面窗口）。"""
import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import webview

from core import guard, vault
from core import autostart
from core.config import APP_DIR, RES_DIR, Store, WindowState
from core.douyin import DouyinClient, DouyinError
from core.douyin_browser import resolve_web_rid
from core.engine import Engine, find_ffmpeg, log_to_file
from core.kuaishou import KuaishouClient, KuaishouError, looks_like_kuaishou
from core.streamget_platform import (
    AcfunClient,
    BilibiliClient,
    DouyuClient,
    HuyaClient,
    StreamGetError,
    looks_like_acfun,
    looks_like_bilibili,
    looks_like_douyu,
    looks_like_huya,
)
from core.extra_platform import (
    ExtraPlatformError,
    ZhanqiClient,
    looks_like_zhanqi,
)
from core.xiaohongshu import XiaohongshuClient, XiaohongshuError, looks_like_xiaohongshu

# 版本号在 core/version.py（打包脚本也从那里读，改一处就够了）
from core.version import APP_NAME, VERSION  # noqa: E402

# 千万不要用 ctypes.windll.user32：那是全进程共享的同一个 CDLL 对象，我们给它设的
# argtypes / restype 会把 pywebview 自己的调用一起改掉 —— 它给 SetWindowPos 的
# cx / cy 传的是 None（靠 SWP_NOSIZE 生效），一旦按 c_int 收了就抛 ArgumentError，
# 于是「最大化 / 还原 / 拖动」全都不动。这里用自己的句柄，参数类型只影响我们自己。
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# 只允许开一个：第二个实例看到这个互斥体存在就直接把老窗口叫出来然后退出
MUTEX_NAME = "FengmangLiveScreen.SingleInstance.v1"
# 左右两栏（主播列表 + 添加主播）比原来的单栏要宽一些
WINDOW_WIDTH = 1120
WINDOW_HEIGHT = 760
MIN_WIDTH = 1000
MIN_HEIGHT = 640

SEC_UID_HINT = re.compile(r"MS4wLjAB")


def _fatal(message):
    """启动前的硬错误：打包版没有控制台，只能弹系统对话框告诉用户。"""
    _user32.MessageBoxW(None, str(message), APP_NAME, 0x10)


def _platform_of(line):
    """按链接特征挑平台，避免短链或分享文字落到错误的平台。"""
    text = (line or "").strip().lower()
    if "douyin" in text or SEC_UID_HINT.search(line or ""):
        return "douyin"
    if looks_like_bilibili(line):
        return "bilibili"
    if looks_like_huya(line):
        return "huya"
    if looks_like_douyu(line):
        return "douyu"
    if looks_like_acfun(line):
        return "acfun"
    if looks_like_zhanqi(line):
        return "zhanqi"
    if looks_like_xiaohongshu(line):
        return "xiaohongshu"
    if looks_like_kuaishou(line):
        return "kuaishou"
    return "douyin"


def _set_clipboard(text):
    """写系统剪贴板（不依赖 tkinter，打包后也能用）。"""
    cf_unicodetext = 13
    gmem_moveable = 0x0002
    kernel32 = _kernel32
    user32 = _user32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    if not user32.OpenClipboard(None):
        return False
    handle = None
    try:
        user32.EmptyClipboard()
        data = str(text).encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(gmem_moveable, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return False
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(cf_unicodetext, handle):
            return False
        handle = None  # 所有权交给系统，不能再释放
        return True
    finally:
        if handle:
            kernel32.GlobalFree(handle)
        user32.CloseClipboard()


def _dpi_scale():
    """系统 DPI 缩放比例，150% 的屏就是 1.5。"""
    try:
        return _user32.GetDpiForSystem() / 96.0
    except AttributeError:  # 老系统没这个函数，当成 100%
        return 1.0


def _primary_screen():
    """主屏大小，换算成 pywebview 那一套逻辑像素。"""
    user32 = _user32
    scale = _dpi_scale()
    return user32.GetSystemMetrics(0) / scale, user32.GetSystemMetrics(1) / scale


def _virtual_screen():
    """所有显示器拼起来的总范围（逻辑像素），用来判断记住的位置还在不在屏幕上。"""
    user32 = _user32
    scale = _dpi_scale()
    return (
        user32.GetSystemMetrics(76) / scale,   # SM_XVIRTUALSCREEN
        user32.GetSystemMetrics(77) / scale,   # SM_YVIRTUALSCREEN
        user32.GetSystemMetrics(78) / scale,   # SM_CXVIRTUALSCREEN
        user32.GetSystemMetrics(79) / scale,   # SM_CYVIRTUALSCREEN
    )


def _fit_size(screen_w, screen_h):
    """默认窗口比屏幕还大的时候缩一缩，否则居中了也有一截露在屏幕外面。"""
    width = max(MIN_WIDTH, min(WINDOW_WIDTH, int(screen_w) - 40))
    height = max(MIN_HEIGHT, min(WINDOW_HEIGHT, int(screen_h) - 40))
    return width, height


def _centered_geometry():
    """默认位置：主屏工作区正中央（水平垂直都居中，且避开任务栏）。

    pywebview 自带的 CenterScreen 在缩放屏上算不对（它把窗口尺寸乘了 DPI 缩放，
    但 WinForms 居中时用的还是没缩放的尺寸），所以这里自己按逻辑像素算好，
    在 create_window 时就传进去 —— 窗口一出生就在正确位置，不会先出现在别处再挪过来。
    """
    left, top, right, bottom = _work_area()
    area_w, area_h = right - left, bottom - top
    width, height = _fit_size(area_w, area_h)
    return {
        "x": int(left + (area_w - width) / 2),
        "y": int(top + (area_h - height) / 2),
        "width": int(width),
        "height": int(height),
    }


WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
GWL_STYLE = -16
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
SPI_GETWORKAREA = 0x0030


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _place_window(title, geometry):
    """把窗口精确摆到 geometry 指定的位置和大小（逻辑像素，自动乘 DPI 缩放）。

    为什么不用 window.move()/resize()：pywebview 那套内部对逻辑/物理像素的换算
    在 150% 缩放的屏上对不齐（实测会偏到别处）。这里直接用 SetWindowPos 按物理像素摆，
    一次到位，配上「先隐藏再显示」就不会出现启动后跳位置。
    """
    _user32.FindWindowW.restype = ctypes.c_void_p
    _user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    hwnd = _user32.FindWindowW(None, title)
    if not hwnd:
        return False
    scale = _dpi_scale()
    x = int(round(float(geometry.get("x", 0)) * scale))
    y = int(round(float(geometry.get("y", 0)) * scale))
    w = int(round(float(geometry.get("width", WINDOW_WIDTH)) * scale))
    h = int(round(float(geometry.get("height", WINDOW_HEIGHT)) * scale))
    _user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | 0x0010)  # +SWP_NOACTIVATE
    return True


def _make_resizable(title):
    """无边框窗口默认连缩放边框都没有，给它把 WS_THICKFRAME 补回来。

    pywebview 的 frameless 走的是 FormBorderStyle.None，等于 WS_POPUP：没有标题栏，
    也没有四周的缩放边框。补上 WS_THICKFRAME 之后系统就会在边缘做命中测试（HTRIGHT /
    HTBOTTOMRIGHT 那一套），缩放和 Windows 原生的手感一致。

    这里**不动窗口尺寸**（SWP_NOSIZE）：非客户区变大、客户区相应小一圈，和以前用系统
    标题栏时客户区的实际大小基本一致；而 pywebview 记窗口位置/大小用的就是外框尺寸，
    保持外框不变，window.json 就不会每次启动都涨一圈。
    """
    user32 = _user32
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False, "找不到窗口"
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        get_long, set_long = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
    else:  # pragma: no cover - 32 位打包时才会走到
        get_long, set_long = user32.GetWindowLongW, user32.SetWindowLongW
    get_long.restype = ctypes.c_long
    get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    set_long.restype = ctypes.c_long
    set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    style = get_long(hwnd, GWL_STYLE)
    want = style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
    if want != style:
        set_long(hwnd, GWL_STYLE, want)
    old = _RECT()
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    client = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(old))
    user32.GetClientRect(hwnd, ctypes.byref(client))
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    return True, (f"style 0x{style:x}->0x{want:x} 客户区 "
                  f"{client.right - client.left}x{client.bottom - client.top}")


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19
DWMWA_BORDER_COLOR = 34
# COLORREF 是 0x00BBGGRR：深色底 #0b0e13、浅色底 #eaeef4
FRAME_COLOR = {"dark": 0x00130E0B, "light": 0x00F4EEEA}


GWLP_WNDPROC = -4
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
SM_CXSIZEFRAME = 32
SM_CXPADDEDBORDER = 92
HTCLIENT = 1
HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 13, 14, 15, 16, 17
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.c_void_p, ctypes.c_void_p)
_OLD_PROCS = {}      # hwnd -> (旧窗口过程, 回调对象)  回调必须一直活着，不然被 GC 掉窗口就崩
_NCCALCSIZE_HITS = []   # 仅用于自检：钩子被调用的次数
# 我们的最大化是按工作区自己摆的，WinForms 的 WindowState 还是 Normal，
# 所以命中测试得自己记住「现在是最大化」，不然最大化状态下还能拖边缘拉伸。
_WINDOW_STATE = {"maximized": False}


def _border_hit_test(hwnd, lparam):
    """自己算边缘命中：非客户区被去掉之后，系统不再把最外圈当成缩放边。

    lParam 里是屏幕坐标（各 16 位，带符号）。返回 0 表示「不是边缘，交给系统」。
    右上角那排窗口按钮所在的一小块要排除掉，否则关闭按钮最外几像素点不中。
    """
    _user32.IsZoomed.argtypes = [ctypes.c_void_p]
    if _WINDOW_STATE["maximized"] or _user32.IsZoomed(hwnd):
        return 0
    raw = int(lparam or 0)
    x = ctypes.c_short(raw & 0xFFFF).value
    y = ctypes.c_short((raw >> 16) & 0xFFFF).value
    rect = _RECT()
    _user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    border = (_user32.GetSystemMetrics(SM_CXSIZEFRAME)
              + _user32.GetSystemMetrics(SM_CXPADDEDBORDER)) or 8
    scale = _dpi_scale()
    # 窗口三键那一带（右上角）不算边缘，按钮才好点
    if y < rect.top + int(46 * scale) and x > rect.right - int(230 * scale):
        return 0
    left = x < rect.left + border
    right = x >= rect.right - border
    top = y < rect.top + border
    bottom = y >= rect.bottom - border
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return 0


def _strip_window_frame(title):
    """把窗口的非客户区整个去掉：系统不再画那圈边框，深色主题下就不会有白边。

    做法是挂一个自己的窗口过程，收到 WM_NCCALCSIZE（且 wparam 为真）时直接返回 0 ——
    意思是「整扇窗都算客户区」。要点：
      * WS_THICKFRAME 仍然保留，DefWindowProc 的 WM_NCHITTEST 还是会在最外圈几像素
        返回 HTLEFT / HTBOTTOMRIGHT 这些缩放码，所以缩放照旧能用；
      * 只是那圈区域不再被系统画出来，界面可以铺满整扇窗；
      * 其它消息原样转给旧窗口过程，绝不吞消息。
    """
    _user32.FindWindowW.restype = ctypes.c_void_p
    _user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    hwnd = _user32.FindWindowW(None, title)
    if not hwnd:
        return False, "找不到窗口"
    if hwnd in _OLD_PROCS:
        return True, "已经挂过"
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    _user32.CallWindowProcW.restype = ctypes.c_ssize_t
    _user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p]
    old = _user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)
    if not old:
        return False, "拿不到旧窗口过程"

    def handler(h, msg, wparam, lparam):
        try:
            if msg == WM_NCCALCSIZE and wparam:
                _NCCALCSIZE_HITS.append(1)
                return 0        # 整扇窗都当客户区：不画标题栏也不画边框
            if msg == WM_NCHITTEST:
                code = _border_hit_test(h, lparam)
                if code:
                    return code
            return _user32.CallWindowProcW(old, h, msg, wparam, lparam)
        except Exception:  # noqa: BLE001  回调里绝不能抛出去，否则窗口直接崩
            return 0

    callback = _WNDPROC(handler)
    _user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p).value)
    _OLD_PROCS[hwnd] = (old, callback)
    # 只换窗口过程还不够：客户区是算好缓存的，必须逼系统重算一次
    _user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    _user32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    _user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0400 | 0x0100 | 0x0080)  # 立即重画整窗
    return True, "已接管 WM_NCCALCSIZE"


def _apply_window_frame_theme(title, theme):
    """把窗口那圈非客户区也染成和界面一致的颜色。

    无边框窗口补了 WS_THICKFRAME 之后，四周的缩放边框是系统画的：深色主题下它会露出
    一条浅色白边。这里用 DWM 的「深色窗口」属性（Win10 2004+）把非客户区变深，
    Win11 上再直接把边框色设成界面底色。浅色主题就是默认那套，不会出现反过来的问题。
    """
    _user32.FindWindowW.restype = ctypes.c_void_p
    _user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    hwnd = _user32.FindWindowW(None, title)
    if not hwnd:
        return False, "找不到窗口"
    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError as exc:  # pragma: no cover
        return False, repr(exc)
    dark = 1 if str(theme or "dark") != "light" else 0
    flag = ctypes.c_int(dark)
    dark_ok = False
    for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY):
        if dwm.DwmSetWindowAttribute(hwnd, ctypes.c_uint(attr), ctypes.byref(flag),
                                     ctypes.sizeof(flag)) == 0:
            dark_ok = True
            break
    color = ctypes.c_uint(FRAME_COLOR["light" if not dark else "dark"])
    dwm.DwmSetWindowAttribute(hwnd, ctypes.c_uint(DWMWA_BORDER_COLOR),
                              ctypes.byref(color), ctypes.sizeof(color))
    return dark_ok, f"dark={dark}"


def _work_area():
    """主屏工作区（逻辑像素）：屏幕减去任务栏，最大化时按它摆窗口。

    无边框窗口直接用系统的最大化会盖住任务栏，所以自己算。
    """
    rect = _RECT()
    user32 = _user32
    user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                             ctypes.POINTER(_RECT), ctypes.c_uint]
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        width, height = _primary_screen()
        return 0.0, 0.0, width, height
    scale = _dpi_scale()
    return (rect.left / scale, rect.top / scale, rect.right / scale, rect.bottom / scale)


def _initial_geometry(state):
    """启动位置：**每次都落在桌面正中央**（水平垂直居中）。

    这是用户明确要的默认行为 —— 打开软件窗口就该在眼前的正中间，不会落在上次
    拖到的角落。窗口**大小**倒是沿用上次的（用户调过的大小是有意的偏好）。

    只有一种例外：用户手动拖动过窗口（window.json 里记了 moved=true），
    说明那是他自己摆好的位置，这时才还原到那里。
    """
    centered = _centered_geometry()
    saved = state.load()
    if not saved:
        return centered
    vx, vy, vw, vh = _virtual_screen()
    width = max(MIN_WIDTH, min(int(saved.get("width") or centered["width"]), int(round(vw))))
    height = max(MIN_HEIGHT, min(int(saved.get("height") or centered["height"]), int(round(vh))))
    # 沿用上次的大小，位置按「主屏工作区」重新居中（和 _centered_geometry 用同一套逻辑像素，
    # 不能拿虚拟屏幕的总宽度来居中 —— 多显示器时那会把窗口甩到两块屏的中间）。
    area_l, area_t, area_r, area_b = _work_area()
    result = dict(centered)
    result["width"] = width
    result["height"] = height
    result["x"] = int((area_l + area_r - width) / 2)
    result["y"] = int((area_t + area_b - height) / 2)
    # 用户手动拖过窗口的话，尊重他的位置（只要还在屏幕可见范围里）
    if saved.get("moved"):
        x, y = int(saved.get("x") or 0), int(saved.get("y") or 0)
        on_screen = (vx - 8 <= y <= vy + vh - 32
                     and x + width > vx + 120 and x < vx + vw - 120)
        if on_screen:
            result["x"], result["y"] = x, y
    return result


def _remember_geometry(window, state, is_maximized=None):
    """记住窗口位置和大小：拖完/缩放完停 0.8 秒落盘一次，关窗口时再落一次。

    is_maximized 是「现在是不是最大化」的判断：最大化那一下不该把铺满工作区的尺寸
    写进 window.json，否则下次启动窗口一上来就占满屏幕。
    """
    lock = threading.Lock()
    # user_moved：只在用户真的拖过/缩放过窗口时才置 True。
    # ready：启动阶段（补边框、去掉非客户区、_place_window 摆位、show）都算 False，
    #        这段时间里窗口自己产生的 moved/resized 事件一律不当成用户拖动 ——
    #        否则第一次启动就会被记成 moved=true，之后再也回不到居中。
    pending = {"timer": None, "minimized": False, "user_moved": False, "ready": False}

    def _write():
        with lock:
            pending["timer"] = None
            if pending["minimized"]:
                return
        if is_maximized is not None and is_maximized():
            return          # 最大化时的尺寸不记
        try:
            state.save(window.x, window.y, window.width, window.height,
                       moved=pending["user_moved"])
        except Exception:  # noqa: BLE001  窗口已经销毁就随它去，别卡住退出流程
            pass

    def _schedule():
        with lock:
            if pending["timer"] is not None:
                pending["timer"].cancel()
            timer = threading.Timer(0.8, _write)
            timer.daemon = True
            pending["timer"] = timer
            timer.start()

    def _mark_user():
        with lock:
            if not pending["ready"]:
                return          # 启动阶段的移动是程序自己摆的，不算用户操作
            pending["user_moved"] = True

    def _on_moved(*_args):
        _mark_user()
        _schedule()

    def _on_resized(*_args):
        _mark_user()
        _schedule()

    def _on_minimized():
        # 最小化时窗口坐标会变成 -32000 这种占位值，别记进去
        with lock:
            pending["minimized"] = True
            if pending["timer"] is not None:
                pending["timer"].cancel()
                pending["timer"] = None

    def _on_restored():
        with lock:
            pending["minimized"] = False
        _schedule()

    def _on_closing():
        with lock:
            if pending["timer"] is not None:
                pending["timer"].cancel()
                pending["timer"] = None
        _write()

    def _reset_moved():
        """程序自己摆位完成后调用：这次移动不算用户拖动。"""
        with lock:
            pending["user_moved"] = False

    def _set_ready(value=True):
        """启动流程走完（窗口已显示且摆好）之后才允许记录用户拖动。"""
        with lock:
            pending["ready"] = bool(value)
            if not value:
                pending["user_moved"] = False

    window._dsh_reset_moved = _reset_moved
    window._dsh_set_ready = _set_ready

    window.events.moved += _on_moved
    window.events.resized += _on_resized
    window.events.minimized += _on_minimized
    window.events.restored += _on_restored
    window.events.closing += _on_closing


def _claim_single_instance():
    """占一个全局互斥体，返回值第二个是「已经有程序在跑了」。

    多开会让两个进程同时轮询开播状态、同时往同一个目录里写录像，白占 CPU 和带宽。
    返回的句柄要一直留着，进程退出时系统才会把互斥体收掉。
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None, False          # 拿不到就照常启动，别把人挡在门外
    return handle, ctypes.get_last_error() == 183   # ERROR_ALREADY_EXISTS


def _focus_running_instance(title, wait=4.0):
    """把已经在跑的那个窗口叫到前台（第二个实例干的最后一件好事）。

    程序只能开一个：第二次双击时不再开新进程，而是把老窗口从最小化 / 托盘里还原出来。
    SetForegroundWindow 会被系统的前台锁定挡下来，所以用「先置顶再取消置顶」这个常规
    绕法，并且多试几次；每次都用 IsWindowVisible 确认真的显示出来了。
    """
    user32 = _user32
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    hwnd = 0
    deadline = time.time() + wait
    while time.time() < deadline:     # 老实例可能刚启动、窗口还没建出来
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        return False
    nosize_nomove = 0x0001 | 0x0002
    for _ in range(3):
        user32.ShowWindow(hwnd, 9)      # SW_RESTORE：先从最小化 / 托盘里还原
        user32.ShowWindow(hwnd, 5)      # SW_SHOW
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, nosize_nomove | 0x0040)
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, nosize_nomove)
        user32.SetForegroundWindow(hwnd)
        if user32.IsWindowVisible(hwnd):
            return True
        time.sleep(0.3)
    return bool(user32.IsWindowVisible(hwnd))


def _force_show(window, title):
    """把窗口从托盘里叫回前台。

    只调 pywebview 的 show() 不够稳：它内部是 Show() + Activate()，而 Activate()
    在窗口不属于前台进程时会被系统忽略，表现就是「点托盘只闪一下任务栏、窗口不上来」，
    偶尔还会停在最小化状态，任务栏有按钮但屏幕上看不到窗口。所以这里再补一次
    ShowWindow + SetForegroundWindow。
    """
    for attempt in range(2):
        try:
            window.restore()
            window.show()
        except Exception:  # noqa: BLE001
            pass
        user32 = _user32
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.ShowWindow(hwnd, 5)   # SW_SHOW
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = ctypes.c_bool
        user32.IsIconic.argtypes = [ctypes.c_void_p]
        nosize_nomove = 0x0001 | 0x0002
        topmost, notopmost = ctypes.c_void_p(-1), ctypes.c_void_p(-2)
        # 先顶到最前再撤掉，比直接 SetForegroundWindow 更容易成功
        user32.SetWindowPos(hwnd, topmost, 0, 0, 0, 0,
                            nosize_nomove | 0x0040)   # + SHOWWINDOW
        user32.SetWindowPos(hwnd, notopmost, 0, 0, 0, 0, nosize_nomove)
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.SetForegroundWindow(hwnd)
        if not attempt and user32.IsIconic(hwnd):
            time.sleep(0.35)     # 还缩在任务栏里就再来一遍
            continue
        return


def _setup_tray(window, api, icon_path):
    """托盘图标：菜单里能把窗口调回来，也能退出程序。

    最小化按钮走 Windows 自己的最小化（缩到任务栏）；右上角 × 的行为看设置里那个
    「关闭到托盘」：打开就收进托盘、程序继续在后台跑，没打开就正常退出。

    返回托盘图标对象（没起来就返回 None，这时 × 照常退出，免得窗口藏起来以后回不去）。
    """
    try:
        import pystray
        from PIL import Image
    except ImportError as exc:  # noqa: BLE001
        api._engine.log(f"没有托盘支持（{exc}），关闭窗口就是直接退出程序")
        return None

    def _close_to_tray():
        try:
            return bool(api._store.config.get("closeToTray"))
        except Exception:  # noqa: BLE001
            return False

    # quitting：托盘菜单点「退出」时置上。退出走的也是 window.destroy()，一样会触发
    #   closing 事件，不打个标记就会被下面那个 _on_closing 当成点 × 拦下来。
    # minimized：从任务栏最小化状态关窗口时，窗口坐标是 -32000 的占位值。
    flags = {"quitting": False, "minimized": False}

    def _show_window(_icon=None, _item=None):
        try:
            _force_show(window, f"{APP_NAME} {VERSION}")
        except Exception:  # noqa: BLE001
            pass

    def _quit(icon=None, _item=None):
        flags["quitting"] = True
        try:
            api._engine.stop()
        finally:
            if icon is not None:
                icon.stop()
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    try:
        tray = pystray.Icon(
            APP_NAME,
            Image.open(str(icon_path)),
            APP_NAME,
            pystray.Menu(
                pystray.MenuItem("显示主界面", _show_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", _quit),
            ),
        )
        tray.run_detached()
        for _ in range(80):      # 等图标真的挂到托盘上，最多 4 秒
            if tray.visible:
                break
            time.sleep(0.05)
        if not tray.visible:
            api._engine.log("托盘图标没能显示，关闭窗口就是直接退出程序")
            return None
    except Exception as exc:  # noqa: BLE001
        api._engine.log(f"托盘初始化失败：{exc!r}")
        return None

    def _on_minimized():
        flags["minimized"] = True

    def _on_restored():
        flags["minimized"] = False

    def _on_closing():
        """拦住关闭就是收进托盘；返回 True 才是让窗口真关掉。"""
        if flags["quitting"] or not _close_to_tray():
            return True
        if _user32.GetSystemMetrics(0x2000):   # SM_SHUTTINGDOWN
            return True       # 系统正在关机，这时候不能拦
        if flags["minimized"]:
            # 最小化状态下坐标是 -32000 占位值，直接 Hide() 再 Show() 会变成
            # 「任务栏有按钮、屏幕上看不到窗口」，先还原到最小化前的位置再藏。
            try:
                window.restore()
            except Exception:  # noqa: BLE001
                pass
        window.hide()
        return False

    window.events.minimized += _on_minimized
    window.events.restored += _on_restored
    window.events.closing += _on_closing
    return tray


class Api:
    # 注意：js_api 对象上所有公开属性都会被 pywebview 递归遍历一遍
    # （它要枚举出能暴露给 JS 的方法）。一旦它顺着 self.window 摸到
    # window.native 里的 WebView2 COM 对象，就会在非 UI 线程上疯狂抛异常，
    # 每次都拖住整个界面。所以这些成员一律用下划线前缀，让它跳过。
    def __init__(self):
        self._store = Store()
        self._engine = Engine(self._store)
        self._client = DouyinClient()
        self._ks = KuaishouClient()
        self._xhs = XiaohongshuClient()
        self._streamget_clients = {
            "bilibili": BilibiliClient(),
            "huya": HuyaClient(),
            "douyu": DouyuClient(),
            "acfun": AcfunClient(),
        }
        # 从 bililive-go 提炼过来的补充平台（目前只有战旗的接口还活着）
        self._extra_clients = {
            "zhanqi": ZhanqiClient(),
        }
        self._window = None
        self._tray = None
        self._maximized = False      # 自绘标题栏自己记最大化状态
        self._restore_box = None     # 最大化之前的窗口框
        self._ks_hint_shown = False
        self._xhs_hint_shown = False
        self._dialog_lock = threading.Lock()

    # ---------- 状态 ----------

    def get_state(self):
        state = self._engine.snapshot()
        state["version"] = VERSION
        return state

    def get_logs(self):
        return list(self._engine.logs)[-200:]

    # ---------- 窗口（无边框之后标题栏是自己画的） ----------

    def win_minimize(self):
        try:
            self._window.minimize()
            return "minimized"
        except Exception:  # noqa: BLE001
            return ""

    def win_toggle_max(self):
        """最大化 / 还原，返回切换后的状态字符串。

        无边框窗口用系统的最大化会盖住任务栏，所以这里按工作区自己摆；还原时用切走
        之前的窗口框，一点不差地放回去。
        """
        window = self._window
        if window is None:
            return "normal"
        try:
            if self._maximized:
                box = self._restore_box or {}
                if box.get("width"):
                    window.move(int(box["x"]), int(box["y"]))
                    window.resize(int(box["width"]), int(box["height"]))
                self._maximized = False
                _WINDOW_STATE["maximized"] = False
                return "normal"
            self._restore_box = {
                "x": window.x, "y": window.y,
                "width": window.width, "height": window.height,
            }
            left, top, right, bottom = _work_area()
            window.move(int(left), int(top))
            window.resize(int(right - left), int(bottom - top))
            self._maximized = True
            _WINDOW_STATE["maximized"] = True
            return "maximized"
        except Exception as exc:  # noqa: BLE001
            self._engine.log(f"最大化 / 还原失败：{exc!r}")
            return "maximized" if self._maximized else "normal"

    def win_state(self):
        try:
            return "maximized" if self._maximized else (self._window.state or "normal")
        except Exception:  # noqa: BLE001
            return "maximized" if self._maximized else "normal"

    def win_close(self):
        """自绘标题栏的 ×：走的还是原来那套「关闭到托盘 / 直接退出」。"""
        try:
            self._window.destroy()
        except Exception:  # noqa: BLE001
            pass
        return True

    # ---------- 设置 ----------

    def apply_settings(self, incoming):
        config = self._store.apply_config(incoming or {})
        if incoming and "theme" in incoming:
            # 主题一变，窗口边框（非客户区）也跟着换，免得深色下留一圈白边
            _apply_window_frame_theme(f"{APP_NAME} {VERSION}", config.get("theme"))
        if incoming and "autoStart" in incoming:
            # 开关一变就立刻写/删注册表自启项，不等下次启动
            ok, detail = autostart.apply(bool(config.get("autoStart")))
            if ok:
                self._engine.log("开机启动已" + ("打开" if config.get("autoStart") else "关闭"))
            else:
                self._engine.log(f"开机启动设置失败：{detail}")
                # 写注册表失败时把开关回滚，免得界面显示开着、实际没生效
                config["autoStart"] = not bool(config.get("autoStart"))
                self._store.config["autoStart"] = config["autoStart"]
                self._store.save()
        self._engine.log("设置已应用")
        return config

    def pick_folder(self, current=""):
        """选目录：用 pywebview 自己的原生对话框，避免在子线程里开 tkinter 窗口。"""
        with self._dialog_lock:
            try:
                chosen = self._window.create_file_dialog(
                    webview.FOLDER_DIALOG,
                    directory=current or self._store.config["save"],
                )
            except Exception:  # noqa: BLE001
                return ""
        if not chosen:
            return ""
        if isinstance(chosen, (list, tuple)):
            return str(chosen[0]) if chosen else ""
        return str(chosen)

    # ---------- 主播列表 ----------

    def add_users(self, text):
        added, updated, failed = [], [], []
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        for line in lines:
            try:
                # 先做一次「不看网络」的查重：链接里能直接认出 sec_uid / 直播间号的，
                # 和列表里已有的对得上就直接回「已经加过」，不再走解析（解析要开隐藏浏览器，
                # 网络慢的时候能拖到十几二十秒，用户会以为卡死了）。
                hit = self._quick_duplicate(line)
                if hit:
                    failed.append({"line": line, "reason": f"已经在列表里了：{hit}", "dup": True})
                    continue
                record = self._resolve_line(line)
                if record is None:
                    failed.append({"line": line, "reason": "不认识的链接格式"})
                    continue
                if self._store.add_user(record):
                    added.append(record["nickname"])
                    self._engine.log(f"已添加主播：{record['nickname']}")
                else:
                    same = next((u for u in self._store.users
                                 if Store.same_user(u, record)), None)
                    if self._fill_share_link(same, record):
                        updated.append(record["nickname"])
                        continue
                    name = (same or {}).get("nickname") or record.get("nickname") or line
                    failed.append({"line": line, "reason": f"已经在列表里了：{name}"})
            except (DouyinError, KuaishouError, XiaohongshuError, StreamGetError,
                    ValueError, OSError) as exc:
                failed.append({"line": line, "reason": str(exc)})
        # 去重提示只保留一句（同一行粘多次时不要刷屏）
        seen = set()
        deduped = []
        for item in failed:
            key = (item.get("reason"), item.get("line"))
            if item.get("dup") and key[0] in seen:
                continue
            if item.get("dup"):
                seen.add(key[0])
            deduped.append(item)
        return {"added": added, "updated": updated, "failed": deduped,
                "count": len(self._store.users)}

    # 每个平台「从链接里直接认出房间/账号标识」的规则。认得出来就能在不联网的情况下查重，
    # 已存在的账号不用再走一遍解析（解析要开浏览器/发请求，慢的时候十几秒）。
    _LINK_KEYS = {
        "douyin": (
            (r"MS4wLjAB[A-Za-z0-9_\-]+", "sec_uid"),
            (r"live\.douyin\.com/(\d{6,})", "web_rid"),
            (r"/webcast/(?:reflow/)?(\d{6,})", "web_rid"),
        ),
        "zhanqi": ((r"zhanqi\.tv/([A-Za-z0-9_\-]+)", "web_rid"),),
        "kuaishou": ((r"live\.kuaishou\.com/u/([A-Za-z0-9_\-]+)", "web_rid"),),
        "xiaohongshu": ((r"xiaohongshu\.com/livestream/(\d+)", "web_rid"),),
        "bilibili": ((r"live\.bilibili\.com/([A-Za-z0-9_]+)", "web_rid"),),
        "huya": ((r"huya\.com/([A-Za-z0-9_]+)", "web_rid"),),
        "douyu": ((r"douyu\.com/([A-Za-z0-9_]+)", "web_rid"),),
        "acfun": ((r"acfun\.cn/live/(\d+)", "web_rid"),),
    }

    def _quick_duplicate(self, line):
        """不联网，只从链接本身看这条是不是已经在列表里了。

        按平台各自的链接特征抠出账号/房间标识，再去列表里比对同平台的记录。
        认得出来就直接返回那位主播的显示名，认不出来返回空串（交给完整解析）。
        """
        text = line or ""
        platform = _platform_of(text)
        matched = {}
        for pattern, field in self._LINK_KEYS.get(platform, ()):
            found = re.search(pattern, text, re.I)
            if found and field not in matched:
                matched[field] = found.group(1)
        if not matched:
            return ""
        for user in self._store.users:
            if str(user.get("type") or "douyin") != platform:
                continue
            for field, value in matched.items():
                kept = str(user.get(field) or "")
                if kept and kept.lower() == value.lower():
                    return user.get("nickname") or user.get("unique_id") or value
        return ""

    def _fill_share_link(self, existing, record):
        """同一位主播再粘一条 APP 分享链接时，把之前缺的分享链接补上。

        快手号 / PC 链接加进来的记录没有分享链接，只能走快手接口（按 IP 限流）；
        补上分享链接之后检测改走分享页 SSR，就不吃限流了。所以这不是重复添加，
        是把这条记录修好。
        """
        share_url = str((record or {}).get("share_url") or "")
        if not existing or not share_url or existing.get("share_url"):
            return False
        if str(existing.get("type") or "") != str(record.get("type") or ""):
            return False
        fields = {"share_url": share_url}
        # 拿快手号加的那条，昵称就是快手号本身；这回解析出真名了就顺手换掉
        if record.get("nickname") and existing.get("nickname") in ("", existing.get("unique_id")):
            fields["nickname"] = record["nickname"]
        if record.get("avatar") and not existing.get("avatar"):
            fields["avatar"] = record["avatar"]
        self._store.update_user(existing["userid"], **fields)
        self._engine.log(f"已给主播补上分享链接："
                         f"{fields.get('nickname') or existing.get('nickname')}（{share_url}）")
        return True

    def _resolve_line(self, line):
        """把一行链接解析成主播记录；七个平台按链接特征分派。"""
        platform = _platform_of(line)
        if platform in self._extra_clients:
            client = self._extra_clients[platform]
            parsed = client.parse_input(line)
            if not parsed:
                return None
            info = client.resolve(parsed)
            return self._extra_record(platform, info)
        if platform in self._streamget_clients:
            parsed = self._streamget_clients[platform].parse_input(line)
            if not parsed:
                return None
            return self._streamget_record(platform, parsed)
        if platform == "xiaohongshu":
            parsed = self._xhs.parse_input(line)
            if not parsed:
                return None
            return self._xiaohongshu_record(parsed)
        if platform == "kuaishou":
            parsed = self._ks.parse_input(line) or self._client.parse_input(line)
            if not parsed:
                return None
            if "principal_id" in parsed:
                return self._kuaishou_record(parsed)
            return self._douyin_record(parsed, line)
        parsed = self._client.parse_input(line) or self._ks.parse_input(line)
        if not parsed:
            return None
        if "principal_id" in parsed:
            return self._kuaishou_record(parsed)
        return self._douyin_record(parsed, line)

    def _streamget_record(self, platform, parsed):
        client = self._streamget_clients[platform]
        info = client.resolve(parsed)
        room_id = str(info.get("web_rid") or info.get("userid") or parsed.get("room_id") or "")
        if not room_id:
            raise StreamGetError("没解析出直播间 ID")
        return {
            "type": platform,
            "nickname": info.get("nickname") or f"{platform}{room_id[-6:]}",
            "userid": room_id,
            "sec_uid": "",
            "web_rid": room_id,
            "unique_id": str(info.get("unique_id") or room_id),
            "avatar": info.get("avatar", ""),
            "disable": False,
            "mode": "video",
        }

    def _extra_record(self, platform, info):
        """补充平台（战旗等）解析成主播记录。"""
        room = str((info or {}).get("web_rid") or (info or {}).get("room_id") or "")
        nickname = str((info or {}).get("nickname") or "").strip()
        if not room:
            raise ValueError("没解析到直播间号")
        return {
            "type": platform,
            "nickname": nickname or f"{platform}主播",
            "userid": room,
            "sec_uid": "",
            "web_rid": room,
            "unique_id": "",
            "avatar": (info or {}).get("avatar") or "",
            "disable": False,
            "mode": "video",
        }

    def _douyin_record(self, parsed, original=""):
        client = self._client
        client.bootstrap()
        sec_uid = str(parsed.get("sec_uid") or "")
        hinted_rid = ""
        if sec_uid and "web_rid" not in parsed:
            # 主页链接本身不带 web_rid。先用 uid 问一次开播状态：明确未开播就不用开隐藏
            # 浏览器（原来要等 20 多秒）；在播或查不到时才去主页找“直播中”入口。
            if client.live_status_by_uid(client.uid_for(sec_uid)) is not False:
                # 只有「正在直播」的主播才需要开浏览器找直播间号，这一步慢（几秒到十几秒），
                # 先把超时压到 12 秒——超时也不影响添加，记录里先不带 web_rid，
                # 后面检测到开播时会自动补上。
                hinted_rid = resolve_web_rid(sec_uid, timeout=12)
        info = client.resolve(parsed, hinted_web_rid=hinted_rid)
        record = {
            "type": "douyin",
            "nickname": info.get("nickname") or "未命名主播",
            "userid": str(info.get("userid") or info.get("sec_uid") or parsed.get("web_rid", "")),
            "sec_uid": info.get("sec_uid", "") or parsed.get("sec_uid", ""),
            "web_rid": str(info.get("web_rid") or hinted_rid or parsed.get("web_rid", "")),
            "unique_id": info.get("unique_id", ""),
            "avatar": info.get("avatar", ""),
            "disable": False,
            "mode": "video",
        }
        if not record["userid"]:
            raise ValueError("没解析到主播 ID")
        return record

    def _kuaishou_record(self, parsed):
        pid = str(parsed.get("principal_id") or "")
        info = self._ks.resolve(parsed)
        kwai = str(info.get("unique_id") or pid or "")
        if not kwai:
            raise ValueError("没解析出快手号，换个链接再试试")
        # 快手没开播时拿不到昵称，先用快手号顶上，开播后检测会自动补上
        record = {
            "type": "kuaishou",
            "nickname": info.get("nickname") or kwai,
            "userid": str(info.get("userid") or kwai),
            "sec_uid": "",
            "web_rid": "",
            "unique_id": kwai,
            "avatar": info.get("avatar", ""),
            "disable": False,
            "mode": "video",
            # 分享短链要存下来：每轮检测都重新打开它拿开播状态和清晰度地址
            "share_url": parsed.get("share_url", ""),
        }
        if not record["share_url"]:
            self._hint_share_link()
        return record

    def _hint_share_link(self):
        """PC 链接在部分网络上会被快手限流，第一次遇到时提醒一下用分享链接。"""
        if self._ks_hint_shown:
            return
        self._ks_hint_shown = True
        self._engine.log("提示：快手优先粘贴 APP 里的分享链接（v.kuaishou.com/...），"
                         "分享页能直接读出开播状态和清晰度；PC 链接在本机会被快手限流，检测可能不准。")

    def _xiaohongshu_record(self, parsed):
        info = self._xhs.resolve(parsed)
        room_id = str(info.get("web_rid") or "")
        if not room_id:
            raise XiaohongshuError("没解析出小红书直播间 ID，请粘贴直播间链接或 APP 分享链接")
        record = {
            "type": "xiaohongshu",
            "nickname": info.get("nickname") or f"小红书{room_id[-6:]}",
            "userid": room_id,
            "sec_uid": "",
            "web_rid": room_id,
            "unique_id": room_id,
            "avatar": info.get("avatar", ""),
            "disable": False,
            "mode": "video",
            # 分享链接要存下来：房间号失效后还能靠它重新找回落地的直播页
            "share_url": parsed.get("share_url", ""),
        }
        self._hint_xhs_link()
        return record

    def _hint_xhs_link(self):
        """小红书只能按直播间链接跟，第一次添加时说清楚，免得拿主页链接反复试。"""
        if self._xhs_hint_shown:
            return
        self._xhs_hint_shown = True
        self._engine.log("提示：小红书按「直播间」跟。APP 里进直播间「分享 → 复制链接」，"
                         "或者直接粘直播间地址（xiaohongshu.com/livestream/...）都行。"
                         "小红书主页链接读不到开播状态（官方要登录），所以主页链接加不进来；"
                         "另外房间号只管这一场，主播下播后原房间就失效了，他下次开播要重新粘一条新链接。")

    def remove_user(self, userid):
        self._engine.stop_user(userid)
        return self._store.remove_user(userid)

    def set_auto(self, userid, enabled):
        user = self._store.update_user(userid, disable=not enabled)
        return bool(user)

    def set_mode(self, userid, mode):
        if mode not in ("video", "audio", "vr"):
            return False
        self._store.update_user(userid, mode=mode)
        self._engine.log(f"录制方式：{ {'video': '默认录制视频', 'audio': '仅录制音频', 'vr': '录制VR直播'}[mode] }")
        return True

    def set_remark(self, userid, text=""):
        """给主播写备注：列表优先显示备注，长昵称就不用占满一行。

        传空串表示清掉备注，回到显示昵称。
        """
        user = self._store.update_user(userid, remark=str(text or "").strip())
        return bool(user)

    def check_now(self, userid):
        return self._engine.check_now(userid)

    def stop_recording(self, userid):
        return self._engine.stop_user_recording(userid)

    def resume_recording(self, userid):
        return self._engine.resume_user_recording(userid)

    # ---------- 录制控制 ----------

    def toggle_run(self):
        if self._engine.running:
            self._engine.stop()
        else:
            if not self._engine.ffmpeg:
                return {"running": False, "error": "没有找到 ffmpeg.exe"}
            self._engine.start()
        return {"running": self._engine.running, "ffmpeg": self._engine.ffmpeg}

    def open_home(self, userid):
        user = self._store.find(userid) or {}
        if user.get("type") in self._streamget_clients:
            url = self._streamget_clients[user["type"]].home_url(user)
            if url:
                webbrowser.open(url)
            return url
        if user.get("type") == "kuaishou":
            url = self._ks.home_url(user)
            if url:
                webbrowser.open(url)
            return url
        if user.get("type") == "xiaohongshu":
            url = self._xhs.home_url(user)
            if url:
                webbrowser.open(url)
            return url
        if user.get("web_rid"):
            url = f"https://live.douyin.com/{user['web_rid']}"
        elif user.get("sec_uid"):
            url = f"https://www.douyin.com/user/{user['sec_uid']}"
        else:
            return ""
        webbrowser.open(url)
        return url

    def copy_stream(self, userid):
        try:
            url = self._engine.stream_url_of(userid)
        except (DouyinError, KuaishouError, XiaohongshuError, StreamGetError,
                ValueError, OSError) as exc:
            return {"url": "", "error": str(exc)}
        if url:
            self._set_clipboard(url)
        return {"url": url, "error": "" if url else "当前没有直播源（未开播？）"}

    def open_dir(self, userid):
        return self._engine.open_dir(userid)

    def open_save_dir(self):
        folder = Path(self._store.config["save"])
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # noqa: S606
        return str(folder)

    def open_recording(self, userid):
        folder = Path(self._engine.open_dir(userid))
        return str(folder)

    def _set_clipboard(self, text):
        return _set_clipboard(text)

    # ---------- 视频转码 ----------

    def list_recordings(self, userid=None):
        root = Path(self._store.config["save"])
        files = []
        if root.exists():
            for path in sorted(root.rglob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                if path.is_file() and path.suffix.lower() in (".ts", ".flv"):
                    files.append({"path": str(path), "name": path.name,
                                  "folder": path.parent.name,
                                  "size": path.stat().st_size,
                                  "mtime": int(path.stat().st_mtime)})
                if len(files) >= 200:
                    break
        return files

    def transcode(self, source):
        source_path = Path(source)
        if not source_path.exists():
            return {"ok": False, "error": "文件不存在"}
        if not self._engine.ffmpeg:
            return {"ok": False, "error": "没有找到 ffmpeg.exe"}
        target = source_path.with_suffix(".mp4")
        if target.exists():
            target = source_path.with_name(source_path.stem + "_转码.mp4")
        cmd = [self._engine.ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
               "-i", str(source_path), "-c", "copy", "-bsf:a", "aac_adtstoasc", str(target)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not target.exists():
            plain = [self._engine.ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
                     "-i", str(source_path), "-c", "copy", str(target)]
            proc = subprocess.run(plain, capture_output=True, text=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if target.exists():
            self._engine.log(f"转码完成：{target.name}")
            return {"ok": True, "target": str(target)}
        return {"ok": False, "error": (proc.stderr or "转码失败")[-400:]}

    def open_readme(self):
        readme = APP_DIR / "README.md"
        if readme.exists():
            os.startfile(readme)  # noqa: S606
            return str(readme)
        return ""

    def reveal(self, path):
        target = Path(path)
        if target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)])  # noqa: S607
        return True


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--douyin-probe":
        from tools.douyin_probe import main as probe_main
        return probe_main(sys.argv[2:])
    if getattr(sys, "frozen", False):
        # 打包版：先对一遍完整性。动过任何一个文件就别想开起来。
        ok, reason = guard.verify(RES_DIR, Path(sys.executable))
        if not ok:
            _fatal("程序文件被修改过，无法启动。\n\n" + reason
                   + "\n\n请从原始压缩包重新解压一份。")
            return 3
    mutex, already_running = _claim_single_instance()
    if already_running:
        # 只能开一个主程序：已经开着了就把老窗口叫到前台，自己别再开第二个
        shown = _focus_running_instance(f"{APP_NAME} {VERSION}")
        log_to_file(time.strftime("%Y-%m-%d %H:%M:%S")
                    + " 程序已经在运行，第二个实例退出（窗口"
                    + ("已" if shown else "未能") + "叫到前台）")
        return 0
    try:
        ui_dir = vault.unpack_ui(RES_DIR / "ui.bin")
    except Exception as exc:  # noqa: BLE001
        _fatal("界面资源损坏或被修改，无法启动。\n\n" + str(exc)
               + "\n\n请从原始压缩包重新解压一份。")
        return 3
    api = Api()
    state = WindowState()
    ui = ui_dir / "index.html"
    icon = ui_dir / "app.ico"
    # 有上次的位置就还原，没有就居中（自己算好工作区中心，在 create_window 时一次传进去）
    geometry = {"width": WINDOW_WIDTH, "height": WINDOW_HEIGHT}
    geometry.update(_initial_geometry(state))
    window = webview.create_window(
        f"{APP_NAME} {VERSION}",
        url=str(ui),
        js_api=api,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        # 去掉系统标题栏，标题栏由界面自己画（.pywebview-drag-region 负责拖动窗口）；
        # easy_drag 关掉：不然整窗都能拖，选文字、点按钮都会被带跑。
        frameless=True,
        easy_drag=False,
        background_color="#0e141b",
        # 先在后台把窗口建好：补缩放边框、去非客户区、套主题这些动作都会触发窗口重排，
        # 如果这时窗口已经显示，用户就会看到它「先出来、再闪一下跳到中间」。
        # 所以先隐藏，等这些东西全部就位后再显示，用户看到的第一帧就是最终位置。
        hidden=True,
        **geometry,
    )
    api._window = window
    _remember_geometry(window, state, lambda: api._maximized)

    def _on_loaded():
        # 无边框窗口默认没有四周的缩放边框，界面出来后补上（不改窗口尺寸）
        ok, detail = _make_resizable(f"{APP_NAME} {VERSION}")
        if not ok:
            api._engine.log(f"窗口缩放边框没能生效：{detail}")
        # 补了缩放边框之后，那圈边框是系统画的（深色下看着就是白边），把非客户区整个去掉
        ok2, detail2 = _strip_window_frame(f"{APP_NAME} {VERSION}")
        if not ok2:
            api._engine.log(f"窗口边框没能去掉：{detail2}")
        # 主题在页面一加载就贴上去，避免先闪一下另一套配色（后面每轮 refresh 还会再同步）
        try:
            theme = api._store.config.get("theme") or "dark"
            window.evaluate_js(f"document.documentElement.dataset.theme = '{theme}'")
            # 边框也要跟着主题走，不然深色下那圈缩放边框是浅色的（看着就是一条白边）
            _apply_window_frame_theme(f"{APP_NAME} {VERSION}", theme)
        except Exception:  # noqa: BLE001
            pass
        # 上面几步都可能让系统重排窗口。这里再按目标位置摆一次（窗口此前是隐藏的，
        # 所以这一下调整用户看不见），然后才显示 —— 保证第一帧就在正确位置。
        _place_window(f"{APP_NAME} {VERSION}", geometry)
        # 上面这次摆位会触发 moved/resized 事件，那是程序自己干的、不是用户拖的，
        # 把标记清掉，免得把「居中位置」当成用户偏好记下来。
        try:
            window._dsh_reset_moved()
        except Exception:  # noqa: BLE001
            pass
        try:
            window.show()
        except Exception:  # noqa: BLE001
            pass
        # 到这里启动流程结束，之后再来的 moved/resized 才是用户真的在拖窗口。
        try:
            window._dsh_set_ready(True)
        except Exception:  # noqa: BLE001
            pass
        # 静默启动：开机自启时不想每回都弹个窗口，直接收进托盘后台跑
        if api._store.config.get("silentStart") and api._store.config.get("closeToTray"):
            try:
                window.hide()
                api._engine.log("静默启动：已收进托盘后台检测")
            except Exception:  # noqa: BLE001
                pass

    window.events.loaded += _on_loaded
    api._tray = _setup_tray(window, api, ui_dir / "app.png")
    if not find_ffmpeg():
        api._engine.log("没有找到 ffmpeg.exe（请把它放到程序目录）")
    api._engine.log(f"{APP_NAME} {VERSION} 启动")
    # 开了开机启动就把注册表里的命令按当前路径校正一遍（程序可能被挪过位置）
    if api._store.config.get("autoStart"):
        try:
            autostart.ensure_synced(True)
        except Exception:  # noqa: BLE001
            pass
    if api._store.config.get("autoCheck", True):
        # 启动自动检测：开软件就开始轮询，不用手点「检测录制」（设置里可以关掉）
        api._engine.start()
    webview.start(icon=str(icon) if icon.exists() else None)
    if api._tray is not None:
        try:
            api._tray.stop()
            time.sleep(0.3)   # 等托盘线程把图标从通知区摘掉，免得留个点不动的残影
        except Exception:  # noqa: BLE001
            pass
    api._engine.stop()
    del mutex   # 进程要退了，互斥体跟着这个句柄一起还给系统
    shutil.rmtree(ui_dir, ignore_errors=True)   # 临时解出来的界面资源收掉


if __name__ == "__main__":
    sys.exit(main())
