#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日完成闸门：判断今天的简报是否已经完整跑完并推送。

history.json 中已存在今日日期 = 今天已成功推送（该文件由工作流在推送成功后回写）。
三个冗余 cron 触发点靠这个判断去重，避免重复推送。

用法：
    python scripts/check_done.py           # 正常判断
    python scripts/check_done.py --force   # 忽略已完成状态，强制重跑
输出：
    today=YYYY-MM-DD
    done=true|false
    skip=true|false
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY = os.path.join(ROOT, "data", "history.json")
TZ_CN = datetime.timezone(datetime.timedelta(hours=8))

today = datetime.datetime.now(TZ_CN).strftime("%Y-%m-%d")
force = "--force" in sys.argv

done = False
if os.path.exists(HISTORY):
    try:
        with open(HISTORY, encoding="utf-8") as f:
            done = today in (json.load(f) or {})
    except (ValueError, OSError) as exc:
        print(f"[WARN] 读取 history.json 失败：{exc}")
        done = False

skip = done and not force

print(f"today={today}")
print(f"done={'true' if done else 'false'}")
print(f"force={'true' if force else 'false'}")
print(f"skip={'true' if skip else 'false'}")
