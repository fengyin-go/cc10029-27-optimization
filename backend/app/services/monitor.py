"""运行监控 分析看板服务：汇总指标、运行分析并生成模拟结果。

监控完整度数据报告的写入点按运行监控既有实现落在本服务（与 run_analysis 同属运行入口）：
- 按期望观测窗口计算完整度，缺失观测、零流量、正常停机分别可识别，缺数不记零值；
- 报告与告警通知清单放进同一事务，报告失败时一起回滚；
- 同一请求标识只记一次，重复提交按成功回执返回；
- 迟到观测只回补对应窗口完整度，已确认的正常停机不能被改成异常；
- 空窗口同时收到迟到观测与任务停机时，只产出一个带最终原因的时间线版本；
- 回退报告或切换新版本都会改变当前数据版本，且输入输出字段保持不变。
"""
from __future__ import annotations

import copy
import hashlib
import random
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from app.services.alert import AlertService
from app.store import (
    ALERT_NOTIFICATION_TABLE,
    MONITOR_META_TABLE,
    MONITOR_RECEIPT_TABLE,
    MONITOR_REPORT_TABLE,
    store,
)

MODULE = "monitor"
REQUIRED_FIELDS = ["大盘编号", "关联任务", "采集速率"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

# 监控完整度数据报告领域常量
WINDOW_STATES = ["正常", "零流量", "缺失观测", "正常停机"]
DUTY_ROLES = {"值班管理员"}  # 只有值班管理员能写报告，权限不足的账户只能查看
WINDOWS_PER_DAY = 8  # 每个观测时段按 3 小时切一个期望观测窗口
TRANSACTION_TABLES = (
    MONITOR_REPORT_TABLE,
    ALERT_NOTIFICATION_TABLE,
    MONITOR_RECEIPT_TABLE,
    MONITOR_META_TABLE,
)


class CompletenessError(Exception):
    """监控完整度数据报告生成或事件入账失败：触发事务回滚。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MonitorService:
    def __init__(self) -> None:
        self._alerts = AlertService()

    def list_results(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        results: list[dict] = []
        for row in rows:
            d = dict(row)
            d["指标值"] = round(random.uniform(0.65, 0.98), 3)
            d["对比基准"] = round(random.uniform(0.60, 0.92), 3)
            d["差异"] = round(d["指标值"] - d["对比基准"], 3)
            results.append(d)
        return results

    def run_analysis(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        count = int(params.get("样本范围", params.get("并行数量", "6")))
        results: list[dict] = []
        for i in range(count):
            results.append({
                "id": i + 1,
                "分组": f"分组{(i%3)+1}",
                "指标值": round(random.uniform(0.62, 1.02), 3),
                "对比基准": round(random.uniform(0.58, 0.95), 3),
                "差异": round(random.uniform(-0.2, 0.3), 3),
            })
        return results

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ------------------------------------------------------------------
    # 监控完整度数据报告
    # ------------------------------------------------------------------

    def get_completeness(self) -> dict[str, Any]:
        """读取当前数据版本的报告；暂无数据或生成失败时给出空态，切换版本不影响返回字段。"""
        meta = self._meta()
        report = self._current_report()
        failed = meta["任务状态"] == "失败"
        return {
            "状态": "已生成" if report else ("失败" if failed else "暂无数据"),
            "空态": report is None,
            "失败原因": meta.get("失败原因"),
            "可重试": failed,
            "恢复说明": meta.get("恢复原因"),
            "当前版本": meta.get("当前版本") or None,
            "版本列表": [int(r["数据版本"]) for r in store.rows(MONITOR_REPORT_TABLE)],
            "报告": report,
        }

    def run_completeness(
        self, values: dict[str, Any], operator: str, role: str
    ) -> tuple[dict[str, Any] | None, str]:
        """生成报告（写入点）：幂等检查 → 同事务提交报告与告警通知清单 → 成功回执。"""
        if deny := self._deny(role):
            return None, deny
        request_id = str(values.get("请求标识") or "").strip()
        if not request_id:
            return None, "缺少请求标识：同一请求需携带固定标识，才能保证只记一次"
        for receipt in store.rows(MONITOR_RECEIPT_TABLE):
            if receipt["请求标识"] == request_id:
                return receipt, "同一请求已记录过，第二次提交按成功回执返回"
        self._meta()["最后参数"] = dict(values)
        return self._execute(values, operator, request_id)

    def retry_completeness(self, operator: str, role: str) -> tuple[dict[str, Any] | None, str]:
        """重试失败中的生成任务；恢复后保留中断原因说明。"""
        if deny := self._deny(role):
            return None, deny
        meta = self._meta()
        if meta["任务状态"] != "失败":
            return None, "当前没有失败中的生成任务，无需重试"
        params = dict(meta.get("最后参数") or {})
        if not params:
            return None, "没有可重试的生成参数"
        params["故障注入"] = "无"
        meta["恢复原因"] = f"中断恢复：{meta['失败原因']}"
        return self._execute(params, operator, str(params.get("请求标识") or ""))

    def apply_events(
        self, window_no: str, events: list[dict[str, Any]], operator: str, role: str
    ) -> tuple[dict[str, Any] | None, str]:
        """观测窗口事件入账：迟到观测、任务停机，或二者同时到达的冲突批次。"""
        if deny := self._deny(role):
            return None, deny
        report = self._current_report()
        if report is None:
            return None, "暂无监控完整度数据报告，请先生成"
        window = next((w for w in report["windows"] if w["窗口编号"] == window_no), None)
        if window is None:
            return None, f"观测窗口 {window_no} 不存在"
        if not events:
            return None, "事件列表为空"
        kinds = [str(e.get("事件类型") or "") for e in events]

        def work() -> None:
            if "迟到观测" in kinds and "任务停机" in kinds and window["窗口状态"] == "缺失观测":
                self._resolve_conflict(report, window, events, operator)
            else:
                for event in events:
                    self._apply_event(report, window, event, operator)
            self._summarize(report)

        try:
            with self._transaction():
                work()
        except CompletenessError as exc:
            return None, str(exc)
        return report, f"窗口 {window_no} 事件已入账，调用方只得到一个带最终原因的时间线版本"

    def switch_version(
        self, action: str, operator: str, role: str
    ) -> tuple[dict[str, Any] | None, str]:
        """回退监控完整度数据报告或切换新版本：两种做法都改变当前数据版本。"""
        if deny := self._deny(role):
            return None, deny
        versions = sorted(int(r["数据版本"]) for r in store.rows(MONITOR_REPORT_TABLE))
        if not versions:
            return None, "暂无数据版本可切换"
        current = int(self._meta().get("当前版本") or versions[-1])
        if action == "回退":
            older = [v for v in versions if v < current]
            if not older:
                return None, "已是最早版本，无法回退"
            target = max(older)
        elif action == "切换":
            newer = [v for v in versions if v > current]
            if not newer:
                return None, "已是最新版本"
            target = min(newer)
        else:
            return None, f"不支持的操作「{action}」，仅支持回退或切换"
        self._meta()["当前版本"] = target
        return self._current_report(), f"已{action}至数据版本 V{target}，输入输出字段保持不变"

    def verify(self, operator: str, role: str) -> tuple[dict[str, Any] | None, str]:
        """值班管理员核对完整度；权限不足的账户只能查看。"""
        if deny := self._deny(role):
            return None, deny
        report = self._current_report()
        if report is None:
            return None, "暂无监控完整度数据报告，无法核对"
        report["核对人"] = operator
        report["核对时间"] = _now()
        report["时间线"].append({
            "时间": _now(),
            "事件": "值班核对",
            "数据版本": report["数据版本"],
            "最终原因": f"{operator} 核对完整度通过",
        })
        return report, f"{operator} 已核对数据版本 V{report['数据版本']} 的完整度"

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _deny(self, role: str) -> str | None:
        if role not in DUTY_ROLES:
            return "权限不足：仅值班管理员可执行该操作，当前账户只能查看"
        return None

    def _meta(self) -> dict[str, Any]:
        rows = store.rows(MONITOR_META_TABLE)
        if not rows:
            rows.append({"当前版本": 0, "任务状态": "空闲", "失败原因": None, "恢复原因": None, "最后参数": None})
        return rows[0]

    def _current_report(self) -> dict[str, Any] | None:
        current = int(self._meta().get("当前版本") or 0)
        for row in store.rows(MONITOR_REPORT_TABLE):
            if int(row["数据版本"]) == current:
                return row
        return None

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """报告与告警通知清单的同一事务：任何一步失败都整体回滚。"""
        backup = {name: copy.deepcopy(store.rows(name)) for name in TRANSACTION_TABLES}
        try:
            yield
        except Exception:
            for name, rows in backup.items():
                store.rows(name)[:] = rows
            raise

    def _execute(
        self, values: dict[str, Any], operator: str, request_id: str
    ) -> tuple[dict[str, Any] | None, str]:
        recovery = self._meta().get("恢复原因")
        try:
            with self._transaction():
                if str(values.get("故障注入") or "无") == "采集中断":
                    raise CompletenessError("采集链路中断，观测数据未送达")
                report = self._build_report(operator, recovery)
                notifications = self._alerts.build_notifications(report)
                # 新版本生效，上一版仍在待处理的通知归档为历史
                for item in store.rows(ALERT_NOTIFICATION_TABLE):
                    if item["通知状态"] == "待处理":
                        item["通知状态"] = "已归档"
                        item["更新时间"] = _now()
                store.rows(MONITOR_REPORT_TABLE).append(report)
                store.rows(ALERT_NOTIFICATION_TABLE).extend(notifications)
                receipt = {
                    "请求标识": request_id,
                    "回执编号": f"RCPT-{len(store.rows(MONITOR_RECEIPT_TABLE)) + 1:04d}",
                    "结果": "成功",
                    "报告编号": report["报告编号"],
                    "数据版本": report["数据版本"],
                    "登记时间": _now(),
                }
                store.rows(MONITOR_RECEIPT_TABLE).append(receipt)
                self._meta()["当前版本"] = report["数据版本"]
            meta = self._meta()
            meta["任务状态"] = "成功"
            meta["失败原因"] = None
            return receipt, f"监控完整度数据报告 V{report['数据版本']} 已生成，告警通知清单已同步"
        except CompletenessError as exc:
            meta = self._meta()
            meta["任务状态"] = "失败"
            meta["失败原因"] = str(exc)
            return None, f"监控完整度数据报告生成失败：{exc}；报告与告警通知清单已一起回滚"

    def _build_report(self, operator: str, recovery: str | None) -> dict[str, Any]:
        rows = store.rows(MODULE)
        if not rows:
            raise CompletenessError("没有可观测的监控大盘")
        version = max((int(r["数据版本"]) for r in store.rows(MONITOR_REPORT_TABLE)), default=0) + 1
        windows: list[dict[str, Any]] = []
        days: list[str] = []
        for row in rows:
            day = str(row.get("观测时段") or "").strip()
            if day:
                days.append(day)
            windows.extend(self._build_windows(row, day))
        timeline = [{
            "时间": _now(),
            "事件": "报告生成",
            "数据版本": version,
            "最终原因": f"按期望观测窗口计算完整度，缺数不记零值，版本 V{version}",
        }]
        if recovery:
            timeline.append({"时间": _now(), "事件": "中断恢复", "数据版本": version, "最终原因": recovery})
        report = {
            "id": version,
            "报告编号": f"COMP-{version:04d}",
            "数据版本": version,
            "状态": "已生成",
            "观测时段范围": f"{min(days)} ~ {max(days)}" if days else "—",
            "生成时间": _now(),
            "生成人": operator,
            "恢复说明": recovery,
            "核对人": None,
            "核对时间": None,
            "windows": windows,
            "时间线": timeline,
            "汇总": {},
        }
        self._summarize(report)
        return report

    def _build_windows(self, row: dict[str, Any], day: str) -> list[dict[str, Any]]:
        try:
            base = date.fromisoformat(day)
        except ValueError:
            base = date.today()
            day = base.isoformat()
        board = str(row.get("大盘编号") or f"MONI-{int(row.get('id', 0)):04d}")
        windows: list[dict[str, Any]] = []
        for i in range(WINDOWS_PER_DAY):
            start = datetime(base.year, base.month, base.day) + timedelta(hours=3 * i)
            end = start + timedelta(hours=3)
            window_no = f"WIN-{int(row.get('id', 0)):04d}-{i + 1:02d}"
            state, rate, reason = self._simulate(board, window_no)
            received = (
                (end - timedelta(minutes=5)).isoformat(timespec="minutes")
                if state in ("正常", "零流量")
                else None
            )
            windows.append({
                "窗口编号": window_no,
                "大盘编号": board,
                "期望观测窗口": f"{day} {3 * i:02d}:00-{3 * (i + 1):02d}:00",
                "期望开始": start.isoformat(timespec="minutes"),
                "期望结束": end.isoformat(timespec="minutes"),
                "实际收到时间": received,
                "窗口状态": state,
                "观测速率": rate,
                "完整度": 0.0 if state == "缺失观测" else 1.0,
                "确认人": "系统计划" if state == "正常停机" else None,
                "原因说明": reason,
            })
        return windows

    @staticmethod
    def _simulate(board: str, window_no: str) -> tuple[str, int | None, str]:
        """确定性模拟窗口实况：缺失观测的速率保持 None（缺数不记零值）。"""
        digest = hashlib.md5(f"{board}|{window_no}".encode("utf-8")).hexdigest()
        r = int(digest[:8], 16) % 100
        if r < 55:
            return "正常", 20 + r % 61, "观测正常"
        if r < 70:
            return "零流量", 0, "窗口内无采集任务，流量为零（非缺数）"
        if r < 88:
            return "缺失观测", None, "观测数据未送达，待确认（缺数不记零）"
        return "正常停机", None, "计划内停机维护（已确认）"

    @staticmethod
    def _late_rate(window_no: str) -> int:
        digest = hashlib.md5(f"late|{window_no}".encode("utf-8")).hexdigest()
        r = int(digest[:8], 16) % 10
        return 0 if r == 0 else 15 + r * 7

    def _apply_event(
        self, report: dict[str, Any], window: dict[str, Any], event: dict[str, Any], operator: str
    ) -> None:
        kind = str(event.get("事件类型") or "")
        if kind == "迟到观测":
            self._apply_late(report, window, event)
        elif kind == "任务停机":
            self._apply_shutdown(report, window, event, operator)
        else:
            raise CompletenessError(f"未知事件类型「{kind}」")

    def _apply_late(self, report: dict[str, Any], window: dict[str, Any], event: dict[str, Any]) -> None:
        received = str(event.get("实际收到时间") or _now())
        rate = event.get("观测速率")
        if rate is None:
            rate = self._late_rate(window["窗口编号"])
        if window["窗口状态"] == "正常停机":
            # 已经确认的正常停机不能被改成异常：迟到数据只回补完整度
            window["实际收到时间"] = received
            window["观测速率"] = rate
            window["完整度"] = 1.0
            window["原因说明"] = f"{window['原因说明']}；迟到观测已回补完整度，正常停机状态不变"
            report["时间线"].append({
                "时间": _now(),
                "事件": "迟到观测回补",
                "数据版本": report["数据版本"],
                "最终原因": f"窗口 {window['窗口编号']} 已确认正常停机，迟到数据仅回补完整度，不改为异常",
            })
            return
        if window["窗口状态"] != "缺失观测":
            report["时间线"].append({
                "时间": _now(),
                "事件": "迟到观测忽略",
                "数据版本": report["数据版本"],
                "最终原因": f"窗口 {window['窗口编号']} 状态为{window['窗口状态']}，无需回补",
            })
            return
        window["实际收到时间"] = received
        window["观测速率"] = rate
        window["完整度"] = 1.0
        window["窗口状态"] = "零流量" if int(rate) == 0 else "正常"
        window["原因说明"] = "迟到观测已补到：零流量（非缺数）" if int(rate) == 0 else "迟到观测已补到，窗口恢复正常"
        report["时间线"].append({
            "时间": _now(),
            "事件": "迟到观测补到",
            "数据版本": report["数据版本"],
            "最终原因": f"缺失窗口 {window['窗口编号']} 由迟到数据恢复为{window['窗口状态']}，关联告警通知标记已恢复",
        })
        self._alerts.sync_window_notifications(report, window["窗口编号"], "已恢复", "迟到观测补到，窗口恢复")

    def _apply_shutdown(
        self, report: dict[str, Any], window: dict[str, Any], event: dict[str, Any], operator: str
    ) -> None:
        reason = str(event.get("原因") or "计划内停机维护")
        if window["窗口状态"] == "正常停机":
            report["时间线"].append({
                "时间": _now(),
                "事件": "任务停机重复",
                "数据版本": report["数据版本"],
                "最终原因": f"窗口 {window['窗口编号']} 已是确认停机，保持原确认不变",
            })
            return
        if window["窗口状态"] != "缺失观测":
            report["时间线"].append({
                "时间": _now(),
                "事件": "任务停机忽略",
                "数据版本": report["数据版本"],
                "最终原因": f"窗口 {window['窗口编号']} 已有观测数据，无需确认停机",
            })
            return
        window["窗口状态"] = "正常停机"
        window["确认人"] = operator
        window["完整度"] = 1.0
        window["原因说明"] = f"{reason}（已确认）"
        report["时间线"].append({
            "时间": _now(),
            "事件": "任务停机确认",
            "数据版本": report["数据版本"],
            "最终原因": f"空窗口 {window['窗口编号']} 确认为正常停机，不视为缺失，关联告警通知取消",
        })
        self._alerts.sync_window_notifications(report, window["窗口编号"], "已取消", "确认为正常停机")

    def _resolve_conflict(
        self, report: dict[str, Any], window: dict[str, Any], events: list[dict[str, Any]], operator: str
    ) -> None:
        """空窗口同时收到迟到观测与任务停机：按任务停机定版，迟到观测仅回补完整度。"""
        late = next((e for e in events if e.get("事件类型") == "迟到观测"), {})
        shutdown = next((e for e in events if e.get("事件类型") == "任务停机"), {})
        rate = late.get("观测速率")
        if rate is None:
            rate = self._late_rate(window["窗口编号"])
        reason = str(shutdown.get("原因") or "计划内停机维护")
        window["实际收到时间"] = str(late.get("实际收到时间") or _now())
        window["观测速率"] = rate
        window["完整度"] = 1.0
        window["窗口状态"] = "正常停机"
        window["确认人"] = operator
        window["原因说明"] = f"{reason}（已确认）；状态冲突已定版：迟到观测仅回补完整度"
        report["时间线"].append({
            "时间": _now(),
            "事件": "状态冲突定版",
            "数据版本": report["数据版本"],
            "最终原因": (
                f"空窗口 {window['窗口编号']} 同时收到迟到观测与任务停机，"
                "按任务停机定版为正常停机，迟到观测仅回补完整度"
            ),
        })
        self._alerts.sync_window_notifications(report, window["窗口编号"], "已取消", "空窗口确认为正常停机，告警通知取消")

    @staticmethod
    def _summarize(report: dict[str, Any]) -> None:
        windows = report["windows"]
        summary: dict[str, Any] = {state: 0 for state in WINDOW_STATES}
        for window in windows:
            summary[window["窗口状态"]] += 1
        summary["期望窗口数"] = len(windows)
        summary["整体完整度"] = (
            round(sum(float(w["完整度"]) for w in windows) / len(windows), 3) if windows else 0.0
        )
        report["汇总"] = summary
