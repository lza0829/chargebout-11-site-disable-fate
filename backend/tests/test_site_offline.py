"""场站下线写范围夹具（臂A：以下线后禁新开充为准）。

覆盖四类缺陷情形的复现与对照：
1. 下线后仍新开 —— 真实实现须拒；夹具另复现"检查与写入分离"时确实漏网；
2. 下线瞬间挡死在充关账 —— 臂A下在充记脉冲/关账必须照常；
3. 下线与开充并发半截会话 —— BEGIN IMMEDIATE 串行后不得出现；
4. 上线恢复后闸态拧花 —— 物化闸与事件重放必须始终一致。
"""
from __future__ import annotations
import re
import sqlite3
import threading

import pytest
from fastapi import HTTPException

from app.db import connect, db
from app.services import core
from app.services import sessions as sess_svc
from app.services import sites as site_svc


def _open(conn, pile):
    return sess_svc.open_session(conn, pile)


# ---------- 1. 臂A主口径：下线后禁新开，在充收口照常 ----------

def test_下线后禁新开而在充记脉冲关账照常(base):
    site = base["甲站"]
    with db(immediate=True) as conn:
        r = _open(conn, base["甲一"])
    with db(immediate=True) as conn:
        receipt = site_svc.offline_site(conn, site, "检修")
        assert receipt["闸"] == "禁新开"
    with db(immediate=True) as conn:
        with pytest.raises(HTTPException) as ei:
            _open(conn, base["甲二"])
        assert ei.value.status_code == 400
        assert "禁止新开充" in ei.value.detail
        # 在充会话不被误挡：脉冲、关账照常收口
        sess_svc.add_pulse(conn, r["会话编号"], 1.5, 7)
        out = sess_svc.settle(conn, r["会话编号"])
        assert out["状态"] == "已关账"
    with db() as conn:
        d = core.session_detail(conn, r["会话编号"])
        assert d["status"] == "已关账" and d["kwh"] == 1.5
        # 桩位枪线已随关账释放，无半截占用
        assert conn.execute(
            "SELECT status FROM piles WHERE id=?", (base["甲一"],)
        ).fetchone()["status"] == "空闲"


def test_他站下线不误挡本站关账(base):
    """未下线却误挡关账的对照：闸是分站口径，乙站下线与甲站无关。"""
    with db(immediate=True) as conn:
        r = _open(conn, base["甲一"])
        site_svc.offline_site(conn, base["乙站"])
        sess_svc.add_pulse(conn, r["会话编号"], 1.0, 7)
        out = sess_svc.settle(conn, r["会话编号"])
        assert out["状态"] == "已关账"
    with db() as conn:
        st = site_svc.gate_status(conn, base["甲站"])
        assert st["闸"] == "开充中" and st["一致"]


# ---------- 2. 下线回执 / 站点页 / 会话册 / 总览 同刷对照 ----------

def test_下线回执与站点页会话册总览同刷对照(base):
    site = base["甲站"]
    with db(immediate=True) as conn:
        r1 = _open(conn, base["甲一"])
        r2 = _open(conn, base["甲二"])
        sess_svc.add_pulse(conn, r1["会话编号"], 2.0, 7)
        receipt = site_svc.offline_site(conn, site, "检修")
    assert receipt["回执"] == "下线" and receipt["闸"] == "禁新开"
    assert receipt["在充会话数"] == 2
    assert {s["会话号"] for s in receipt["在充会话"]} == {r1["会话号"], r2["会话号"]}

    with db() as conn:
        # 站点页同刷：闸禁新开、在充 2
        srow = next(s for s in core.list_sites(conn) if s["id"] == site)
        assert srow["gate"] == "禁新开" and srow["open_sessions"] == 2
        # 会话册同刷：两笔仍在充、站点已标下线，可与回执逐笔对照
        book = {s["slip_no"]: s for s in core.list_sessions(conn)}
        for r in (r1, r2):
            assert book[r["会话号"]]["status"] == "充电中"
            assert book[r["会话号"]]["site_active"] == 0
        # 审计流水中的下线事件即回执存档，字段一致
        ev = site_svc.list_site_events(conn, site)[-1]
        assert ev["动作"] == "下线" and ev["在充会话数"] == 2
        assert set(ev["在充会话"]) == {r1["会话号"], r2["会话号"]}
        # 总览同刷：禁新开可见、下线站在充 2
        o = core.overview(conn)
        assert o["禁新开站点"] == 1 and "甲站" in o["禁新开站点名"]
        assert o["下线站在充"] == 2
        assert site_svc.gate_status(conn, site)["一致"]

    # 在充收口后各面同刷归零
    with db(immediate=True) as conn:
        sess_svc.add_pulse(conn, r2["会话编号"], 1.0, 7)
        sess_svc.settle(conn, r1["会话编号"])
        sess_svc.settle(conn, r2["会话编号"])
    with db() as conn:
        o = core.overview(conn)
        assert o["下线站在充"] == 0 and o["禁新开站点"] == 1
        srow = next(s for s in core.list_sites(conn) if s["id"] == site)
        assert srow["open_sessions"] == 0


