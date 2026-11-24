"""端到端夹具：HTTP 层验证下线入口与各写口、各页面的联动口径。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import db, init_db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "api.db")
    init_db()
    with db() as conn:
        conn.execute(
            "INSERT INTO rate_plans(name,peak_price,flat_price,valley_price) VALUES('测',1,1,1)"
        )
        conn.execute("INSERT INTO sites(name,address,active) VALUES('甲站','甲路',1)")
        conn.execute(
            "INSERT INTO piles(site_id,name,rated_kw,status,rate_plan_id) VALUES(1,'甲一',7,'空闲',1)"
        )
        conn.execute("INSERT INTO guns(pile_id,name,status) VALUES(1,'枪','可用')")
        conn.execute(
            "INSERT INTO piles(site_id,name,rated_kw,status,rate_plan_id) VALUES(1,'甲二',7,'空闲',1)"
        )
        conn.execute("INSERT INTO guns(pile_id,name,status) VALUES(2,'枪','可用')")
    return TestClient(app)


def test_api_下线上线全联动(client):
    # 开一笔在充
    r = client.post("/api/开会话", json={"桩位编号": 1})
    assert r.status_code == 200, r.text
    sid = r.json()["会话编号"]
    slip = r.json()["会话号"]

    # 下线：回执带出在充快照
    r = client.post("/api/站点/1/下线", json={"备注": "检修"})
    assert r.status_code == 200, r.text
    receipt = r.json()
    assert receipt["闸"] == "禁新开" and receipt["闸序"] == 1
    assert receipt["在充会话"] == [{"会话编号": sid, "会话号": slip, "状态": "充电中"}]

    # 开充口：禁新开（用空闲桩甲二验证，排除桩态占用干扰）
    r = client.post("/api/开会话", json={"桩位编号": 2})
    assert r.status_code == 400 and "禁止新开充" in r.json()["detail"]

    # 脉冲口、结算口：在充照常收口
    assert client.post(f"/api/会话/{sid}/脉冲", json={"电量": 2, "功率": 7}).status_code == 200
    assert client.post(f"/api/会话/{sid}/结算").status_code == 200

    # 闸对照：物化闸与事件重放一致
    g = client.get("/api/站点/1/闸").json()
    assert g["闸"] == "禁新开" and g["一致"] and g["闸序"] == 1

    # 站点审计流水：事件即回执存档
    ev = client.get("/api/站点/1/事件").json()
    assert ev[-1]["动作"] == "下线" and ev[-1]["在充会话"] == [slip]

    # 总览：禁新开可见，在充已收口归零
    o = client.get("/api/总览").json()
    assert o["禁新开站点"] == 1 and o["禁新开站点名"] == ["甲站"] and o["下线站在充"] == 0

    # 站点页 / 桩位页 / 会话册同刷
    s = client.get("/api/站点").json()[0]
    assert s["gate"] == "禁新开" and s["open_sessions"] == 0
    p = client.get("/api/桩位").json()[0]
    assert p["site_active"] == 0
    se = client.get("/api/会话").json()[0]
    assert se["site_active"] == 0 and se["status"] == "已关账"

    # 上线恢复：闸重开，可再新开
    r = client.post("/api/站点/1/上线", json={})
    assert r.json()["闸"] == "开充中"
    r = client.post("/api/开会话", json={"桩位编号": 1})
    assert r.status_code == 200, r.text
    g = client.get("/api/站点/1/闸").json()
    assert g["闸"] == "开充中" and g["一致"] and g["闸序"] == 2


def test_api_旧启用字口已废(client):
    # 只改启用字的旧口不复存在，防止绕过事件流
    assert client.post("/api/站点/1/停用").status_code == 404
    assert client.post("/api/站点/1/启用").status_code == 404


def test_api_建站即下线也可重放(client):
    r = client.post("/api/站点", json={"名称": "丙站", "地址": "", "启用": False})
    assert r.status_code == 200, r.text
    sid = r.json()["编号"]
    g = client.get(f"/api/站点/{sid}/闸").json()
    assert g["闸"] == "禁新开" and g["一致"]
    ev = client.get(f"/api/站点/{sid}/事件").json()
    assert ev[0]["动作"] == "下线" and ev[0]["闸序"] == 1


def test_api_重复下线被拒(client):
    assert client.post("/api/站点/1/下线", json={}).status_code == 200
    r = client.post("/api/站点/1/下线", json={})
    assert r.status_code == 400 and "勿重复下线" in r.json()["detail"]
    assert client.post("/api/站点/1/上线", json={}).status_code == 200
    r = client.post("/api/站点/1/上线", json={})
    assert r.status_code == 400 and "勿重复上线" in r.json()["detail"]
