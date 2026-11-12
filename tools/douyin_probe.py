"""隐藏 WebView2 探针：只负责从抖音主页提取当前直播间 web_rid。"""
import json
import re
import sys
import threading
import time
from pathlib import Path

import webview

LIVE_URL_RE = re.compile(r"live\.douyin\.com/([0-9]{6,})")


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    sec_uid = args[0]
    result_path = Path(args[1])
    timeout = max(8, int(args[2]))
    result = {"web_rid": ""}
    lock = threading.Lock()
    window = webview.create_window(
        "锋芒直播录屏-检测",
        "https://www.douyin.com/user/" + sec_uid,
        width=360,
        height=240,
        hidden=True,
        focus=False,
        resizable=False,
    )

    def finish(rid=""):
        with lock:
            if result["web_rid"]:
                return
            result["web_rid"] = rid
        try:
            result_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
        try:
            window.destroy()
        except Exception:
            pass

    def on_loaded():
        def poll():
            deadline = time.time() + timeout
            while time.time() < deadline and not result["web_rid"]:
                time.sleep(0.7)
                try:
                    href = window.evaluate_js(
                        "Array.from(document.querySelectorAll('a'))"
                        ".map(a => a.href || '')"
                        ".find(h => /live\\.douyin\\.com\\/[0-9]{6,}/.test(h)) || ''"
                    ) or ""
                except Exception:
                    continue
                match = LIVE_URL_RE.search(href)
                if match:
                    finish(match.group(1))
                    return
            finish("")

        threading.Thread(target=poll, daemon=True).start()

    window.events.loaded += on_loaded
    watchdog = threading.Timer(timeout + 4, lambda: finish(""))
    watchdog.daemon = True
    watchdog.start()
    webview.start(debug=False, private_mode=True)
    watchdog.cancel()


if __name__ == "__main__":
    main()
