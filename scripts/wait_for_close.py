#!/usr/bin/env python3
"""睡到 A 股收盘之后再继续，用等待吸收 GitHub 的队列延迟。

为什么需要这个
--------------
GitHub Actions 原生 cron 是 best-effort。本账号的实测调度历史（a-share-brief、
trend-dashboard、us-share-brief 三个仓库）显示：

    延迟区间 0.5 ~ 5.5 小时，偶尔直接丢弃不跑，且失败完全静默。

a-share-brief 的证据：
    10-21  cron 07:20 / 07:45  →  实际 14:10 / 14:35（迟到约 6h50m）
    10-22  cron 07:20 / 07:45  →  完全没有触发

如果把 cron 直接设在 15:20（07:20 UTC），一旦迟到 2 小时就变成 17:20，
迟到 5 小时就彻底变成晚上推送。cron 越靠近送达时限，容错空间越小。

反过来做：cron 设在收盘前（北京 10:00 ~ 14:00），job 一旦被调度起来就先
睡到 15:12。这样「队列延迟」和「睡眠」互相抵消：

    北京 10:00 准点起跑    -> 睡 5h12m -> 15:12 干活 -> 15:15 送达
    北京 10:00 延迟 3 小时  -> 13:00 起跑 -> 睡 2h12m -> 15:12 干活
    北京 10:00 延迟 5 小时  -> 15:00 起跑 -> 睡 12 分钟 -> 15:12 干活
    北京 10:00 延迟 6 小时  -> 16:00 起跑 -> 不睡，立即干活（迟到但当天必达）

即：只要 job 在当天被调度起来，「送达时间」就被钉死在 15:12 之后几分钟，
而不是随 cron 延迟漂移。

为什么敢睡这么久
----------------
仓库是 public，GitHub Actions 分钟数免费且不限量（私有仓库才有 2000 分钟/
月的限制）。每天睡 5 小时不产生任何费用。

为什么是 15:12 不是 15:00
-------------------------
收盘是 15:00，但两个数据源需要落库时间：
  - 腾讯/东财的行情时间戳要刷新到 15:00:00 之后
  - 新浪的「A股收评」通常在 15:01 ~ 15:08 之间发布，成交额/上涨家数/
    领涨领跌题材都从这篇里解析，抓早了归因会缺数据
所以留 12 分钟缓冲。送达约 15:15，仍在 15:20 的承诺之内。
"""

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")

# 目标时刻：北京时间 15:12（收盘后 12 分钟，等行情与收评落库）
TARGET_HOUR = 15
TARGET_MINUTE = 12

# 测试用覆盖：ASHARE_TARGET="15:40" 可以强制指定目标时刻
_override = os.environ.get("ASHARE_TARGET", "").strip()
if _override:
    try:
        h, m = _override.split(":")
        TARGET_HOUR, TARGET_MINUTE = int(h), int(m)
        print(f"[TEST] 目标时刻被覆盖为 {TARGET_HOUR:02d}:{TARGET_MINUTE:02d}")
    except Exception:
        print(f"[WARN] ASHARE_TARGET 格式不对：{_override!r}，用默认值")

# 单个 job 的硬上限是 6 小时。留出 checkout / setup / 抓数 / 部署的余量，
# 睡眠最长按 5h30m 控制。超过这个量说明 cron 设得太早或这次是迟到的旧触发，
# 直接跳过交给后面的触发点，而不是把 job 拖到超时（超时会被判失败并误发告警）。
MAX_WAIT_SECONDS = 5 * 3600 + 30 * 60

# 心跳间隔：让日志能看出它是在睡而不是卡死
HEARTBEAT_SECONDS = 15 * 60


def gh_output(**kv):
    """写 GitHub Actions 的 step output（本地直接跑时静默跳过）。"""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        for k, v in kv.items():
            print(f"[OUTPUT] {k}={v}")
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def main():
    now = datetime.now(CN)
    print(f"[INFO] 北京时间 {now.strftime('%Y-%m-%d %H:%M:%S')}（星期{now.isoweekday()}）")

    # 周末不等待：cron 已限定 UTC 周一至周五（对应北京周一至周五），这里只是防御
    if now.weekday() >= 5:
        print("[INFO] 今天是周末，不等待。")
        gh_output(waited="0", reason="weekend")
        return 0

    target = now.replace(hour=TARGET_HOUR, minute=TARGET_MINUTE,
                         second=0, microsecond=0)
    delta = (target - now).total_seconds()

    if delta <= 0:
        print(f"[INFO] 已过 {target.strftime('%H:%M')}，无需等待，直接继续。")
        gh_output(waited="0", reason="already-closed")
        return 0

    if delta > MAX_WAIT_SECONDS:
        # cron 设得太早（或是昨天的旧触发迟到到现在），睡下去会撑爆 6 小时上限。
        # 跳过本次，交给后面的触发点。
        print(f"[WARN] 距 {target.strftime('%H:%M')} 还有 {delta / 3600:.2f} 小时，"
              f"超过单 job 可承受的 {MAX_WAIT_SECONDS / 3600:.1f} 小时。"
              f"本次跳过，等后续触发点。")
        gh_output(waited="0", reason="too-early")
        return 0

    print(f"[INFO] 距 {target.strftime('%H:%M')} 还有 {delta / 60:.0f} 分钟，开始等待。"
          f"这是一次「提前排队」——把 cron 的队列延迟用睡眠吸收掉。")

    slept = 0
    next_beat = HEARTBEAT_SECONDS
    while slept < delta:
        chunk = min(30, delta - slept)
        time.sleep(chunk)
        slept += chunk
        if slept >= next_beat:
            print(f"[INFO] 已等待 {slept / 60:.0f} 分钟，"
                  f"距 {target.strftime('%H:%M')} 还有 {(delta - slept) / 60:.0f} 分钟")
            next_beat += HEARTBEAT_SECONDS

    after = datetime.now(CN)
    print(f"[INFO] 等待结束，北京时间 {after.strftime('%H:%M:%S')}，开始抓取收盘数据。")
    gh_output(waited=f"{slept:.0f}", reason="waited")
    return 0


if __name__ == "__main__":
    sys.exit(main())
