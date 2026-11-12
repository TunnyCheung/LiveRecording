"""清掉 dist/锋芒直播录屏 里的运行残留（配置、窗口位置、日志、录像、探针缓存）。"""
import shutil
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist" / "锋芒直播录屏"
SCRATCH = ("config.json", "config.json.bak", "window.json",
           "锋芒直播录屏.log", "锋芒直播录屏.log.old", "douyin_rooms.json")
SCRATCH_DIRS = ("录像",)
SCRATCH_GLOBS = ("douyin_probe_*.json",)

if not DIST.is_dir():
    raise SystemExit(f"没有 {DIST}")

for name in SCRATCH:
    path = DIST / name
    if path.is_file():
        path.unlink()
        print("removed", name)

for name in SCRATCH_GLOBS:
    for path in DIST.glob(name):
        if path.is_file():
            path.unlink()
            print("removed", path.name)

for name in SCRATCH_DIRS:
    path = DIST / name
    if path.is_dir():
        shutil.rmtree(path)
        print("removed", name + "/")

print("dist:", sorted(p.name for p in DIST.iterdir()))
