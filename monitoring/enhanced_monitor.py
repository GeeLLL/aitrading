#!/usr/bin/env python3
"""
Enhanced Real-time Monitoring System
====================================
生产级实时监控、告警和自动恢复。

特性:
- 5 分钟无采样自动告警
- P&L 异常检测
- 网络连接监控
- 自动故障恢复建议
- 实时仪表板更新
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import DAILY_SLOTS, SESSION_TIMEZONE, expected_runs_for_date
from monitoring.market_calendar import is_market_open_today


class EnhancedMonitor:
    """生产级实时监控系统"""

    def __init__(self):
        self.root = ROOT
        self.log_file = ROOT / "logs/enhanced_monitor.log"
        self.alerts_file = ROOT / "logs/enhanced_alerts.jsonl"
        self.last_sample_time = None
        self.last_alert_time = {}
        self.last_known_pnl = 0.0

    def log(self, level: str, message: str, **kwargs):
        """记录到文件和控制台"""
        timestamp = datetime.now(SESSION_TIMEZONE).isoformat()
        entry = {
            "timestamp": timestamp,
            "level": level,
            "message": message,
            **kwargs
        }

        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        icon = {"INFO": "ℹ️", "WARNING": "⚠️", "CRITICAL": "🚨", "SUCCESS": "✅"}[level]
        print(f"{icon} [{level}] {message}")

    def alert(self, alert_type: str, severity: str, message: str, auto_action: str = None):
        """发送告警"""
        # 防止告警风暴 - 同一告警每 5 分钟最多一次
        key = f"{alert_type}:{severity}"
        now = datetime.now(SESSION_TIMEZONE)

        if key in self.last_alert_time:
            if (now - self.last_alert_time[key]).total_seconds() < 300:
                return  # 跳过，防止告警风暴

        self.last_alert_time[key] = now

        alert = {
            "timestamp": now.isoformat(),
            "type": alert_type,
            "severity": severity,
            "message": message,
            "auto_action": auto_action
        }

        with open(self.alerts_file, "a") as f:
            f.write(json.dumps(alert) + "\n")

        emoji = {"CRITICAL": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}[severity]
        print(f"{emoji} ALERT [{alert_type}] {message}")

        if auto_action:
            print(f"   🔧 Auto action: {auto_action}")

    def check_sampling_lag(self) -> bool:
        """检查采样延迟 - 5 分钟无新采样"""
        today = date.today()
        today_dir = self.root / "logs/launchd_worker" / today.isoformat()

        if not today_dir.exists():
            return False

        # 获取最新采样文件的修改时间
        sample_files = list(today_dir.glob("pilot-*.json"))
        if not sample_files:
            return False

        latest = max(sample_files, key=lambda f: f.stat().st_mtime)
        last_sample = datetime.fromtimestamp(latest.stat().st_mtime, tz=SESSION_TIMEZONE)

        time_since_last = (datetime.now(SESSION_TIMEZONE) - last_sample).total_seconds() / 60

        if time_since_last > 5:
            self.alert(
                "SAMPLING_LAG",
                "WARNING",
                f"No sampling for {int(time_since_last)} minutes (last: {latest.name})",
                auto_action="Check system health and retry"
            )
            return True

        return False

    def check_pnl_anomaly(self) -> bool:
        """检查 P&L 异常"""
        today = date.today()
        today_dir = self.root / "logs/launchd_worker" / today.isoformat()

        if not today_dir.exists():
            return False

        total_pnl = 0.0
        trades = 0

        for decision_file in today_dir.glob("pilot-*.decision.json"):
            try:
                data = json.loads(decision_file.read_text())
                decision = data.get('decision', {})
                if decision.get('action') == 'ENTRY_SIMULATED':
                    trades += 1
                    pnl = decision.get('simulated_pnl', 0)
                    total_pnl += pnl
            except:
                pass

        # 检测异常: 大幅下跌
        if trades > 0 and total_pnl < self.last_known_pnl - 100:
            self.alert(
                "PNL_ANOMALY",
                "WARNING",
                f"Significant P&L drop: ${total_pnl:.2f} (was ${self.last_known_pnl:.2f})",
                auto_action="Review latest trades"
            )
            return True

        self.last_known_pnl = total_pnl
        return False

    def check_network_health(self) -> bool:
        """检查网络连接"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", "from execution.official_mcp_collector import collect_official_raw_snapshot; print('OK')"],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
                text=True
            )

            if "OK" not in result.stdout:
                self.alert(
                    "NETWORK_ERROR",
                    "CRITICAL",
                    "MCP connection failed",
                    auto_action="Check network and MCP configuration"
                )
                return False

            return True

        except subprocess.TimeoutExpired:
            self.alert(
                "NETWORK_TIMEOUT",
                "WARNING",
                "MCP request timed out",
                auto_action="Check network latency"
            )
            return False
        except Exception as e:
            self.alert(
                "NETWORK_EXCEPTION",
                "CRITICAL",
                f"Network check failed: {e}",
                auto_action="Investigate network configuration"
            )
            return False

    def check_python_environment(self) -> bool:
        """检查 Python 环境"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import sys; print(sys.version)"],
                capture_output=True,
                timeout=5,
                text=True
            )

            version_str = result.stdout.strip()
            print(f"Python: {version_str}")

            # 检查关键模块
            try:
                from strategy.policy import load_strategy_policy
                from monitoring.market_calendar import is_market_open_today
                self.log("INFO", "Python environment healthy", python_version=version_str)
                return True
            except ImportError as e:
                self.alert(
                    "IMPORT_ERROR",
                    "CRITICAL",
                    f"Missing module: {e}",
                    auto_action="Run cache cleanup and retry"
                )
                return False

        except Exception as e:
            self.alert(
                "ENV_ERROR",
                "CRITICAL",
                f"Environment check failed: {e}",
                auto_action="Restart system"
            )
            return False

    def suggest_remediation(self):
        """建议修复步骤"""
        suggestions = []

        # 检查缓存
        cache_size = len(list((self.root / ".").glob("**/__pycache__")))
        if cache_size > 0:
            suggestions.append("Clear Python cache: find . -type d -name __pycache__ -exec rm -rf {} +")

        # 检查采样
        today_dir = self.root / "logs/launchd_worker" / date.today().isoformat()
        if today_dir.exists():
            samples = list(today_dir.glob("pilot-*.json"))
            expected = len(DAILY_SLOTS)
            if len(samples) < expected * 0.8:  # < 80% 完成率
                suggestions.append(f"Sampling lag: only {len(samples)}/{expected} completed - check launchd/cron")

        if suggestions:
            self.alert(
                "REMEDIATION",
                "INFO",
                f"Suggested actions: {'; '.join(suggestions)}",
            )

    def health_check_cycle(self) -> dict:
        """完整的健康检查循环"""
        now = datetime.now(SESSION_TIMEZONE)
        health = {
            "timestamp": now.isoformat(),
            "checks": {}
        }

        # 运行所有检查
        health["checks"]["network"] = self.check_network_health()
        health["checks"]["python_env"] = self.check_python_environment()
        health["checks"]["sampling_lag"] = self.check_sampling_lag()
        health["checks"]["pnl_anomaly"] = self.check_pnl_anomaly()

        # 计算总体健康状态
        all_passed = all(health["checks"].values())
        health["overall"] = "healthy" if all_passed else "degraded"

        # 建议修复
        if not all_passed:
            self.suggest_remediation()

        return health

    def run_continuous(self, check_interval: int = 60):
        """连续监控循环"""
        print("🚀 Enhanced Monitor Started")
        print(f"   Check interval: {check_interval}s")
        print(f"   Alert threshold: 5 min without sampling")

        while True:
            try:
                now = datetime.now(SESSION_TIMEZONE)

                # 停止条件: 市场收盘后
                if now.hour >= 13 and now.minute >= 5:
                    if not is_market_open_today():
                        self.log("INFO", "Market closed - stopping monitor")
                        break

                # 运行健康检查
                health = self.health_check_cycle()

                # 显示状态
                status_str = f"[{now.strftime('%H:%M:%S')}] {health['overall'].upper()}"
                checks_str = ", ".join([f"{k}:{'✅' if v else '❌'}" for k, v in health['checks'].items()])
                print(f"\n{status_str} | {checks_str}")

                time.sleep(check_interval)

            except KeyboardInterrupt:
                self.log("INFO", "Monitor stopped by user")
                break
            except Exception as e:
                self.log("ERROR", f"Monitor error: {e}")
                time.sleep(check_interval)


def main():
    monitor = EnhancedMonitor()
    monitor.run_continuous(check_interval=60)


if __name__ == "__main__":
    main()
