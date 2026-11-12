r"""编译 Windows 安装包（Inno Setup 6）。

dist 里的绿色版由 tools/build_app.py 生成，这里只把它打成安装程序。
用法：python tools/build_installer.py
输出：<项目>\dist\锋芒直播录屏-<版本>-安装包.exe
"""
import os
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from core.version import APP_NAME, VERSION  # noqa: E402

DIST = APP / "dist" / APP_NAME
ISS = Path(__file__).resolve().parent / "installer.iss"
OUT = APP / "dist" / f"{APP_NAME}-{VERSION}-安装包.exe"


def find_iscc():
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", ""))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", ""))
    for path in (
        local / "Programs" / "Inno Setup 6" / "ISCC.exe",
        local / "Programs" / "Inno" / "ISCC.exe",
        program_files_x86 / "Inno Setup 6" / "ISCC.exe",
        program_files / "Inno Setup 6" / "ISCC.exe",
    ):
        if path.is_file():
            return path
    return None


def main():
    if not (DIST / f"{APP_NAME}.exe").is_file():
        print("缺", DIST, "——先跑 tools/build_app.py")
        return 1
    iscc = find_iscc()
    if not iscc:
        print("没找到 Inno Setup 6 的 ISCC.exe，先装一个：https://jrsoftware.org/isdl.php")
        return 1
    if OUT.exists():
        OUT.unlink()
    cmd = [str(iscc), f"/DMyAppVersion={VERSION}",
           f"/DMyAppVersionNumeric={VERSION}.0", str(ISS)]
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ISS.parent))
    if proc.returncode != 0:
        print("编译安装包失败", proc.returncode)
        return proc.returncode
    if not OUT.is_file():
        print("没生成", OUT)
        return 1
    print("安装包:", OUT, OUT.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
