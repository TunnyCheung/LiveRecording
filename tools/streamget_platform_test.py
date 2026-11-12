"""多平台解析回归（离线）。

跨平台房间号撞号不能算重复；B站短链要展开成直播间；虎牙、斗鱼别名要交给
streamget 解析，不能因为不是纯数字就被提前拒绝。
"""
import sys
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from core.config import Store  # noqa: E402
from core.streamget_platform import (  # noqa: E402
    AcfunClient,
    BilibiliClient,
    DouyuClient,
    HuyaClient,
)

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append((name, ok, got, want))
    print(f"{'OK ' if ok else 'BAD'} {name}: got={got!r} want={want!r}", flush=True)


def main():
    left = {"type": "douyu", "userid": "9999", "web_rid": "9999", "unique_id": "9999"}
    right = {"type": "huya", "userid": "9999", "web_rid": "9999", "unique_id": "9999"}
    check("跨平台同房间号不算重复", Store.same_user(left, right), False)
    same = dict(left)
    check("同平台同房间号算重复", Store.same_user(left, same), True)
    check("缺平台字段时按抖音兼容", Store.same_user({"userid": "1"}, {"userid": "1"}), True)

    huya = HuyaClient()
    check("虎牙字母别名", huya.parse_input("https://www.huya.com/lpl"),
          {"room_id": "lpl"})
    check("虎牙数字房间", huya.parse_input("https://www.huya.com/660000"),
          {"room_id": "660000"})

    douyu = DouyuClient()
    check("斗鱼字母别名", douyu.parse_input("https://www.douyu.com/yyf"),
          {"room_id": "yyf"})
    check("斗鱼数字房间", douyu.parse_input("https://www.douyu.com/9999"),
          {"room_id": "9999"})
    check("斗鱼 rid 参数", douyu.parse_input("https://www.douyu.com/topic/x?rid=123456"),
          {"room_id": "123456"})

    acfun = AcfunClient()
    check("Acfun PC 直播间", acfun.parse_input("https://live.acfun.cn/live/1234"),
          {"room_id": "1234"})
    check("Acfun 手机直播间", acfun.parse_input("https://m.acfun.cn/live/1234"),
          {"room_id": "1234"})

    bili = BilibiliClient()
    check("B站普通直播间", bili.parse_input("https://live.bilibili.com/6"),
          {"room_id": "6"})
    with mock.patch(
        "core.streamget_platform.requests.get",
        return_value=mock.Mock(
            url="https://live.bilibili.com/6?spm=test",
            text="",
        ),
    ):
        check("B站短链展开直播间", bili.parse_input("https://b23.tv/AbCdEf"),
              {"room_id": "6"})
    with mock.patch(
        "core.streamget_platform.requests.get",
        return_value=mock.Mock(
            url="https://www.bilibili.com/video/BV1GJ411x7h7",
            text="",
        ),
    ):
        check("B站视频短链不是直播间", bili.parse_input("https://b23.tv/AbCdEf"), None)

    bad = [row for row in RESULTS if not row[1]]
    print("\n失败项：", bad if bad else "无")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
