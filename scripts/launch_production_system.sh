#!/bin/bash
# Production System Launcher
# =======================
# 一键启动完整的 Formal Shadow Trading 生产系统

set -e

REPO_ROOT="/Users/ge/ge/aitrading"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "🚀 Production System Launcher"
echo "Start time: $TIMESTAMP"
echo "=================================="

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 函数: 打印步骤
log_step() {
    echo -e "${GREEN}✅${NC} $1"
}

# 函数: 打印警告
log_warning() {
    echo -e "${YELLOW}⚠️${NC} $1"
}

# 函数: 打印错误
log_error() {
    echo -e "${RED}❌${NC} $1"
}

cd "$REPO_ROOT"

# 第 1 步: 诊断
echo ""
echo "=================================="
echo "PHASE 1: Pre-Market Diagnostic"
echo "=================================="

if python3 scripts/premarket_diagnostic.py; then
    log_step "Diagnostic passed - all systems ready"
else
    log_error "Diagnostic failed - fix issues before launching"
    exit 1
fi

# 第 2 步: 清除缓存
echo ""
echo "=================================="
echo "PHASE 2: Cache Cleanup"
echo "=================================="

find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -type f -name "*.pyc" -delete 2>/dev/null
log_step "Python cache cleared"

# 第 3 步: 启动后台服务
echo ""
echo "=================================="
echo "PHASE 3: Starting Background Services"
echo "=================================="

# 启动采集观察器 (observe-only; 绝不回补漏采的样本)
log_step "Starting Collection Observer (observe-only, never backfills)..."
python3 -u scripts/robust_sampling_coordinator.py >> logs/sampling_coordinator.log 2>&1 &
COORDINATOR_PID=$!
echo "   PID: $COORDINATOR_PID"

# 启动增强监控
log_step "Starting Enhanced Monitor..."
python3 -u monitoring/enhanced_monitor.py >> logs/enhanced_monitor.log 2>&1 &
MONITOR_PID=$!
echo "   PID: $MONITOR_PID"

# 启动实时仪表板
log_step "Starting Dashboard..."
python3 scripts/serve_remote_dashboard.py >> logs/dashboard.log 2>&1 &
DASHBOARD_PID=$!
echo "   PID: $DASHBOARD_PID"

sleep 2

# 验证所有服务启动成功
echo ""
echo "=================================="
echo "PHASE 4: Service Verification"
echo "=================================="

if ps -p $COORDINATOR_PID > /dev/null; then
    log_step "Sampling Coordinator running (PID: $COORDINATOR_PID)"
else
    log_error "Sampling Coordinator failed to start"
    exit 1
fi

if ps -p $MONITOR_PID > /dev/null; then
    log_step "Enhanced Monitor running (PID: $MONITOR_PID)"
else
    log_error "Enhanced Monitor failed to start"
    exit 1
fi

if ps -p $DASHBOARD_PID > /dev/null; then
    log_step "Dashboard running (PID: $DASHBOARD_PID)"
else
    log_warning "Dashboard may be starting - checking connectivity..."
fi

# 第 5 步: 启动完成信息
echo ""
echo "=================================="
echo "🎉 SYSTEM LAUNCH COMPLETE"
echo "=================================="

echo ""
echo "📊 Service Status:"
echo "   Sampling Coordinator: http://PID:$COORDINATOR_PID"
echo "   Enhanced Monitor: http://PID:$MONITOR_PID"
echo "   Real-time Dashboard: http://127.0.0.1:8788"
echo ""

echo "📋 Next Steps:"
echo "   1. Open dashboard: http://127.0.0.1:8788 (password: trading2026)"
echo "   2. Monitor sampling progress in real-time"
echo "   3. Alerts will be shown if issues detected"
echo "   4. Missed samples surface as incidents — they are NEVER backfilled"
echo ""

echo "📊 Market Hours:"
echo "   Trading: 10:00 AM - 1:05 PM PT"
echo "   Expected samples: 8 total"
echo ""

echo "🔍 View Logs:"
echo "   tail -f logs/enhanced_monitor.log"
echo "   tail -f logs/sampling_coordinator/status.jsonl"
echo ""

echo "=================================="
echo "✨ All systems are now running!"
echo "=================================="
echo ""
echo "Press Ctrl+C to stop services and generate report"

# 保存 PID 以便稍后停止
echo "$COORDINATOR_PID" > .pids/coordinator.pid
echo "$MONITOR_PID" > .pids/monitor.pid
echo "$DASHBOARD_PID" > .pids/dashboard.pid

mkdir -p .pids

# 等待直到用户中断或市场收盘
wait_until_market_close() {
    while true; do
        HOUR=$(date '+%H')
        MINUTE=$(date '+%M')

        # 检查是否超过 13:05
        if [ "$HOUR" -ge 13 ] && [ "$MINUTE" -ge 5 ]; then
            echo ""
            echo "📊 Market closed at 13:05"
            break
        fi

        sleep 60
    done
}

# 监听 Ctrl+C
trap_exit() {
    echo ""
    echo ""
    echo "🛑 Shutting down services..."
    kill $COORDINATOR_PID 2>/dev/null || true
    kill $MONITOR_PID 2>/dev/null || true
    kill $DASHBOARD_PID 2>/dev/null || true

    echo "✅ Services stopped"
    echo ""
    echo "📊 Generating final report..."
    python3 monitoring/data_integrity_validator.py

    echo ""
    echo "✅ Session complete"
    exit 0
}

trap trap_exit EXIT INT TERM

# 保持脚本运行直到市场收盘
wait_until_market_close
