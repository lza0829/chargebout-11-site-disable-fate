"""测试夹具公共层：隔离临时库 + 基础造数。

任何 app 模块导入前先把 CHARGEBOUT_DB 指到临时目录，
避免 seed 落进仓库 data/。
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

_SEED_DIR = tempfile.mkdtemp(prefix="chargebout-seed-")
os.environ["CHARGEBOUT_DB"] = str(Path(_SEED_DIR) / "seed.db")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.db import db, init_db  # noqa: E402


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """每用例一座空库。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    init_db()
    return tmp_path / "t.db"


@pytest.fixture()
def base(fresh):
    """一个费率；甲站（甲一/甲二两桩各一枪）、乙站（乙一一桩一枪）。"""
    with db() as conn:
        conn.execute(
            "INSERT INTO rate_plans(name,peak_price,flat_price,valley_price) VALUES('测',1,1,1)"
        )
        conn.execute("INSERT INTO sites(name,address,active) VALUES('甲站','甲路',1)")
        conn.execute("INSERT INTO sites(name,address,active) VALUES('乙站','乙路',1)")
        for name, sid in (("甲一", 1), ("甲二", 1), ("乙一", 2)):
            cur = conn.execute(
                "INSERT INTO piles(site_id,name,rated_kw,status,rate_plan_id) VALUES(?,?,7,'空闲',1)",
                (sid, name),
            )
            conn.execute(
                "INSERT INTO guns(pile_id,name,status) VALUES(?,'枪','可用')",
                (cur.lastrowid,),
            )
        ids = {}
        for name in ("甲站", "乙站"):
            ids[name] = conn.execute(
                "SELECT id FROM sites WHERE name=?", (name,)
            ).fetchone()["id"]
        for name in ("甲一", "甲二", "乙一"):
            ids[name] = conn.execute(
                "SELECT id FROM piles WHERE name=?", (name,)
            ).fetchone()["id"]
    return ids
