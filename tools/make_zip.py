r"""把 dist/锋芒直播录屏 打成发布压缩包（不带 config.json / window.json / 日志 / 录像）。

用法：python tools/make_zip.py [输出zip]
默认输出：<项目>\dist\锋芒直播录屏-<版本>.zip（版本号取自 core/version.py）
"""
import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from core.version import APP_NAME, VERSION  # noqa: E402

SRC = APP / "dist" / APP_NAME
OUT = (Path(sys.argv[1]) if len(sys.argv) > 1
       else APP / "dist" / f"{APP_NAME}-{VERSION}.zip")
SKIP_FILES = {"config.json", "config.json.bak", "window.json", "douyin_rooms.json"}
SKIP_PREFIX = ("锋芒直播录屏.log", "douyin_probe_")
SKIP_DIRS = {"录像"}


def main():
    if not SRC.is_dir():
        print("missing", SRC)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    count = 0
    total = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(SRC.rglob("*")):
            rel = path.relative_to(SRC)
            if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
                continue
            if path.name in SKIP_FILES or path.name.startswith(SKIP_PREFIX):
                continue
            z.write(path, APP_NAME + "/" + rel.as_posix())
            count += 1
            total += path.stat().st_size
    print("entries:", count, "uncompressed:", total)
    print("版本:", VERSION)
    print("zip:", OUT, OUT.stat().st_size)
    with zipfile.ZipFile(OUT) as z:
        bad = [n for n in z.namelist()
               if "config" in n or n.endswith(".bak") or "window.json" in n
               or "录像" in n or ".log" in n or "douyin_rooms.json" in n
               or "douyin_probe_" in n]
        print("user files present (should be empty):", bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
