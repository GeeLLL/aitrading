#!/usr/bin/env python3
"""
事件清理脚本 - 每小时运行一次，清理过期的事件文件
这是架构改进的第一部分，防止事件无限积累导致级联失败
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def cleanup_expired_incidents(
    incident_dir: str | Path = "logs/incidents",
    ttl_hours: int = 24,
    dry_run: bool = False,
) -> int:
    """清理超过 TTL 的事件文件

    Args:
        incident_dir: 事件目录路径
        ttl_hours: 事件过期时间（小时）
        dry_run: True 时只报告，不删除

    Returns:
        清理的文件数
    """
    directory = Path(incident_dir)
    if not directory.exists():
        return 0

    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    deleted = 0
    kept = 0

    for path in sorted(directory.glob("*.scheduler-incident.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if not dry_run:
                path.unlink()
            deleted += 1
            print(f"  🗑️  {path.name} (unreadable/corrupt)")
            continue

        # 检查是否已解决
        resolution = payload.get("resolution")
        is_resolved = isinstance(resolution, dict) and bool(str(resolution.get("status") or "").strip())

        # 检查是否过期
        try:
            detected_at = datetime.fromisoformat(payload.get("detected_at", ""))
            is_expired = detected_at < cutoff_time
        except (ValueError, TypeError):
            is_expired = False

        if is_resolved or is_expired:
            reason = "resolved" if is_resolved else "expired"
            if not dry_run:
                path.unlink()
            deleted += 1
            run_id = payload.get("run_id", path.name)
            print(f"  🗑️  {path.name} ({reason})")
        else:
            kept += 1
            age_hours = (datetime.now(timezone.utc) - detected_at).total_seconds() / 3600
            run_id = payload.get("run_id", path.name)
            print(f"  📌 {path.name} (age: {age_hours:.1f}h)")

    return deleted


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="清理过期的调度事件")
    parser.add_argument(
        "--dir",
        default="logs/incidents",
        help="事件目录 (default: logs/incidents)",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=24,
        help="事件过期时间，小时 (default: 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只报告，不删除",
    )

    args = parser.parse_args()

    print("════════════════════════════════════════════════════════════════")
    print(f"🧹 清理过期事件 (TTL: {args.ttl}h, dry-run: {args.dry_run})")
    print("════════════════════════════════════════════════════════════════")
    print()

    deleted = cleanup_expired_incidents(
        incident_dir=args.dir,
        ttl_hours=args.ttl,
        dry_run=args.dry_run,
    )

    print()
    print("════════════════════════════════════════════════════════════════")
    if args.dry_run:
        print(f"✅ [DRY RUN] 将清理 {deleted} 个事件")
    else:
        print(f"✅ 已清理 {deleted} 个事件")
    print("════════════════════════════════════════════════════════════════")

    sys.exit(0)
