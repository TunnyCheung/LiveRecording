"""各平台通用的小工具。

在线人数这种字段，各平台给的格式五花八门：抖音给 "2w+"、"1.5万"，
小红书给 "10万"，B站/快手给纯数字。以前每个平台各写一份 int() 转换，
碰到带单位的字符串就静默归零，界面上的「在线人数」一直是 0。
这里统一收口成一个函数，所有平台都用它。
"""
import re

_UNITS = {
    "w": 10000,
    "万": 10000,
    "k": 1000,
    "亿": 100000000,
}

_COUNT_RE = re.compile(r"([\d.]+)\s*([wk万亿])?", re.IGNORECASE)


def parse_count(value):
    """把各种写法的在线人数折成整数，拿不到就给 0。

    能吃下：23800 / "23,800" / "2w+" / "2W" / "1.5万" / "12.3k" / 2.0（浮点）。
    注意 Union[str, int, float, None] 都收，不要让它抛异常。
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value) if value else 0
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return 0
    # "+"/"人"/"人在看" 这类后缀直接无视，数字部分才是要的
    match = _COUNT_RE.search(text)
    if not match:
        return 0
    try:
        number = float(match.group(1))
    except ValueError:
        return 0
    unit = (match.group(2) or "").lower()
    # re.IGNORECASE 会把 "万" 之外的单位统一成小写，中文单位不受影响
    scale = _UNITS.get(unit) or _UNITS.get(unit.lower()) or 1
    return int(number * scale)