# ---------- 3. 半截会话：朴素写法复现缺陷，串行写事务根除 ----------

def test_夹具复现_检查与写入分离则下线瞬间开出半截会话(base):
    """旧式朴素写法：查闸在事务外（自动提交），写时才开事务 —— 查与写不原子。
    下线在查与写之间落账 → 站已下线仍开出新充（缺陷复现）。"""
    site = base["甲站"]
    a = connect(timeout=0)
    a.isolation_level = None  # 自动提交：SELECT 不持锁，复刻旧代码的查闸方式
    try:
        assert a.execute(
            "SELECT active FROM sites WHERE id=?", (site,)
        ).fetchone()["active"] == 1  # 闸开（过期判定）
        # 另一事务此刻完成下线（只改启用字）
        with db(immediate=True) as conn:
            conn.execute("UPDATE sites SET active=0 WHERE id=?", (site,))
        # A 仍按过期判定写入 → 半截会话漏网
        a.execute("BEGIN IMMEDIATE")
        a.execute(
            """INSERT INTO sessions(slip_no,site_id,pile_id,gun_id,rate_plan_id,
                                    business_day,status,kwh,amount,price_locked,started_at,note)
               VALUES('充漏',?,?,1,1,'2026-11-24','充电中',0,0,1,'2026-11-24 10:00:00','')""",
            (site, base["甲一"]),
        )
        a.execute("COMMIT")
    finally:
        a.close()
    with db() as conn:
        leaked = conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE site_id=?", (site,)
        ).fetchone()["c"]
        assert leaked == 1  # 缺陷确被夹具复现
        assert site_svc.gate_open(conn, site) is False  # 闸已关却仍开出新充


def test_写事务串行后下线与开充无半截(base):
    """BEGIN IMMEDIATE：开充判定与写入持锁同事务，下线挤不进来。"""
    site = base["甲站"]
    a = connect(timeout=0)
    a.isolation_level = None
    a.execute("BEGIN IMMEDIATE")
    r = sess_svc.open_session(a, base["甲一"])  # 真实开充口：闸判定+写入
    b = connect(timeout=0)
    b.isolation_level = None
    with pytest.raises(sqlite3.OperationalError):
        b.execute("BEGIN IMMEDIATE")  # 下线事务被锁挡在门外
    b.close()
    a.commit()
    a.close()
    # 开充先落账：下线回执的在充快照必须看见这笔（同刷对照）
    with db(immediate=True) as conn:
        receipt = site_svc.offline_site(conn, site)
    assert receipt["在充会话数"] == 1
    assert receipt["在充会话"][0]["会话号"] == r["会话号"]
    # 反向顺序：下线先落账，则开充被闸当场挡下
    with db(immediate=True) as conn:
        with pytest.raises(HTTPException) as ei:
            _open(conn, base["甲二"])
        assert "禁止新开充" in ei.value.detail


def test_并发下线开充不产半截会话(base):
    """多线程压力：下线/上线循环 vs 反复开充，事后逐笔重放校验。
    注：本环境单笔提交约 200ms（fsync 慢），cycler 让出锁窗口以模拟真实节奏。"""
    import time

    site = base["甲站"]
    piles = [base["甲一"], base["甲二"]]
    slips = []
    errors = []
    stop = threading.Event()

    def cycler():
        while not stop.is_set():
            try:
                with db(immediate=True) as conn:
                    site_svc.offline_site(conn, site)
                time.sleep(0.03)  # 让出锁窗口：下线/上线本是低频管理操作
                with db(immediate=True) as conn:
                    site_svc.online_site(conn, site)
                time.sleep(0.03)
            except HTTPException:
                pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    def opener(rounds):
        for i in range(rounds):
            try:
                with db(immediate=True) as conn:
                    r = sess_svc.open_session(conn, piles[i % 2])
                    slips.append(r["会话号"])
                    sess_svc.settle(conn, r["会话编号"])  # 同事务关账释放桩位
            except HTTPException:
                pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    t0 = threading.Thread(target=cycler)
    ts = [threading.Thread(target=opener, args=(8,)) for _ in range(2)]
    t0.start()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    stop.set()
    t0.join()

    assert not errors, f"并发期出现非业务异常: {errors}"
    with db() as conn:
        # 闸态不拧花：物化闸与事件重放一致
        assert site_svc.gate_status(conn, site)["一致"]
        # 每笔开充锚定的闸序重放都必须是闸开 → 无一笔半截漏网
        for e in conn.execute(
            "SELECT note FROM session_events WHERE action='开会话'"
        ).fetchall():
            m = re.search(r"闸序(\d+)", e["note"])
            assert m, "开会话事件须锚定闸序"
            assert site_svc.replay_gate(conn, site, int(m.group(1))) is True
        # 无半截占用：全部会话已关账、桩位全空闲
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE status!='已关账'"
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM piles WHERE status!='空闲'"
        ).fetchone()["c"] == 0


