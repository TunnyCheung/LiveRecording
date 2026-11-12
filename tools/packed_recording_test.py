"""打包版真实录制冒烟：用临时配置启动 exe，加入公开直播间，验证自动录出可读视频。

跑完恢复 dist 原来的 config.json / window.json / 录像目录，不动用户现有主播。
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from core.version import APP_NAME, VERSION  # 版本号唯一来源

APP = Path(__file__).resolve().parents[1]
DIST = APP / "dist" / "锋芒直播录屏"
EXE = DIST / "锋芒直播录屏.exe"
WORK = APP / "tools" / "_scratch" / "packed_recording"
FFMPEG = Path(r"F:\Media\ffmpeg\ffmpeg.exe")
FFPROBE = Path(r"F:\Media\ffmpeg\ffprobe.exe")

CASES = {
    "bilibili": {
        "type": "bilibili", "nickname": "B站录制测试", "userid": "6",
        "web_rid": "6", "unique_id": "6", "disable": False, "mode": "video",
    },
    "douyu": {
        "type": "douyu", "nickname": "斗鱼录制测试", "userid": "9999",
        "web_rid": "9999", "unique_id": "9999", "disable": False, "mode": "video",
    },
}
MIN_DURATION = 8.0
MIN_SIZE = 800_000
MIN_RECORD_SECONDS = 15


def latest_recording(user):
    temp_recordings = WORK / "录像"
    roots = [
        temp_recordings / f"{user['nickname']}_{user['unique_id']}",
    ]
    roots.extend(temp_recordings.glob(f"*_{user['unique_id']}"))
    files = []
    for root in roots:
        if root.exists():
            files.extend(root.glob("*.ts"))
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def kill():
    subprocess.run(["taskkill", "/IM", EXE.name, "/F"], capture_output=True)
    subprocess.run(["taskkill", "/IM", "ffmpeg.exe", "/F"], capture_output=True)
    time.sleep(1.2)


def parse_args():
    parser = argparse.ArgumentParser(description="打包版真实录制冒烟测试")
    parser.add_argument(
        "cases",
        nargs="*",
        choices=("all", *CASES),
        default=["all"],
        help="要测试的平台；默认全部",
    )
    args = parser.parse_args()
    names = list(CASES) if "all" in args.cases else list(dict.fromkeys(args.cases))
    return [CASES[name] for name in names]


def main():
    cases = parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    config = DIST / "config.json"
    window = DIST / "window.json"
    recordings = DIST / "录像"
    saved_config = config.read_bytes() if config.exists() else None
    saved_window = window.read_bytes() if window.exists() else None
    temp_recordings = WORK / "录像"
    log = DIST / "锋芒直播录屏.log"
    log_saved = WORK / "锋芒直播录屏.log"
    if log_saved.exists():
        log_saved.unlink()
    if temp_recordings.exists():
        shutil.rmtree(temp_recordings)
    temp_recordings.mkdir(parents=True)
    try:
        kill()
        payload = {
            "config": {
                "intervalTime": 5,
                "definition": "蓝光",
                "streamType": "flv",
                "outFormat": "ts",
                "save": str(temp_recordings),
                "closeToTray": False,
            },
            "user": cases,
        }
        config.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        window.unlink(missing_ok=True)
        subprocess.Popen([str(EXE)], cwd=str(DIST))
        print("等待打包版启动，然后手动开启检测录制……", flush=True)
        time.sleep(10)
        click_text("检测录制")
        started = time.time()
        deadline = started + 45
        print("已点「检测录制」，等待每路录像至少 800 KB 且录制 15 秒……", flush=True)
        while time.time() < deadline:
            elapsed = time.time() - started
            sizes = []
            for user in cases:
                file = latest_recording(user)
                sizes.append(file.stat().st_size if file else 0)
            if elapsed >= MIN_RECORD_SECONDS and all(size >= MIN_SIZE for size in sizes):
                break
            time.sleep(2)

        kill()
        time.sleep(1)
        failed = []
        for user in cases:
            file = latest_recording(user)
            if not file:
                print(f"BAD {user['nickname']}: 没有生成录像")
                failed.append(user["nickname"])
                continue
            probe = subprocess.run(
                [str(FFPROBE), "-v", "error", "-show_entries", "format=duration,size",
                 "-of", "json", str(file)],
                capture_output=True, text=True,
            )
            try:
                info = json.loads(probe.stdout)["format"]
            except (KeyError, ValueError, json.JSONDecodeError):
                info = {}
            duration = float(info.get("duration") or 0)
            size = int(info.get("size") or file.stat().st_size)
            ok = duration >= MIN_DURATION and size >= MIN_SIZE
            print(
                f"{'OK ' if ok else 'BAD'} {user['nickname']}: "
                f"{file.name} duration={duration:.3f} size={size}"
            )
            if not ok:
                failed.append(user["nickname"])
        return 1 if failed else 0
    finally:
        if log.exists():
            shutil.copy2(log, log_saved)
        kill()
        if saved_config is not None:
            config.write_bytes(saved_config)
        else:
            config.unlink(missing_ok=True)
        if saved_window is not None:
            window.write_bytes(saved_window)
        else:
            window.unlink(missing_ok=True)
        if recordings.exists():
            shutil.rmtree(recordings, ignore_errors=True)
        temp = temp_recordings
        # 保留本次录像便于人工核对；下次测试开始时会清掉


def click_text(text):
    """在当前窗口里用 WebView 键盘导航太脆，直接找名为该文字的 Win32 子按钮也不稳；
    这里调用 pywebview 的 JS 只能从外部截图调试。测试前保持窗口前台，用坐标点击。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    hwnd = user32.FindWindowW(None, f"{APP_NAME} {VERSION}")
    if not hwnd:
        print("BAD 没找到主窗口")
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    # 底栏「检测录制」在右下角，按当前 1120x760 布局的相对位置点
    x = rect.left + width - 58
    y = rect.top + height - 34
    user32.SetForegroundWindow(hwnd)
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    print("点击坐标:", x, y)
    return True


if __name__ == "__main__":
    sys.exit(main())
