"""提单册登记。毛重口径：申报毛重与过磅合计逐票同态展示。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from models import BlIn
from validators import validate_bl_match

router = APIRouter(tags=["bl"])


def _with_gross(conn, row: dict) -> dict:
    """为提单补过磅合计与匹配判决，供提单页与对账页同态核销。"""
    agg = conn.execute(
        """SELECT COALESCE(SUM(gross_t),0) s, COUNT(*) c FROM weigh_tickets
           WHERE bl_ref=? AND status NOT IN ('作废')""",
        (row["bl_code"],),
    ).fetchone()
    weighed = round(float(agg["s"]), 3)
    declared = float(row["declared_gross_t"])
    row["weigh_gross_t"] = weighed
    row["ticket_count"] = int(agg["c"])
    row["gross_match"] = bool(
        agg["c"] > 0 and validate_bl_match(declared, weighed)
    )
    return row


@router.get("/api/bl")
def list_bl(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bl_manifests ORDER BY bl_code"
        ).fetchall()
        return {"items": [_with_gross(conn, dict(r)) for r in rows]}


@router.get("/api/bl/{bl_code}")
def get_bl(bl_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "提单不存在")
        tickets = conn.execute(
            """SELECT ticket_code, gross_t, status, generation FROM weigh_tickets
               WHERE bl_ref=? ORDER BY id DESC""",
            (bl_code,),
        ).fetchall()
        declared = float(row["declared_gross_t"])
        items = []
        for t in tickets:
            d = dict(t)
            d["gross_match"] = validate_bl_match(declared, float(t["gross_t"]))
            items.append(d)
        return {
            "bl": _with_gross(conn, dict(row)),
            "tickets": items,
        }


@router.post("/api/bl")
def create_bl(body: BlIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        if conn.execute(
            "SELECT id FROM bl_manifests WHERE bl_code=?", (body.bl_code,)
        ).fetchone():
            raise HTTPException(409, "提单号已存在")
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO bl_manifests(bl_code,cargo_desc,declared_gross_t,vessel_voyage,
               status,created_at) VALUES (?,?,?,?,?,?)""",
            (
                body.bl_code,
                body.cargo_desc,
                body.declared_gross_t,
                body.vessel_voyage,
                "在港",
                now,
            ),
        )
        write_audit(conn, user, "bl_register", body.bl_code, {"bl_id": cur.lastrowid})
        conn.commit()
        return {"id": cur.lastrowid, "bl_code": body.bl_code}
