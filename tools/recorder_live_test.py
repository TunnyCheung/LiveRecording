"""真跑一套隔离的 Engine + Recorder，验证自动检测、起录、文件是否持续增长。"""
import json
import shutil
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from core.config import DEFAULT_CONFIG, Store  # noqa: E402
from core.engine import Engine  # noqa: E402

WORK = APP / "tools" / "_scratch" / "recorder_live"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    save = WORK / "录像"
    if save.exists():
        shutil.rmtree(save)
    save.mkdir(parents=True)
    config = WORK / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({
        "save": str(save),
        "intervalTime": 5,
        "definition": "蓝光",
        "streamType": "flv",
        "outFormat": "ts",
    })
    payload = {
        "config": cfg,
        "user": [{
            "type": "bilibili",
            "nickname": "B站录制测试",
            "userid": "6",
            "web_rid": "6",
            "unique_id": "6",
            "disable": False,
            "mode": "video",
        }],
    }
    config.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    store = Store(config)
    engine = Engine(store)
    engine.start()
    try:
        deadline = time.time() + 28
        while time.time() < deadline:
            files = list(save.rglob("*.ts"))
            if files and files[0].stat().st_size > 500_000:
                break
            time.sleep(1)
        files = list(save.rglob("*.ts"))
        print("日志:")
        for line in engine.logs:
            print(" ", line)
        print("文件:")
        for path in files:
            print(" ", path, path.stat().st_size)
        print("recorders:", list(engine.recorders))
        if not files or files[0].stat().st_size < 300_000:
            return 1
        return 0
    finally:
        engine.stop()


if __name__ == "__main__":
    sys.exit(main())
