"""重新打包 dist/锋芒直播录屏：跑 PyInstaller，再把 ffmpeg.exe + README.md 放到 exe 旁边。

用法：
    python tools/build_app.py              自动把版本号末位 +1 再打包（每改一次升一次版本）
    python tools/build_app.py --no-bump    不升版本号（内容完全没变、只想重打时用）

版本号在 core/version.py，文件名 dist/<名称>-<版本>.zip 跟着它走。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

import core.version as _version  # noqa: E402
from core.version import APP_NAME, VERSION  # noqa: E402
PY = APP / "venv" / "Scripts" / "python.exe"
SPEC = APP / "build" / "锋芒直播录屏.spec"
DIST = APP / "dist" / "锋芒直播录屏"
# 打包时从这里取 ffmpeg.exe（仓库里不放这个 148MB 的大件）；换机器要改这一行
FFMPEG_SRC = Path(r"F:\Media\ffmpeg\ffmpeg.exe")


def run_pack(what, env):
    proc = subprocess.run([str(PY), str(HERE / "pack.py"), what], cwd=str(HERE), env=env)
    if proc.returncode != 0:
        raise SystemExit(f"pack {what} 失败")


def run_tool(name, env):
    """跑 tools 下的另一个脚本（发布包 / 源码包），失败就整体算失败。"""
    proc = subprocess.run([str(PY), str(HERE / name)], cwd=str(APP), env=env)
    if proc.returncode != 0:
        raise SystemExit(f"{name} 失败")


def kill_running():
    subprocess.run(["taskkill", "/IM", "锋芒直播录屏.exe", "/F"], capture_output=True)
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
         "Where-Object { $_.CommandLine -like '*\\app.py*' -and $_.ProcessId -ne $PID } | "
         "ForEach-Object { $_.ProcessId }"],
        capture_output=True, text=True)
    for pid in out.stdout.split():
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)


def bump_version():
    """每打包发布一次就把版本号末位 +1（--no-bump 可跳过）。"""
    if "--no-bump" in sys.argv:
        print("版本号保持:", _version.VERSION, "（--no-bump）")
        return _version.VERSION
    old = _version.VERSION
    new = _version.bump(old)
    # bump() 只改了文件文本，同一进程里的模块属性还是旧值，这里同步一下，
    # 否则后面汇总打印会用旧版本号（产物本身因为在新进程里生成，是对的）。
    _version.VERSION = new
    print(f"版本号：{old} → {new}")
    return new


def main():
    if not PY.exists():
        print("找不到", PY, "——先建虚拟环境：python -m venv venv，再装 requirements.txt")
        return 1
    bump_version()
    kill_running()
    env = dict(os.environ, PYTHONUTF8="1")
    run_pack("ui", env)        # 先把 ui/ 加密成 ui.bin，spec 打包的就是它
    # 用 python -m PyInstaller，不碰 venv\Scripts\pyinstaller.exe：
    # 那个 exe 是在建虚拟环境时生成的，里面写死了当时的绝对路径，venv 一搬家就用不了
    cmd = [str(PY), "-m", "PyInstaller", "--noconfirm", "--distpath", str(APP / "dist"),
           "--workpath", str(APP / "build"), str(SPEC)]
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(APP), env=env)
    if proc.returncode != 0:
        print("build FAILED", proc.returncode)
        return proc.returncode
    if not DIST.is_dir():
        print("missing", DIST)
        return 1
    if FFMPEG_SRC.exists():
        shutil.copy2(FFMPEG_SRC, DIST / "ffmpeg.exe")
    else:
        print("警告：没找到", FFMPEG_SRC, "，dist 里的 ffmpeg.exe 原样保留（录制要靠它）")
    shutil.copy2(APP / "README.md", DIST / "README.md")
    run_pack("manifest", env)  # 再按最终产物生成完整性清单
    # 两个压缩包也一起生成，文件名都带版本号，方便一眼看出是哪个版本
    run_tool("make_zip.py", env)
    run_tool("make_src_zip.py", env)
    # 安装包（要 Inno Setup 6；没装就跳过，绿色版照样出）
    if subprocess.run([str(PY), str(HERE / "build_installer.py")],
                      cwd=str(APP), env=env).returncode != 0:
        print("提示：安装包没有生成，绿色版已就绪")
    print("dist:", sorted(p.name for p in DIST.iterdir()))
    print("exe size:", (DIST / f"{APP_NAME}.exe").stat().st_size)
    # bump 之后模块里的 VERSION 已经变了，这里读最新的
    ver = _version.VERSION
    print(f"版本：{APP_NAME} {ver}")
    print(f"发布包：dist/{APP_NAME}-{ver}.zip")
    print(f"源码包：dist/{APP_NAME}-源码-{ver}.zip")
    installer = APP / "dist" / f"{APP_NAME}-{ver}-安装包.exe"
    if installer.exists():
        print(f"安装包：dist/{installer.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
