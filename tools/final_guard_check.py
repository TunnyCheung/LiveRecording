"""交付前的只读核对：清单仍匹配最终 exe，两个 ZIP 都能打开且不含用户文件。"""
import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from core import guard  # noqa: E402
from core.version import APP_NAME, VERSION  # noqa: E402


def main():
    dist = APP / "dist" / "锋芒直播录屏"
    ok, reason = guard.verify(dist / "_internal", dist / "锋芒直播录屏.exe")
    print(f"{'OK ' if ok else 'BAD'} 最终清单: {reason or '通过'}")
    failed = not ok
    print(f"版本：{APP_NAME} {VERSION}")
    # 打包产物的文件名带版本号：<名称>-<版本>.zip / <名称>-源码-<版本>.zip
    for name in (f"{APP_NAME}-{VERSION}.zip", f"{APP_NAME}-源码-{VERSION}.zip"):
        path = APP / "dist" / name
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                bad = [
                    item for item in names
                    if item.endswith(("config.json", "config.json.bak", "window.json"))
                    or item.endswith(".log")
                    or "录像/" in item
                ]
            good = not bad
            print(f"{'OK ' if good else 'BAD'} {name}: {len(names)} 个条目，"
                  f"用户文件={bad or '无'}")
            failed = failed or not good
        except (OSError, zipfile.BadZipFile) as exc:
            print(f"BAD {name}: {exc}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
