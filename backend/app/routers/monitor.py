"""运行监控 分析看板接口：汇总指标、参数运行与结果导出。

监控完整度数据报告的写入点按既有实现落在本模块的运行入口：
/api/monitor/completeness/run 与既有 /api/monitor/run 同属运行监控的运行动作。
"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    CompletenessEventPayload,
    CompletenessRunPayload,
    EntryPayload,
    OperatorPayload,
    VersionSwitchPayload,
)
from app.services.monitor import MonitorService

router = APIRouter(prefix="/api/monitor", tags=["运行监控"])
service = MonitorService()
LIST_FIELDS = ["大盘编号", "关联任务", "采集速率", "成功率", "平均延迟", "内存占用", "错误次数", "观测时段"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return {"metrics": [
        {"label": "覆盖数据源", "value": random.randint(12, 45), "trend": random.randint(-12, 25)},
        {"label": "最新指标值", "value": round(random.uniform(0.72, 0.99), 3), "trend": random.randint(-8, 15)},
        {"label": "异常记录", "value": random.randint(0, 8), "trend": -random.randint(1, 10)},
    ]}


@router.get("")
def list_results() -> dict[str, Any]:
    return {"items": service.list_results()}


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=monitor.csv"})


@router.get("/completeness")
def get_completeness() -> dict[str, Any]:
    """读取当前数据版本的监控完整度数据报告；暂无数据或生成失败时返回空态与失败原因。"""
    return service.get_completeness()


@router.post("/completeness/run", response_model=ActionResult)
def run_completeness(payload: CompletenessRunPayload) -> ActionResult:
    """生成监控完整度数据报告：同一请求标识只记一次，报告与告警通知清单同一事务。"""
    receipt, message = service.run_completeness(payload.values, payload.operator, payload.role)
    if receipt is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=receipt)


@router.post("/completeness/retry", response_model=ActionResult)
def retry_completeness(payload: OperatorPayload) -> ActionResult:
    """重试失败中的生成任务；中断恢复后保留原因说明。"""
    receipt, message = service.retry_completeness(payload.operator, payload.role)
    if receipt is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=receipt)


@router.post("/completeness/events", response_model=ActionResult)
def apply_events(payload: CompletenessEventPayload) -> ActionResult:
    """观测窗口事件入账：迟到观测只回补完整度；冲突批次只返回一个带最终原因的时间线版本。"""
    report, message = service.apply_events(
        payload.window, payload.events, payload.operator, payload.role)
    if report is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=report)


@router.post("/completeness/version", response_model=ActionResult)
def switch_version(payload: VersionSwitchPayload) -> ActionResult:
    """回退监控完整度数据报告或切换新版本：两种做法都改变当前数据版本。"""
    report, message = service.switch_version(payload.action, payload.operator, payload.role)
    if report is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=report)


@router.post("/completeness/verify", response_model=ActionResult)
def verify_completeness(payload: OperatorPayload) -> ActionResult:
    """值班管理员核对完整度；权限不足的账户只能查看。"""
    report, message = service.verify(payload.operator, payload.role)
    if report is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=report)


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"监控大盘 {entry_id} 不存在")
    return entry
