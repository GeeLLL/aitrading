#!/usr/bin/env python3
"""
恢复模式采样脚本 - 当系统被事件阻止时使用
启用测试模式以忽略历史事件，强制采样继续运行
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_in_recovery_mode():
    """在恢复模式下运行采样 (忽略历史事件)"""

    print("════════════════════════════════════════════════════════════════")
    print("🔧 采样恢复模式")
    print("════════════════════════════════════════════════════════════════")
    print("")
    print("说明:")
    print("  • 启用 SHADOW_TRADING_TEST_MODE")
    print("  • 采样将忽略历史事件约束")
    print("  • 只有在诊断确认系统其他方面正常时使用")
    print("")

    env = os.environ.copy()
    env["SHADOW_TRADING_TEST_MODE"] = "1"

    print("运行采样...")
    print("")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/launchd_shadow_worker_smart.py")],
        env=env,
        cwd=ROOT,
    )

    print("")
    print("════════════════════════════════════════════════════════════════")
    if result.returncode == 0:
        print("✅ 采样在恢复模式下完成")
    else:
        print(f"❌ 采样失败，exit code: {result.returncode}")
    print("════════════════════════════════════════════════════════════════")

    return result.returncode


if __name__ == "__main__":
    sys.exit(run_in_recovery_mode())
