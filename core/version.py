"""版本号只有一个来源：改这里的 VERSION，界面标题、窗口标题、启动日志和打包出来的
文件名都会跟着变（tools 里的打包脚本 import 的就是这份）。

规则：**每修改一次就要升一次版本号**。
    python tools/build_app.py            # 自动把末位 +1，再打包
    python tools/build_app.py --no-bump  # 不升版本号（纯重打相同内容时才用）

版本号格式：主.次.构建号（5.0.30 → 5.0.31 → …）。改了功能就升构建号。
"""

APP_NAME = "锋芒直播录屏"
VERSION = "5.0.37"


def bump(version=None):
    """把版本号末位 +1 并写回本文件，返回新版本号。

    只改 VERSION 那一行，其它内容原样保留（避免重写文件时丢注释）。
    """
    import re
    from pathlib import Path

    current = version or VERSION
    parts = str(current).split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):
        parts = [str(current), "1"]
    new_version = ".".join(parts)

    path = Path(__file__).resolve()
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'(?m)^VERSION = ".*"$', 'VERSION = "%s"' % new_version, text)
    path.write_text(text, encoding="utf-8")
    return new_version


if __name__ == "__main__":
    print(bump())
