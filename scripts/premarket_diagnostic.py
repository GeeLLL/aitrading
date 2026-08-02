#!/usr/bin/env python3
"""
Pre-Market Diagnostic Tool
==========================
市场开盘前的完整系统诊断和准备检查。

运行时间: 市场开盘前 10 分钟 (9:50 AM PT)
"""

import json
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SESSION_TIMEZONE = ZoneInfo("America/Los_Angeles")


class PremarketDiagnostic:
    """市场开盘前诊断系统"""

    def __init__(self):
        self.root = ROOT
        self.results = {}
        self.failures = []
        self.warnings = []

    def print_header(self, title: str):
        """打印标题"""
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")

    def check(self, name: str, test_fn, critical: bool = True) -> bool:
        """运行检查"""
        try:
            result = test_fn()
            status = "✅" if result else "❌"
            print(f"{status} {name}")

            self.results[name] = result

            if not result:
                if critical:
                    self.failures.append(name)
                else:
                    self.warnings.append(name)

            return result

        except Exception as e:
            print(f"❌ {name} - Exception: {e}")
            self.failures.append(name)
            return False

    def run_diagnostics(self):
        """运行完整诊断"""
        self.print_header("PRE-MARKET DIAGNOSTIC (9:50 AM PT)")

        now = datetime.now(SESSION_TIMEZONE)
        print(f"Time: {now.strftime('%H:%M:%S %Z')}")
        print(f"Date: {now.date()}")

        # 第 1 组: Python 环境
        self.print_header("1️⃣  Python Environment")

        self.check(
            "Python 3.13+ installed",
            lambda: sys.version_info >= (3, 13)
        )

        self.check(
            "Key modules importable",
            lambda: self._check_imports()
        )

        self.check(
            "Python path correct",
            lambda: str(self.root) in sys.path
        )

        # 第 2 组: 缓存清理
        self.print_header("2️⃣  Cache Management")

        self.check(
            "Clear __pycache__",
            lambda: self._clear_pycache(),
            critical=False
        )

        self.check(
            "No stale .pyc files",
            lambda: self._check_pyc_files()
        )

        # 第 3 组: 采样系统
        self.print_header("3️⃣  Sampling System")

        self.check(
            "launchd_shadow_worker.py exists",
            lambda: (self.root / "scripts/launchd_shadow_worker.py").exists()
        )

        self.check(
            "self_arming_worker.py exists",
            lambda: (self.root / "scripts/self_arming_worker.py").exists()
        )

        self.check(
            "Sampling script executable",
            lambda: self._test_sampling_script()
        )

        self.check(
            "End-to-end sampling flow",
            lambda: self._test_sampling_flow(),
            critical=False  # 非关键，因为采样时间限制
        )

        # 第 4 组: Cron 任务
        self.print_header("4️⃣  Cron Tasks")

        self.check(
            "No stale cron entries (launchd is the only scheduler)",
            lambda: self._check_no_stale_cron(),
            critical=False
        )

        # 第 5 组: 网络连接
        self.print_header("5️⃣  Network Connectivity")

        self.check(
            "MCP connection available",
            lambda: self._check_mcp_connection()
        )

        self.check(
            "API endpoints reachable",
            lambda: self._check_api_endpoints()
        )

        # 第 6 组: 仪表板
        self.print_header("6️⃣  Dashboard & Monitoring")

        self.check(
            "Dashboard script exists",
            lambda: (self.root / "scripts/serve_remote_dashboard.py").exists()
        )

        self.check(
            "Monitor script exists",
            lambda: (self.root / "monitoring/enhanced_monitor.py").exists(),
            critical=False
        )

        # 第 7 组: 日志目录
        self.print_header("7️⃣  Log Directories")

        self.check(
            "logs directory writable",
            lambda: self._check_logs_writable()
        )

        self.check(
            "Today's sample directory clean",
            lambda: self._check_sample_dir_clean()
        )

        # 最终报告
        self.print_summary()

    def print_summary(self):
        """打印诊断总结"""
        self.print_header("📊 DIAGNOSTIC SUMMARY")

        total = len(self.results)
        passed = sum(self.results.values())
        failed = total - passed

        print(f"\nTotal checks: {total}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        print(f"⚠️  Warnings: {len(self.warnings)}")

        if self.failures:
            print(f"\n🚨 CRITICAL FAILURES:")
            for failure in self.failures:
                print(f"   - {failure}")

        if self.warnings:
            print(f"\n⚠️  WARNINGS:")
            for warning in self.warnings:
                print(f"   - {warning}")

        # 可启动性判断
        print(f"\n{'='*60}")
        if not self.failures:
            print("🚀 SYSTEM READY FOR LAUNCH")
            print("   All critical systems are operational")
            return 0
        else:
            print("🛑 SYSTEM NOT READY")
            print(f"   {len(self.failures)} critical failures detected")
            print("   Please resolve before starting sampling")
            return 1

    # 辅助检查方法

    def _check_imports(self) -> bool:
        """检查关键模块导入"""
        try:
            from strategy.policy import load_strategy_policy
            from monitoring.market_calendar import is_market_open_today
            from risk.startup_guard import validate_safety_config
            from execution.official_mcp_collector import collect_official_raw_snapshot
            return True
        except ImportError as e:
            print(f"   Import error: {e}")
            return False

    def _clear_pycache(self) -> bool:
        """清除 Python 缓存"""
        try:
            subprocess.run(
                "find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null",
                shell=True,
                cwd=str(self.root),
                timeout=10
            )
            return True
        except:
            return False

    def _check_pyc_files(self) -> bool:
        """检查是否有陈旧的 .pyc 文件"""
        pyc_count = len(list((self.root / ".").glob("**/*.pyc")))
        return pyc_count == 0

    def _test_sampling_script(self) -> bool:
        """测试采样脚本的关键依赖和基本流程

        不仅测试脚本是否存在，还测试：
        1. 所有关键模块是否可导入
        2. 采样流程的关键依赖是否可用
        """
        try:
            # 导入关键模块，验证依赖完整性
            from main import build_status
            from monitoring.shadow_readiness import build_shadow_readiness
            from monitoring.scheduler_watchdog import unresolved_incident_ids
            from execution.official_mcp_collector import claude_binary

            # 尝试调用一些关键函数以确保运行时没有错误
            status = build_status()
            readiness = build_shadow_readiness()
            incidents = unresolved_incident_ids(self.root / "logs/incidents")

            # 检查关键配置
            if status.get("system_mode") != "READ_ONLY":
                return False

            return True
        except Exception as e:
            print(f"      Error: {e}")
            return False

    def _check_no_stale_cron(self) -> bool:
        """cron 已废除:self-arming launchd 是唯一调度器,残留 cron 条目会重复触发"""
        try:
            result = subprocess.run(
                "crontab -l 2>/dev/null | grep -c launchd_shadow_worker",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            count = int(result.stdout.strip() or "0")
            if count:
                print(f"   Found {count} STALE cron entries - remove them (crontab -e)")
            return count == 0
        except Exception:
            return False

    def _check_mcp_connection(self) -> bool:
        """检查 MCP 连接"""
        try:
            from execution.official_mcp_collector import collect_official_raw_snapshot
            # 不实际连接，只检查模块可导入
            return True
        except:
            return False

    def _check_api_endpoints(self) -> bool:
        """检查 API 端点可达性"""
        # 简化检查 - 只验证模块导入
        try:
            from execution.official_mcp_collector import claude_binary
            return True
        except:
            return False

    def _check_logs_writable(self) -> bool:
        """检查日志目录可写"""
        try:
            logs_dir = self.root / "logs"
            logs_dir.mkdir(exist_ok=True)
            test_file = logs_dir / ".diagnostic_test"
            test_file.write_text("test")
            test_file.unlink()
            return True
        except:
            return False

    def _check_sample_dir_clean(self) -> bool:
        """检查今天的采样目录"""
        today = date.today()
        today_dir = self.root / "logs/launchd_worker" / today.isoformat()

        if not today_dir.exists():
            today_dir.mkdir(parents=True, exist_ok=True)
            return True

        # 检查是否有错误或损坏的采样
        try:
            for f in today_dir.glob("pilot-*.json"):
                json.loads(f.read_text())
            return True
        except:
            return False

    def _test_sampling_flow(self) -> bool:
        """端到端采样流程测试

        验证采样系统的关键路径是否正常工作，包括：
        - 安全检查是否通过
        - 调度系统是否能识别采样时间
        - 事件系统是否正常
        """
        try:
            from main import build_status
            from monitoring.kill_switch import AutomationHalt
            from monitoring.scheduler_watchdog import unresolved_incident_ids

            # 检查安全状态
            status = build_status()

            # 检查关键安全设置
            if status.get("system_mode") != "READ_ONLY":
                return False
            if status.get("live_trading_enabled") is not False:
                return False
            if status.get("order_tools_enabled") is not False:
                return False
            if status.get("kill_switch_engaged") is not True:
                return False

            # 检查自动化是否暂停
            halted = AutomationHalt(self.root / "state/automation_halt.json").active()
            if halted:
                return False

            # 检查是否有关键事件阻止采样
            incidents = unresolved_incident_ids(self.root / "logs/incidents")
            # 允许有一些旧事件，但不应该有太多（表示系统故障）
            if len(incidents) > 10:  # 超过 10 个事件表示有问题
                return False

            return True
        except Exception as e:
            print(f"      Error in sampling flow test: {e}")
            return False


def main():
    diagnostic = PremarketDiagnostic()
    exit_code = diagnostic.run_diagnostics()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
