"""检查界面文案：顶栏说明的位置、使用说明里那行微信，并且这些内容是从加密的
ui.bin 里解出来的（也就是用户装到手上那份）。

用法: python ui_text_test.py
"""
import ctypes
import shutil
import sys
import time
from ctypes import wintypes
from pathlib import Path

import webview
from PIL import ImageGrab

APP = Path(__file__).resolve().parents[1]
WORK = Path(__file__).resolve().parent / "_scratch"
WORK.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(APP))
from core.version import APP_NAME, VERSION  # 版本号唯一来源（打包脚本同一份）

from core import vault  # noqa: E402

TITLE = f"{APP_NAME} {VERSION}"
NOTE = "锋芒直播录屏，永久免费给同行的录屏工具。"
NOTE_SHORT = "一款专业的直播录制软件"   # 顶栏文案（2026-10 更新）

user32 = ctypes.windll.user32
ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append((name, ok, got, want))
    print(f"{'OK ' if ok else 'BAD'} {name}: got={got!r} want={want!r}", flush=True)


def shot(png):
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        return
    user32.SetWindowPos(hwnd, wintypes.HWND(-2), 20, 20, 0, 0, 0x0001 | 0x0040)
    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002)
    time.sleep(0.8)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom),
                   all_screens=True).save(png)
    user32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, 0x0001 | 0x0002)
    print("截图:", png)


def driver(window, api, ui_dir):
    time.sleep(3)
    print("界面来自:", ui_dir)
    # 顶栏这轮改成了「品牌名 + 紧跟的免费说明 + 右侧功能按钮 + 齿轮 + 窗口三键」
    check("顶栏品牌名", window.evaluate_js(
        "document.querySelector('.brand .brand-name').textContent.trim()"), "锋芒直播录屏")
    check("「永久免费」紧跟品牌名之后",
          window.evaluate_js(
              "document.querySelector('.brand .brand-name').nextElementSibling.className"),
          "top-note")
    check("顶栏免费说明文字", window.evaluate_js(
        "document.querySelector('.brand .top-note').textContent.trim()"), NOTE_SHORT)
    check("功能按钮都在右侧 .top-actions", window.evaluate_js(
        "['btnQuality','btnFormat','btnTranscode','btnRun'].every(function (id) {"
        "  return !!document.querySelector('.top-actions #' + id); })"), True)
    check("原来的四条框换成了齿轮图标", window.evaluate_js(
        "!!document.querySelector('#btnMenu svg') && "
        "document.getElementById('btnMenu').textContent.indexOf('≡') === -1"), True)
    check("窗口三键齐全", window.evaluate_js(
        "document.querySelectorAll('.win-controls .win-btn').length"), 3)
    check("平台示例一共七行（抖音+快手合并成一行）", window.evaluate_js(
        "document.querySelectorAll('.formats .format-row').length"), 7)
    check("第一行并排是 抖音+快手", window.evaluate_js(
        "[].map.call(document.querySelectorAll('.formats .format-row')[0]"
        ".querySelectorAll('.pl-tag'), function (e) { return e.textContent; })"),
        ["抖音", "快手"])
    check("第二行是小红书", window.evaluate_js(
        "document.querySelectorAll('.formats .format-row')[1]"
        ".querySelector('.pl-tag').textContent"), "小红书")
    check("后五行是 B站/虎牙/斗鱼/Acfun/战旗", window.evaluate_js(
        "[].map.call(document.querySelectorAll('.formats .format-row .pl-tag'),"
        "  function (e) { return e.textContent; }).slice(3)"),
        ["B站", "虎牙", "斗鱼", "Acfun", "战旗"])
    check("各行说明条数", window.evaluate_js(
        "[].map.call(document.querySelectorAll('.formats .format-row'), function (row) {"
        "  return row.querySelectorAll('.fmt').length; })"), [1, 2, 3, 3, 3, 2, 1])
    check("抖音+快手共用一个说明、不重复、不带网址示例", window.evaluate_js(
        "(function () {"
        "  var rows = document.querySelectorAll('.formats .format-row');"
        "  var first = rows[0];"
        "  var all = (document.querySelector('.formats').textContent || '');"
        "  var n = all.split('支持APP直播间和主页').length - 1;"
        "  return first.querySelectorAll('code').length === 0"
        "    && first.querySelectorAll('.pl-tag').length === 2"
        "    && first.querySelectorAll('.fmt').length === 1"
        "    && n === 1; })()"), True)
    check("示例里有 Acfun 和战旗", window.evaluate_js(
        "(function(){var t=document.querySelector('.formats').textContent||'';"
        " return t.indexOf('Acfun') >= 0 && t.indexOf('战旗') >= 0; })()"), True)
    check("小红书那行不再写「读不到开播状态」", window.evaluate_js(
        "(document.querySelector('.formats').textContent || '').indexOf('读不到开播状态') === -1"), True)
    # 150% 缩放的屏上 1px 会被算成 0.666667px，所以只确认「有线」而不是量宽度
    check("平台之间有分隔线", window.evaluate_js(
        "(function () {"
        "  var s = getComputedStyle(document.querySelectorAll('.formats .format-row')[1]);"
        "  return s.borderTopStyle + ':' + (parseFloat(s.borderTopWidth) > 0);"
        "})()"), "solid:true")
    check("每条示例都标了链接类型", window.evaluate_js(
        "document.querySelectorAll('.formats .fmt i').length"), 15)
    check("三种链接类型都在", window.evaluate_js(
        "(function () {"
        "  var t = [].map.call(document.querySelectorAll('.formats .fmt i'),"
        "    function (e) { return e.textContent; });"
        "  return ['PC链接', 'APP直播间', 'APP主页'].every(function (k) {"
        "    return t.indexOf(k) >= 0; });"
        "})()"), True)
    check("示例地址都用 xxx 占位", window.evaluate_js(
        "[].every.call(document.querySelectorAll('.formats code'),"
        "  function (e) { return /xxx$/.test(e.textContent); })"), True)

    window.evaluate_js("document.getElementById('readmeModal').classList.remove('hidden');")
    time.sleep(0.8)
    check("使用说明里不再显示联系方式", window.evaluate_js(
        "document.querySelector('#readmeModal .dialog-body').textContent.indexOf('微信') === -1"),
        True)
    check("说明正文还在（含新增的「支持的平台」一段）", window.evaluate_js(
        "document.querySelectorAll('#readmeModal .doc-text').length"), 3)
    shot(WORK / "ui_readme.png")

    api._engine.stop()
    window.destroy()


def main():
    ui_dir = vault.unpack_ui(APP / "ui.bin")
    api = __import__("app").Api()
    api._engine.log = lambda *args, **kwargs: None
    window = webview.create_window(TITLE, url=str(ui_dir / "index.html"), js_api=api,
                                   width=1120, height=760)
    api._window = window
    try:
        webview.start(func=lambda: driver(window, api, ui_dir))
    finally:
        shutil.rmtree(ui_dir, ignore_errors=True)
    bad = [row for row in RESULTS if not row[1]]
    print("\n失败项：", bad if bad else "无")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
