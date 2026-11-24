"""场站下线/上线与开充闸。

全局口径（臂A：以下线后禁新开充为准，全库不混用另一臂）：
- 下线后该站开充闸关闭（禁新开），拒绝一切新开充；
- 已有会话的记脉冲、结算关账照常（在充收口不冻结）；
- 闸态物化在 sites.active，事件源在 site_events；
  任意时刻的闸判定必须能由事件流重放复算，且与物化闸一致；
- 下线/上线不是只改启用字：校验迁移、追加事件（单调闸序+在充快照）、
  翻启用字、出回执，四者同一事务。
"""
from __future__ import annotations
import json

from fastapi import HTTPException

from app.db import row_dict
from app.services.core import now_str

# 未结会话口径：这些状态的会话仍需记脉冲/关账收口，下线不得误挡
UNCLOSED = ("充电中", "结算中", "已超时")

闸开 = "开充中"
闸关 = "禁新开"


def _site(conn, site_id: int):
    s = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if not s:
        raise HTTPException(404, detail="站点不存在")
    return s


def gate_label(is_open: bool) -> str:
    return 闸开 if is_open else 闸关


def gate_seq(conn, site_id: int) -> int:
    """当前闸序：该站已发生下线/上线事件数，单调递增。"""
    r = conn.execute(
        "SELECT COALESCE(MAX(seq),0) AS q FROM site_events WHERE site_id=?",
        (site_id,),
    ).fetchone()
    return int(r["q"])


def gate_open(conn, site_id: int) -> bool:
    """物化闸（开充写口判定走这里，须在写事务内调用）。"""
    return bool(_site(conn, site_id)["active"])


def replay_gate(conn, site_id: int, upto_seq: int | None = None) -> bool:
    """由下线事件重放闸态：建站默认开，按闸序折叠 下线/上线。"""
    sql = "SELECT action FROM site_events WHERE site_id=?"
    args: list = [site_id]
    if upto_seq is not None:
        sql += " AND seq<=?"
        args.append(upto_seq)
    sql += " ORDER BY seq"
    is_open = True
    for r in conn.execute(sql, args).fetchall():
        if r["action"] == "下线":
            is_open = False
        elif r["action"] == "上线":
            is_open = True
    return is_open


def gate_status(conn, site_id: int) -> dict:
    """闸态对照：物化闸 vs 事件重放闸，二者不一致即闸态拧花。"""
    s = _site(conn, site_id)
    material = bool(s["active"])
    replayed = replay_gate(conn, site_id)
    return {
        "站点编号": site_id,
        "站点": s["name"],
        "闸": gate_label(material),
        "物化闸": gate_label(material),
        "重放闸": gate_label(replayed),
        "一致": material == replayed,
        "闸序": gate_seq(conn, site_id),
    }


def _unclosed_sessions(conn, site_id: int) -> list[dict]:
    marks = ",".join("?" * len(UNCLOSED))
    rs = conn.execute(
        f"SELECT id, slip_no, status FROM sessions WHERE site_id=? AND status IN ({marks}) ORDER BY id",
        (site_id, *UNCLOSED),
    ).fetchall()
    return [{"会话编号": r["id"], "会话号": r["slip_no"], "状态": r["status"]} for r in rs]


def append_event(conn, site_id: int, action: str, note: str = "") -> tuple[int, list[dict], str]:
    """追加一条站点事件（闸序单调+在充快照），须在写事务内调用。"""
    seq = gate_seq(conn, site_id) + 1
    open_list = _unclosed_sessions(conn, site_id)
    ts = now_str()
    conn.execute(
        """INSERT INTO site_events(site_id,action,seq,open_sessions,open_slips,note,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            site_id,
            action,
            seq,
            len(open_list),
            json.dumps([s["会话号"] for s in open_list], ensure_ascii=False),
            note,
            ts,
        ),
    )
    return seq, open_list, ts


def _receipt(action: str, site, seq: int, open_list: list[dict], ts: str, note: str) -> dict:
    return {
        "回执": action,
        "站点编号": site["id"],
        "站点": site["name"],
        "闸": 闸关 if action == "下线" else 闸开,
        "闸序": seq,
        "时间": ts,
        "在充会话数": len(open_list),
        "在充会话": open_list,
        "备注": note,
    }


def offline_site(conn, site_id: int, note: str = "") -> dict:
    """下线：禁新开（闸关），在充会话留待照常记脉冲与关账收口。"""
    s = _site(conn, site_id)
    if not s["active"]:
        raise HTTPException(400, detail="站点已下线，勿重复下线")
    seq, open_list, ts = append_event(conn, site_id, "下线", note)
    conn.execute("UPDATE sites SET active=0 WHERE id=?", (site_id,))
    return _receipt("下线", row_dict(s), seq, open_list, ts, note)


def online_site(conn, site_id: int, note: str = "") -> dict:
    """上线恢复：闸重开，恢复新开充；事件流同步追加，重放口径不拧花。"""
    s = _site(conn, site_id)
    if s["active"]:
        raise HTTPException(400, detail="站点在线，勿重复上线")
    seq, open_list, ts = append_event(conn, site_id, "上线", note)
    conn.execute("UPDATE sites SET active=1 WHERE id=?", (site_id,))
    return _receipt("上线", row_dict(s), seq, open_list, ts, note)


def list_site_events(conn, site_id: int) -> list[dict]:
    """站点审计流水：每条事件即一张回执，可与站点页/会话册同刷对照。"""
    _site(conn, site_id)
    rs = conn.execute(
        "SELECT * FROM site_events WHERE site_id=? ORDER BY seq", (site_id,)
    ).fetchall()
    out = []
    for r in rs:
        out.append(
            {
                "编号": r["id"],
                "动作": r["action"],
                "闸序": r["seq"],
                "在充会话数": r["open_sessions"],
                "在充会话": json.loads(r["open_slips"] or "[]"),
                "备注": r["note"],
                "时间": r["created_at"],
            }
        )
    return out
