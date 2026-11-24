from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db
from app.services import core
from app.services import sites as svc

router = APIRouter()


class SiteIn(BaseModel):
    名称: str = Field(min_length=1)
    地址: str = ""
    启用: bool = True


class NoteIn(BaseModel):
    备注: str = ""


@router.get("/站点")
def list_sites():
    with db() as conn:
        return core.list_sites(conn)


@router.post("/站点")
def create_site(body: SiteIn):
    with db(immediate=True) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO sites(name,address,active) VALUES(?,?,?)",
                (body.名称, body.地址, 1 if body.启用 else 0),
            )
        except Exception as e:
            raise HTTPException(400, detail=f"无法新建站点：{e}") from e
        sid = cur.lastrowid
        if not body.启用:
            # 建站即下线：补一条下线事件，保证闸态始终可由事件流重放
            svc.append_event(conn, sid, "下线", "建站即下线")
        return {"编号": sid, "名称": body.名称}


@router.post("/站点/{site_id}/下线")
def offline_site(site_id: int, body: NoteIn | None = None):
    with db(immediate=True) as conn:
        return svc.offline_site(conn, site_id, body.备注 if body else "")


@router.post("/站点/{site_id}/上线")
def online_site(site_id: int, body: NoteIn | None = None):
    with db(immediate=True) as conn:
        return svc.online_site(conn, site_id, body.备注 if body else "")


@router.get("/站点/{site_id}/事件")
def site_events(site_id: int):
    with db() as conn:
        return svc.list_site_events(conn, site_id)


@router.get("/站点/{site_id}/闸")
def site_gate(site_id: int):
    with db() as conn:
        return svc.gate_status(conn, site_id)
