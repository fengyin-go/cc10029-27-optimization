"""告警中心业务规则：状态流转、字段校验与筛选口径都收在这里。

告警通知清单由监控完整度数据报告驱动：告警中心读取报告，只针对被确认为
缺失或异常的观测窗口生成通知；零流量与正常停机可识别但不触发通知。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import ALERT_NOTIFICATION_TABLE, MONITOR_META_TABLE, MONITOR_REPORT_TABLE, store

MODULE = "alert"
LIST_FIELDS = ["规则编号", "监控对象", "告警条件", "通知渠道", "通知模板", "重复间隔", "生效时段", "值班人员"]
REQUIRED_FIELDS = ["规则编号", "监控对象", "告警条件"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

# 只有被确认为缺失或异常的观测窗口才允许生成告警通知
NOTIFIABLE_STATES = {"缺失观测", "异常"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class AlertService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"告警规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于告警中心可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"告警规则已{action}"

    # ------------------------------------------------------------------
    # 告警通知清单（读取监控完整度数据报告生成）
    # ------------------------------------------------------------------

    def build_notifications(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """读取监控完整度数据报告，只针对被确认为缺失或异常的观测窗口生成通知。"""
        rows = store.rows(ALERT_NOTIFICATION_TABLE)
        seq = max((int(n.get("id", 0)) for n in rows), default=0)
        notifications: list[dict[str, Any]] = []
        for window in report.get("windows", []):
            if window.get("窗口状态") not in NOTIFIABLE_STATES:
                continue
            seq += 1
            notifications.append({
                "id": seq,
                "通知编号": f"NOTI-{seq:04d}",
                "来源报告": report["报告编号"],
                "数据版本": report["数据版本"],
                "报告状态": report["状态"],
                "窗口编号": window["窗口编号"],
                "观测时段": window["期望观测窗口"],
                "告警类型": window["窗口状态"],
                "通知状态": "待处理",
                "原因说明": window.get("原因说明"),
                "生成时间": _now(),
                "更新时间": None,
            })
        return notifications

    def list_notifications(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(ALERT_NOTIFICATION_TABLE)
        if status:
            rows = [n for n in rows if n.get("通知状态") == status]
        return sorted(rows, key=lambda n: int(n.get("id", 0)), reverse=True)

    def sync_window_notifications(
        self, report: dict[str, Any], window_no: str, new_status: str, reason: str
    ) -> None:
        """窗口状态变化后同步通知：迟到补到标记已恢复，确认停机标记已取消。"""
        for item in store.rows(ALERT_NOTIFICATION_TABLE):
            if (
                item.get("来源报告") == report["报告编号"]
                and item.get("窗口编号") == window_no
                and item.get("通知状态") == "待处理"
            ):
                item["通知状态"] = new_status
                item["原因说明"] = reason
                item["更新时间"] = _now()

    def completeness_status(self) -> dict[str, Any]:
        """监控完整度数据报告状态落到告警中心：清单响应里携带报告状态与数据版本。"""
        meta_rows = store.rows(MONITOR_META_TABLE)
        current = int(meta_rows[0].get("当前版本") or 0) if meta_rows else 0
        report = next(
            (r for r in store.rows(MONITOR_REPORT_TABLE) if int(r.get("数据版本", 0)) == current),
            None,
        )
        return {
            "报告状态": report["状态"] if report else "暂无数据",
            "数据版本": current or None,
            "报告编号": report["报告编号"] if report else None,
        }
