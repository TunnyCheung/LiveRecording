"""开机启动：往当前用户的注册表 Run 项里写启动命令。

为什么用注册表而不是「开始菜单启动文件夹」：
- 注册表 HKCU\\...\\Run 是 Windows 官方推荐的自启方式，任务管理器的「启动」
  页能识别并让用户单独开关它，用户也不会找不到；
- 不需要管理员权限（只动当前用户），不会弹 UAC；
- 启动文件夹里的 .lnk 容易被杀软当成可疑快捷方式。

写进去的值是带引号的 exe 绝对路径；如果程序被挪了位置，下次开机启动会失败，
所以每次启动时都按当前路径校正一遍（ensure_synced）。
"""
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "FengmangLiveScreen"


def _exe_path():
    """当前可执行文件的路径；源码运行时用 pythonw + app.py 才能不弹黑框。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    # 开发态：python.exe 换成同目录的 pythonw.exe，避免开机会弹一个控制台窗口
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else python


def _command():
    exe = _exe_path()
    if getattr(sys, "frozen", False):
        return f'"{exe}"'
    return f'"{exe}" "{Path(__file__).resolve().parents[1] / "app.py"}"'


def is_enabled():
    """注册表里有没有我们的自启项（不管路径对不对）。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(str(value).strip())
    except (FileNotFoundError, OSError):
        return False


def enable():
    """写开机启动，返回 (是否成功, 说明)。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False, "当前系统不支持开机启动"
    command = _command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        return True, command
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"


def disable():
    """删掉开机启动项。已经不存在也算成功。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False, "当前系统不支持开机启动"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True, ""
    except FileNotFoundError:
        return True, ""
    except OSError as exc:
        return False, f"删除注册表项失败：{exc}"


def apply(enabled):
    """按开关状态写/删，返回 (是否成功, 说明)。"""
    return enable() if enabled else disable()


def ensure_synced(enabled):
    """启动时校正：程序可能被挪过位置，路径变了就按新路径重写一遍。"""
    if not enabled:
        return
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            current, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except (FileNotFoundError, OSError):
        enable()          # 开关是开的但注册表没有，补写
        return
    if str(current).strip() != _command():
        enable()
