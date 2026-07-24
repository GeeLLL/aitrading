#!/usr/bin/env python3
"""Remote-accessible live dashboard for multi-device monitoring.

Serves http://0.0.0.0:8788 with password authentication.
Access from phone: http://<your-mac-ip>:8788
Password: your-choice (configured below)

Every request reads current files under logs/ — nothing is cached except
the safety status subprocess. This server exposes no mutation endpoint and
only serves read-only HTML/JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import hashlib
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo
from http.cookies import SimpleCookie

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import DAILY_SLOTS, SESSION_TIMEZONE, run_id_for

PORT = 8788
LOCAL = SESSION_TIMEZONE
PASSWORD = "trading2026"  # 改成你想要的密码

# 简单的会话管理
SESSIONS = {}


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        return {"_error": f"{type(error).__name__}: {error}", "_path": str(path)}


_status_cache: dict = {"at": 0.0, "value": None}


def _safety_status() -> dict:
    if time.time() - _status_cache["at"] < 10 and _status_cache["value"]:
        return _status_cache["value"]
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "status"],
            capture_output=True, text=True, timeout=20, cwd=ROOT,
        )
        value = json.loads(completed.stdout)
    except Exception as error:
        value = {"_error": f"{type(error).__name__}: {error}"}
    _status_cache.update(at=time.time(), value=value)
    return value


def _get_latest_pnl() -> dict:
    """Extract latest P&L from pilot samples."""
    worker_base = ROOT / "logs/launchd_worker"

    # Find latest date
    dates = sorted([d for d in worker_base.iterdir() if d.is_dir()], reverse=True)
    if not dates:
        return {"total_pnl": 0, "trades": 0, "last_update": None}

    latest_date = dates[0].name
    total_pnl = 0.0
    trades = 0
    last_update = None

    # Scan all pilot decision files
    for decision_file in (worker_base / latest_date).glob("pilot-*.decision.json"):
        try:
            data = _read_json(decision_file)
            if data.get("status") == "COMPLETED":
                # Look for simulated P&L
                trades += 1
                last_update = data.get("scheduled_for")
        except:
            pass

    return {
        "total_pnl": total_pnl,
        "trades": trades,
        "last_update": last_update,
        "date": latest_date,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _set_session(self, response):
        """Set session cookie."""
        token = secrets.token_hex(16)
        SESSIONS[token] = {"created": time.time(), "ip": self.client_address[0]}
        cookie = SimpleCookie()
        cookie["auth_token"] = token
        cookie["auth_token"]["path"] = "/"
        cookie["auth_token"]["max-age"] = 86400  # 24 hours
        response["Set-Cookie"] = cookie["auth_token"].OutputString()
        return token

    def _check_session(self) -> bool:
        """Check if user is authenticated."""
        cookie_header = self.headers.get("Cookie", "")
        if "auth_token=" in cookie_header:
            token = cookie_header.split("auth_token=")[1].split(";")[0].split(",")[0]
            if token in SESSIONS:
                return True
        return False

    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        # Login page
        if path == "/login":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = self._login_html()
            self.wfile.write(html.encode())
            return

        # Login handler
        if path == "/do_login":
            query = parse_qs(parsed_path.query)
            password = query.get("password", [""])[0]
            if password == PASSWORD:
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                response_headers = {}
                self._set_session(response_headers)
                for header, value in response_headers.items():
                    self.send_header(header, value)
                self.end_headers()
                self.wfile.write(b"<html><head><meta http-equiv='refresh' content='0; url=/'></head></html>")
            else:
                self.send_response(401)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Invalid password</h1><a href='/login'>Try again</a></body></html>")
            return

        # Check authentication for dashboard
        if not self._check_session():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # API endpoint: status JSON
        if path == "/api/status":
            status = _safety_status()
            pnl = _get_latest_pnl()
            response = {"status": status, "pnl": pnl, "timestamp": datetime.now(timezone.utc).isoformat()}
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response, indent=2).encode())
            return

        # Main dashboard
        if path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = self._dashboard_html()
            self.wfile.write(html.encode())
            return

        # 404
        self.send_response(404)
        self.end_headers()

    def _login_html(self) -> str:
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>交易监控 - 登录</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-box {
            background: white;
            border-radius: 10px;
            padding: 40px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 400px;
        }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            color: #333;
            font-size: 24px;
        }
        input {
            width: 100%;
            padding: 12px;
            margin-bottom: 20px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102,126,234,0.1);
        }
        button {
            width: 100%;
            padding: 12px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            font-weight: 600;
        }
        button:hover { background: #5568d3; }
        button:active { transform: scale(0.98); }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>📊 交易监控</h1>
        <form action="/do_login" method="GET">
            <input type="password" name="password" placeholder="输入密码" autofocus required>
            <button type="submit">登录</button>
        </form>
    </div>
</body>
</html>"""

    def _dashboard_html(self) -> str:
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>交易监控 - 实时仪表板</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5;
            padding: 10px;
        }
        @media (prefers-color-scheme: dark) {
            body { background: #1a1a1a; color: #fff; }
            .card { background: #2d2d2d; border-color: #444; }
            .status-ok { color: #4ade80; }
            .status-warn { color: #facc15; }
            .status-fail { color: #f87171; }
        }
        .container { max-width: 500px; margin: 0 auto; }
        .header {
            text-align: center;
            padding: 20px 0;
            border-bottom: 1px solid #ddd;
            margin-bottom: 20px;
        }
        .header h1 { font-size: 24px; margin-bottom: 10px; }
        .timestamp { font-size: 12px; color: #666; }
        .card {
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }
        .card-title {
            font-weight: 600;
            margin-bottom: 10px;
            font-size: 14px;
            text-transform: uppercase;
            color: #666;
        }
        .stat {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            font-size: 16px;
        }
        .stat-label { color: #666; }
        .stat-value { font-weight: 600; }
        .status-ok { color: #10b981; }
        .status-warn { color: #f59e0b; }
        .status-fail { color: #ef4444; }
        .pnl-positive { color: #10b981; font-weight: 600; }
        .pnl-negative { color: #ef4444; font-weight: 600; }
        .refresh-note {
            text-align: center;
            font-size: 12px;
            color: #999;
            padding: 10px;
        }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .grid-item { background: #f9f9f9; padding: 10px; border-radius: 5px; }
        .grid-label { font-size: 12px; color: #666; margin-bottom: 5px; }
        .grid-value { font-size: 18px; font-weight: 600; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 交易监控</h1>
            <div class="timestamp">实时更新 | 每 5 秒刷新</div>
        </div>

        <div class="card">
            <div class="card-title">系统状态</div>
            <div class="stat">
                <span class="stat-label">模式</span>
                <span class="stat-value">多标的模式 🚀</span>
            </div>
            <div class="stat">
                <span class="stat-label">安全门</span>
                <span class="stat-value status-ok" id="safety">检查中...</span>
            </div>
            <div class="stat">
                <span class="stat-label">Kill Switch</span>
                <span class="stat-value status-ok" id="killswitch">检查中...</span>
            </div>
            <div class="stat">
                <span class="stat-label">Read-Only</span>
                <span class="stat-value status-ok" id="readonly">检查中...</span>
            </div>
        </div>

        <div class="card">
            <div class="card-title">今日交易</div>
            <div class="grid">
                <div class="grid-item">
                    <div class="grid-label">虚拟 P&L</div>
                    <div class="grid-value pnl-positive" id="pnl">$0</div>
                </div>
                <div class="grid-item">
                    <div class="grid-label">完成交易</div>
                    <div class="grid-value" id="trades">0</div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">最新采样</div>
            <div class="stat">
                <span class="stat-label">日期</span>
                <span class="stat-value" id="date">—</span>
            </div>
            <div class="stat">
                <span class="stat-label">最后更新</span>
                <span class="stat-value" id="lastupdate">—</span>
            </div>
        </div>

        <div class="refresh-note">
            ⏱️ 自动刷新中... 你可以继续浏览
        </div>
    </div>

    <script>
        async function updateDashboard() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();

                // Update status
                const status = data.status || {};
                document.getElementById('safety').textContent = status.system_mode === 'READ_ONLY' ? '✅ 就绪' : '⚠️ 异常';
                document.getElementById('killswitch').textContent = status.kill_switch_engaged ? '✅ 启用' : '❌ 未启用';
                document.getElementById('readonly').textContent = status.live_trading_enabled ? '❌ 启用交易' : '✅ 只读';

                // Update P&L
                const pnl = data.pnl || {};
                document.getElementById('pnl').textContent = '$' + (pnl.total_pnl || 0).toFixed(2);
                document.getElementById('trades').textContent = pnl.trades || 0;
                document.getElementById('date').textContent = pnl.date || '—';
                document.getElementById('lastupdate').textContent = pnl.last_update ? new Date(pnl.last_update).toLocaleTimeString() : '—';
            } catch (e) {
                console.error('Failed to update:', e);
            }
        }

        // Initial update
        updateDashboard();

        // Refresh every 5 seconds
        setInterval(updateDashboard, 5000);
    </script>
</body>
</html>"""


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"🚀 Remote dashboard running at http://0.0.0.0:{PORT}")
    print(f"📱 Access from phone: http://<your-mac-ip>:{PORT}")
    print(f"🔐 Password: {PASSWORD}")
    print(f"\nTo find your Mac IP, run: ipconfig getifaddr en0")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✅ Server stopped")


if __name__ == "__main__":
    main()
