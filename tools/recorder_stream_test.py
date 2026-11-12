"""录制器离线回归：明文拉流地址升级 HTTPS，短流不会在三次失败后过早放弃。"""
import sys
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from core.engine import Recorder  # noqa: E402


class Store:
    config = {
        "save": str(APP / "_scratch" / "recorder_stream_test"),
        "recTime": 0,
        "recFileSize": 0,
        "fileTime": 0,
        "fileSize": 0,
        "checkPK": False,
    }


class Engine:
    store = Store()
    ffmpeg = "ffmpeg.exe"

    def __init__(self, url, live=True):
        self.url = url
        self.live = live
        self.logs = []

    def current_stream_url(self, _userid):
        return self.url

    def is_live(self, _userid):
        return self.live

    def log(self, message):
        self.logs.append(message)

    def update_recording(self, _userid, _recorder):
        pass


class Proc:
    def __init__(self):
        self.code = 1

    def poll(self):
        return self.code


def main():
    user = {"userid": "1", "nickname": "测试", "type": "douyu", "mode": "video"}
    recorder = Recorder(Engine("http://example.com/live.flv"), user)
    checks = [
        (
            "HTTP 拉流地址升级成 HTTPS",
            recorder._secure_url("http://example.com/live.flv?token=1"),
            "https://example.com/live.flv?token=1",
        ),
        (
            "已经 HTTPS 的地址不变",
            recorder._secure_url("https://example.com/live.flv?token=1"),
            "https://example.com/live.flv?token=1",
        ),
    ]

    fake_clock = mock.Mock(return_value=0.0)
    engine = Engine("https://example.com/live.flv")
    recorder = Recorder(engine, user)
    recorder._run_ffmpeg = mock.Mock(return_value=Proc())
    recorder._session_bytes = mock.Mock(return_value=0)
    with mock.patch("core.engine.time.time", fake_clock), \
            mock.patch("core.engine.time.sleep", return_value=None):
        recorder.run()
    checks.append(
        (
            "短流重连次数超过三次才停止",
            "连续中断" in recorder.last_error,
            True,
        )
    )
    checks.append(("连续重连尝试为 12 次", recorder.fast_attempts, 12))

    failed = []
    for name, got, want in checks:
        ok = got == want
        print(f"{'OK ' if ok else 'BAD'} {name}: got={got!r} want={want!r}")
        if not ok:
            failed.append((name, got, want))
    print("\n失败项：", failed if failed else "无")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