# ---------- 4. 上线恢复与闸态拧花 ----------

def test_上线恢复后闸态不拧花(base):
    site = base["甲站"]

    def check(expect_open: bool):
        with db() as conn:
            st = site_svc.gate_status(conn, site)
            assert st["一致"], f"闸态拧花: {st}"
            assert (st["闸"] == "开充中") == expect_open

    check(True)
    with db(immediate=True) as conn:
        site_svc.offline_site(conn, site)
    check(False)
    with db(immediate=True) as conn:
        rc = site_svc.online_site(conn, site, "恢复")
        assert rc["闸"] == "开充中"
    check(True)
    with db(immediate=True) as conn:  # 恢复后可新开
        r = _open(conn, base["甲一"])
        assert r["状态"] == "充电中"
    # 再下再上多轮，闸序单调、重放始终一致
    with db(immediate=True) as conn:
        site_svc.offline_site(conn, site)
        site_svc.online_site(conn, site)
    check(True)
    with db() as conn:
        assert site_svc.gate_seq(conn, site) == 4


def test_重复下线与重复上线被拒(base):
    """严格迁移：事件流不留重影，重放口径不拧花。"""
    site = base["甲站"]
    with db(immediate=True) as conn:
        site_svc.offline_site(conn, site)
        with pytest.raises(HTTPException, match="勿重复下线"):
            site_svc.offline_site(conn, site)
    with db(immediate=True) as conn:
        site_svc.online_site(conn, site)
        with pytest.raises(HTTPException, match="勿重复上线"):
            site_svc.online_site(conn, site)
    with db() as conn:
        assert site_svc.gate_seq(conn, site) == 2
        assert site_svc.gate_status(conn, site)["一致"]


def test_夹具复现_只改启用字则闸态拧花(base):
    """只改启用字的"恢复"不写事件 → 物化闸与事件重放分叉（拧花复现）；
    闸态对照能检出，正规下线/上线流程可修回一致。"""
    site = base["甲站"]
    with db(immediate=True) as conn:
        site_svc.offline_site(conn, site)
    with db(immediate=True) as conn:  # 朴素恢复：只翻启用字
        conn.execute("UPDATE sites SET active=1 WHERE id=?", (site,))
    with db() as conn:
        st = site_svc.gate_status(conn, site)
        assert st["物化闸"] == "开充中" and st["重放闸"] == "禁新开"
        assert not st["一致"]  # 拧花被检出
    # 修回：先把启用字对齐事件流，再走正规上线
    with db(immediate=True) as conn:
        conn.execute("UPDATE sites SET active=0 WHERE id=?", (site,))
    with db(immediate=True) as conn:
        site_svc.online_site(conn, site)
    with db() as conn:
        assert site_svc.gate_status(conn, site)["一致"]


# ---------- 5. 闸序锚定：任意开充都可重放验证 ----------

def test_开会话锚定闸序可重放(base):
    site = base["甲站"]
    with db(immediate=True) as conn:
        r1 = _open(conn, base["甲一"])
        sess_svc.settle(conn, r1["会话编号"])
    with db(immediate=True) as conn:
        site_svc.offline_site(conn, site)
        site_svc.online_site(conn, site)
    with db(immediate=True) as conn:
        r2 = _open(conn, base["甲一"])
    with db() as conn:
        anchors = {}
        for sid in (r1["会话编号"], r2["会话编号"]):
            ev = next(
                e for e in core.session_detail(conn, sid)["事件"] if e["action"] == "开会话"
            )
            m = re.search(r"闸序(\d+)", ev["note"])
            assert m, "开会话事件须锚定闸序"
            anchors[sid] = int(m.group(1))
            # 重放到该闸序，闸必为开 → 此次开充确在闸开时放行
            assert site_svc.replay_gate(conn, site, int(m.group(1))) is True
        assert anchors[r1["会话编号"]] == 0  # 无任何事件时开的第一笔
        assert anchors[r2["会话编号"]] == 2  # 经历 下线+上线 后开的
