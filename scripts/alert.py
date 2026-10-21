#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""失败告警：用 Bark 把异常直接推到手机，避免「静默失败」。

用法：
    python scripts/alert.py "标题" "正文"

环境变量：
    BARK_URL   必填，Bark 基础地址
    RUN_URL    可选，GitHub 运行日志地址，会附在正文末尾

设计原则：告警本身失败不再抛异常（否则会掩盖真正的失败原因），
只打印到 stderr。
"""

import json
import os
import sys
import urllib.request

UA = "Mozilla/5.0 (compatible; AShareBriefAlert/1.0)"


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "A股简报任务异常"
    body = sys.argv[2] if len(sys.argv) > 2 else "请到 GitHub Actions 查看运行日志"

    run_url = (os.environ.get("RUN_URL") or "").strip()
    if run_url:
        body = f"{body}\n{run_url}"

    bark = (os.environ.get("BARK_URL") or "").strip().rstrip("/")
    if not bark:
        print("[WARN] 未配置 BARK_URL，告警无法送达", file=sys.stderr)
        return 0

    payload = {
        "title": title,
        "body": body[:500],
        "group": "A股简报",
        "level": "timeSensitive",   # 专注模式下也能送达
        "isArchive": 1,
    }
    req = urllib.request.Request(
        bark,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"[OK] 告警已送达 {r.read().decode('utf-8', errors='ignore')[:120]}")
    except Exception as exc:                      # noqa: BLE001
        print(f"[ERR] 告警发送失败: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
