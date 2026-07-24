#!/usr/bin/env python3
"""
Data Integrity Validator
========================
采样数据完整性验证和异常检测系统。

特性:
- 采样文件完整性检查
- 数据异常检测
- 自动修复建议
- 审计日志记录
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SESSION_TIMEZONE = ZoneInfo("America/Los_Angeles")


class DataIntegrityValidator:
    """数据完整性验证系统"""

    def __init__(self):
        self.root = ROOT
        self.audit_log = ROOT / "logs/data_integrity_audit.jsonl"
        self.issues = []

    def log_audit(self, event_type: str, message: str, **details):
        """记录审计日志"""
        entry = {
            "timestamp": datetime.now(SESSION_TIMEZONE).isoformat(),
            "type": event_type,
            "message": message,
            **details
        }

        with open(self.audit_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

        print(f"[AUDIT] {event_type}: {message}")

    def validate_sample_file(self, sample_file: Path) -> dict:
        """验证单个采样文件"""
        result = {
            "file": sample_file.name,
            "valid": True,
            "issues": []
        }

        try:
            # 读取文件
            data = json.loads(sample_file.read_text())

            # 检查必需字段
            required_fields = ["status", "symbol", "timestamp"]
            for field in required_fields:
                if field not in data:
                    result["valid"] = False
                    result["issues"].append(f"Missing field: {field}")

            # 检查 status 值
            valid_statuses = ["COMPLETED", "ERROR", "SAFETY_GATE_FAILED"]
            if data.get("status") not in valid_statuses:
                result["valid"] = False
                result["issues"].append(f"Invalid status: {data.get('status')}")

            # 检查时间戳格式
            if "timestamp" in data and data["timestamp"]:
                try:
                    datetime.fromisoformat(data["timestamp"])
                except:
                    result["issues"].append("Invalid timestamp format")

            return result

        except json.JSONDecodeError:
            result["valid"] = False
            result["issues"].append("Invalid JSON")
            return result
        except Exception as e:
            result["valid"] = False
            result["issues"].append(f"Error: {str(e)}")
            return result

    def validate_decision_file(self, decision_file: Path) -> dict:
        """验证决策文件"""
        result = {
            "file": decision_file.name,
            "valid": True,
            "issues": []
        }

        try:
            data = json.loads(decision_file.read_text())

            # 检查决策结构
            if "decision" not in data:
                result["valid"] = False
                result["issues"].append("Missing 'decision' object")
                return result

            decision = data["decision"]

            # 允许 None 决策（表示无交易信号）
            if decision is not None:
                if not isinstance(decision, dict):
                    result["valid"] = False
                    result["issues"].append("Decision must be dict or null")
                    return result

                # 检查决策字段
                if "action" in decision and decision["action"] not in [None, "ENTRY_SIMULATED", "SKIP"]:
                    result["issues"].append(f"Unknown action: {decision['action']}")

                # 如果有 P&L，检查其有效性
                if "simulated_pnl" in decision and decision["simulated_pnl"] is not None:
                    try:
                        float(decision["simulated_pnl"])
                    except:
                        result["valid"] = False
                        result["issues"].append("Invalid P&L value")

            return result

        except json.JSONDecodeError:
            result["valid"] = False
            result["issues"].append("Invalid JSON")
            return result
        except Exception as e:
            result["valid"] = False
            result["issues"].append(f"Error: {str(e)}")
            return result

    def validate_daily_samples(self, sample_date: date = None) -> dict:
        """验证某一天的所有采样"""
        if sample_date is None:
            sample_date = date.today()

        sample_dir = self.root / "logs/launchd_worker" / sample_date.isoformat()

        summary = {
            "date": sample_date.isoformat(),
            "total_samples": 0,
            "valid_samples": 0,
            "invalid_samples": 0,
            "issues": []
        }

        if not sample_dir.exists():
            self.log_audit("VALIDATION", f"No samples found for {sample_date}")
            return summary

        # 验证所有采样文件
        for sample_file in sorted(sample_dir.glob("pilot-*.json")):
            result = self.validate_sample_file(sample_file)
            summary["total_samples"] += 1

            if result["valid"]:
                summary["valid_samples"] += 1
            else:
                summary["invalid_samples"] += 1
                summary["issues"].extend(result["issues"])
                self.log_audit("INVALID_SAMPLE", f"{sample_file.name}: {'; '.join(result['issues'])}")

        # 验证所有决策文件
        for decision_file in sorted(sample_dir.glob("pilot-*.decision.json")):
            result = self.validate_decision_file(decision_file)

            if not result["valid"]:
                summary["invalid_samples"] += 1
                summary["issues"].extend(result["issues"])
                self.log_audit("INVALID_DECISION", f"{decision_file.name}: {'; '.join(result['issues'])}")

        # 记录摘要
        self.log_audit(
            "VALIDATION_SUMMARY",
            f"{summary['date']}: {summary['valid_samples']}/{summary['total_samples']} valid",
            **summary
        )

        return summary

    def detect_anomalies(self, sample_date: date = None) -> list:
        """检测数据异常"""
        if sample_date is None:
            sample_date = date.today()

        sample_dir = self.root / "logs/launchd_worker" / sample_date.isoformat()
        anomalies = []

        if not sample_dir.exists():
            return anomalies

        # 检查 1: 重复采样
        seen_times = {}
        for f in sample_dir.glob("pilot-*.json"):
            try:
                data = json.loads(f.read_text())
                timestamp = data.get("timestamp")
                symbol = data.get("symbol")

                key = (timestamp, symbol)
                if key in seen_times:
                    anomalies.append({
                        "type": "DUPLICATE_SAMPLE",
                        "files": [seen_times[key], f.name],
                        "details": f"Duplicate: {symbol} at {timestamp}"
                    })
                else:
                    seen_times[key] = f.name
            except:
                pass

        # 检查 2: 缺失采样
        expected_count = 8  # 一天应该有 8 个采样时间
        actual_count = len(list(sample_dir.glob("pilot-*.json")))

        if actual_count < expected_count * 0.7:
            anomalies.append({
                "type": "MISSING_SAMPLES",
                "expected": expected_count,
                "actual": actual_count,
                "details": f"Only {actual_count}/{expected_count} samples"
            })

        # 检查 3: P&L 异常
        total_pnl = 0.0
        for decision_file in sample_dir.glob("pilot-*.decision.json"):
            try:
                data = json.loads(decision_file.read_text())
                decision = data.get('decision', {})
                if decision and decision.get('action') == 'ENTRY_SIMULATED':
                    pnl = decision.get('simulated_pnl', 0)
                    total_pnl += pnl
            except:
                pass

        # 如果大幅亏损，记录异常
        if total_pnl < -500:
            anomalies.append({
                "type": "PNL_ANOMALY",
                "pnl": total_pnl,
                "details": f"Unusual loss: ${total_pnl:.2f}"
            })

        # 记录异常
        for anomaly in anomalies:
            self.log_audit("ANOMALY_DETECTED", anomaly.get("type"), **anomaly)

        return anomalies

    def generate_report(self, sample_date: date = None) -> dict:
        """生成完整的数据完整性报告"""
        if sample_date is None:
            sample_date = date.today()

        validation = self.validate_daily_samples(sample_date)
        anomalies = self.detect_anomalies(sample_date)

        report = {
            "report_date": datetime.now(SESSION_TIMEZONE).isoformat(),
            "sample_date": sample_date.isoformat(),
            "validation": validation,
            "anomalies": anomalies,
            "overall_status": "CLEAN" if validation["invalid_samples"] == 0 and not anomalies else "ISSUES_FOUND"
        }

        return report


def main():
    """运行数据完整性验证"""
    validator = DataIntegrityValidator()

    print("🔍 Data Integrity Validation")
    print("=" * 60)

    # 验证今天的采样
    report = validator.generate_report()

    print(f"\nValidation Report for {report['sample_date']}")
    print(f"Total samples: {report['validation']['total_samples']}")
    print(f"Valid: {report['validation']['valid_samples']}")
    print(f"Invalid: {report['validation']['invalid_samples']}")
    print(f"Anomalies: {len(report['anomalies'])}")
    print(f"Overall: {report['overall_status']}")

    if report['anomalies']:
        print("\nDetected anomalies:")
        for anomaly in report['anomalies']:
            print(f"  - {anomaly.get('type')}: {anomaly.get('details')}")

    return json.dumps(report, indent=2)


if __name__ == "__main__":
    result = main()
    print("\n" + result)
