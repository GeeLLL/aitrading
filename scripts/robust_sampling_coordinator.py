#!/usr/bin/env python3
"""
Robust Sampling Coordinator
=========================
管理采样执行、监控、重试和恢复的核心系统。

特性:
- 多层采样执行 (Cron + 备份 + 手动)
- 自动故障检测和恢复
- 实时进度监控
- 自动重试机制
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import DAILY_SLOTS, SESSION_TIMEZONE


class RobustSamplingCoordinator:
    """采样协调系统 - 确保采样可靠执行"""

    def __init__(self):
        self.root = ROOT
        self.log_dir = ROOT / "logs/sampling_coordinator"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.status_file = self.log_dir / "status.jsonl"
        self.retry_log = self.log_dir / "retries.jsonl"

    def log_event(self, event_type: str, message: str, **kwargs):
        """记录事件到审计日志"""
        event = {
            "timestamp": datetime.now(SESSION_TIMEZONE).isoformat(),
            "type": event_type,
            "message": message,
            **kwargs
        }

        with open(self.status_file, "a") as f:
            f.write(json.dumps(event) + "\n")

        print(f"[{event_type}] {message}")

    def check_sampling_health(self) -> dict:
        """检查采样系统健康状态"""
        now = datetime.now(SESSION_TIMEZONE)
        today = now.date()

        today_dir = ROOT / "logs/launchd_worker" / today.isoformat()

        # 检查采样进度
        if not today_dir.exists():
            return {"status": "no_samples_yet", "healthy": True}

        samples = list(today_dir.glob("pilot-*.json"))
        completed = [f for f in samples if json.loads(f.read_text()).get("status") == "COMPLETED"]
        failed = [f for f in samples if json.loads(f.read_text()).get("status") in ("SAFETY_GATE_FAILED", "ERROR")]

        health = {
            "status": "healthy" if not failed or len(completed) > len(failed) else "unhealthy",
            "total_samples": len(samples),
            "completed": len(completed),
            "failed": len(failed),
            "healthy": len(failed) == 0 or len(completed) >= len(failed)
        }

        return health

    def should_retry_sampling(self, run_time: datetime) -> bool:
        """判断是否应该重试采样"""
        # 如果距离预定采样时间已经超过 10 分钟，应该重试
        now = datetime.now(SESSION_TIMEZONE)
        time_since_scheduled = (now - run_time).total_seconds() / 60

        return time_since_scheduled > 10 and time_since_scheduled < 120

    def execute_sampling(self, symbol: str, retry_count: int = 0) -> bool:
        """执行采样，带重试机制"""
        try:
            result = subprocess.run(
                [sys.executable, str(self.root / "scripts/launchd_shadow_worker.py")],
                cwd=str(self.root),
                capture_output=True,
                timeout=300,
                text=True
            )

            if result.returncode == 0:
                self.log_event("SAMPLING_SUCCESS", f"Sampling for {symbol} completed", symbol=symbol, retry=retry_count)
                return True
            else:
                if retry_count < 3:
                    self.log_event("SAMPLING_RETRY", f"Sampling for {symbol} failed, retrying ({retry_count+1}/3)", symbol=symbol)
                    time.sleep(5)  # 等待 5 秒后重试
                    return self.execute_sampling(symbol, retry_count + 1)
                else:
                    self.log_event("SAMPLING_FAILED", f"Sampling for {symbol} failed after 3 retries", symbol=symbol)
                    return False

        except subprocess.TimeoutExpired:
            self.log_event("SAMPLING_TIMEOUT", f"Sampling for {symbol} timed out", symbol=symbol)
            return False
        except Exception as e:
            self.log_event("SAMPLING_ERROR", f"Sampling error: {e}", symbol=symbol, error=str(e))
            return False

    def verify_sampling_window(self, symbol: str) -> bool:
        """验证采样时间窗口是否合理"""
        now = datetime.now(SESSION_TIMEZONE)

        # 检查是否在市场营业时间
        if now.hour < 9 or now.hour > 16:
            self.log_event("WINDOW_CHECK", "Outside market hours", symbol=symbol, hour=now.hour)
            return False

        # 检查是否周末
        if now.weekday() >= 5:
            self.log_event("WINDOW_CHECK", "Weekend - no sampling", symbol=symbol)
            return False

        return True

    def detect_missed_sampling(self) -> list:
        """检测是否有遗漏的采样"""
        now = datetime.now(SESSION_TIMEZONE)
        today = now.date()
        today_dir = ROOT / "logs/launchd_worker" / today.isoformat()

        if not today_dir.exists():
            return []

        executed_slots = set()
        for f in today_dir.glob("pilot-*.json"):
            # 从文件名提取时间: pilot-20260723-1003.json -> 10:03
            try:
                time_str = f.name.split("-")[-1].replace(".json", "")
                if len(time_str) == 4:
                    executed_slots.add(time_str)
            except:
                pass

        missed = []
        for (hour, minute), (kind, symbol) in DAILY_SLOTS.items():
            slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            time_str = f"{hour:02d}{minute:02d}"

            # 如果这个时间槽已经过去但没有采样
            if slot_time <= now and time_str not in executed_slots:
                missed.append({
                    "time": f"{hour:02d}:{minute:02d}",
                    "symbol": symbol,
                    "kind": kind,
                    "scheduled": slot_time.isoformat()
                })

        if missed:
            self.log_event("MISSED_SAMPLING", f"Detected {len(missed)} missed samples", count=len(missed), samples=missed)

        return missed

    def auto_recover_from_failure(self):
        """从采样失败自动恢复"""
        self.log_event("AUTO_RECOVERY", "Starting automatic recovery")

        # 步骤 1: 清除缓存
        self.log_event("RECOVERY_STEP_1", "Clearing Python cache")
        subprocess.run("find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null", shell=True, cwd=str(self.root))

        # 步骤 2: 检查依赖
        self.log_event("RECOVERY_STEP_2", "Verifying dependencies")
        try:
            from strategy.policy import load_strategy_policy
            from monitoring.market_calendar import is_market_open_today
            self.log_event("RECOVERY_STEP_2", "Dependencies verified successfully")
        except ImportError as e:
            self.log_event("RECOVERY_FAILED", f"Dependency verification failed: {e}")
            return False

        # 步骤 3: 重试遗漏的采样
        self.log_event("RECOVERY_STEP_3", "Retrying missed samples")
        missed = self.detect_missed_sampling()

        for missed_sample in missed:
            if self.should_retry_sampling(datetime.fromisoformat(missed_sample["scheduled"])):
                self.log_event("RECOVERY_RETRY", f"Retrying {missed_sample['symbol']}", symbol=missed_sample['symbol'])
                self.execute_sampling(missed_sample['symbol'])

        self.log_event("AUTO_RECOVERY", "Auto recovery completed")
        return True

    def health_report(self) -> dict:
        """生成健康报告"""
        health = self.check_sampling_health()
        missed = self.detect_missed_sampling()

        report = {
            "timestamp": datetime.now(SESSION_TIMEZONE).isoformat(),
            "sampling_health": health,
            "missed_samples": missed,
            "overall_status": "healthy" if health["healthy"] and not missed else "degraded"
        }

        return report


def main():
    """主程序 - 持续监控和恢复采样"""
    coordinator = RobustSamplingCoordinator()

    print("🚀 Robust Sampling Coordinator Started")
    print("=====================================")

    # 启动持续监控循环
    check_interval = 60  # 每 60 秒检查一次

    while True:
        try:
            # 健康检查
            health = coordinator.check_sampling_health()

            # 检测遗漏
            missed = coordinator.detect_missed_sampling()

            # 如果有遗漏，尝试自动恢复
            if missed:
                coordinator.auto_recover_from_failure()

            # 生成报告
            report = coordinator.health_report()
            print(f"\n[{datetime.now(SESSION_TIMEZONE).strftime('%H:%M:%S')}] Status: {report['overall_status']}")
            print(f"  Samples: {health['completed']}/{health['total_samples']} completed")

            if missed:
                print(f"  ⚠️  {len(missed)} missed samples detected")

            time.sleep(check_interval)

        except KeyboardInterrupt:
            print("\n✅ Coordinator stopped by user")
            break
        except Exception as e:
            coordinator.log_event("COORDINATOR_ERROR", f"Unexpected error: {e}", error=str(e))
            time.sleep(check_interval)


if __name__ == "__main__":
    main()
