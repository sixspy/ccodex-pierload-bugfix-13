"""提单册与过磅票对账证明。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from validators import manifest_gross, validate_bl_match

router = APIRouter(tags=["reconcile"])


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        rows = conn.execute(
            """SELECT bl_code, declared_gross_t FROM bl_manifests ORDER BY bl_code"""
        ).fetchall()
        items = []
        for bl in rows:
            items.append(_reconcile_bl(conn, bl["bl_code"]))
        return {"items": items}


@router.get("/api/reconcile/{bl_code}")
def reconcile_one(bl_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        bl = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
        ).fetchone()
        if not bl:
            raise HTTPException(404, "提单不存在")
        return _reconcile_bl(conn, bl_code)


def _reconcile_bl(conn, bl_code: str) -> dict:
    bl = conn.execute(
        "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
    ).fetchone()
    tickets = conn.execute(
        """SELECT ticket_code, gross_t, status, generation FROM weigh_tickets
           WHERE bl_ref=? AND status NOT IN ('作废')""",
        (bl_code,),
    ).fetchall()
    if not tickets:
        return {
            "bl_code": bl_code,
            "declared_gross_t": bl["declared_gross_t"],
            "ticket_sum_t": 0,
            "balanced": False,
            "reason": "无过磅票",
            "tickets": [],
            "seal_count": 0,
            "seal_ok_count": 0,
            "shadow_seal_count": 0,
            "proof_ready": False,
        }
    ticket_sum = round(sum(float(t["gross_t"]) for t in tickets), 3)
    declared = float(bl["declared_gross_t"])
    # 毛重对账恢复实算：申报与过磅合计不符即不平衡。
    matched = validate_bl_match(declared, ticket_sum)
    seal_rows = conn.execute(
        """SELECT s.seal_ref, s.ticket_generation, s.manifest, t.ticket_code,
                  t.gross_t, t.status
           FROM gate_seals s JOIN weigh_tickets t ON t.id=s.ticket_id
           WHERE t.bl_ref=? AND t.status NOT IN ('作废')""",
        (bl_code,),
    ).fetchall()
    # 逐张快照按票面闸代读数（t.gross_t）判定：清单文本仅作摘要，不得单独作准；
    # 清单读数须与票面读数同源，且票面毛重与申报相符，方计入正式齐套。
    formal_seals = []
    shadow_seals = []
    for s in seal_rows:
        gross = float(s["gross_t"])
        read = manifest_gross(s["manifest"])
        same_source = read is not None and abs(read - gross) <= 1e-6
        if same_source and validate_bl_match(declared, gross):
            formal_seals.append(dict(s))
        else:
            shadow_seals.append(dict(s))
    proof_ok = matched and len(formal_seals) >= 1
    return {
        "bl_code": bl_code,
        "declared_gross_t": declared,
        "ticket_sum_t": ticket_sum,
        "balanced": matched,
        "proof_ready": proof_ok,
        "seal_count": len(formal_seals),
        "seal_ok_count": len(formal_seals),
        "shadow_seal_count": len(shadow_seals),
        "tickets": [dict(t) for t in tickets],
        "seals": formal_seals,
        "shadow_seals": shadow_seals,
    }


@router.get("/api/proof/ticket/{ticket_code}")
def proof_ticket(ticket_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        ticket = conn.execute(
            "SELECT * FROM weigh_tickets WHERE ticket_code=?", (ticket_code,)
        ).fetchone()
        if not ticket:
            raise HTTPException(404, "票不存在")
        bl = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (ticket["bl_ref"],)
        ).fetchone()
        seal = conn.execute(
            """SELECT * FROM gate_seals WHERE ticket_id=? ORDER BY id DESC LIMIT 1""",
            (ticket["id"],),
        ).fetchone()
        release = conn.execute(
            """SELECT * FROM gate_releases WHERE ticket_id=? AND status='已放行'""",
            (ticket["id"],),
        ).fetchone()
        overload_closed = not conn.execute(
            """SELECT id FROM overload_holds WHERE ticket_id=? AND status='开放'""",
            (ticket["id"],),
        ).fetchone()
        manifest_match = False
        seal_formal = False
        if seal and bl:
            declared = float(bl["declared_gross_t"])
            gross = float(ticket["gross_t"])
            manifest_match = validate_bl_match(declared, gross)
            read = manifest_gross(seal["manifest"])
            # 快照须与票面闸代读数同源，且票面毛重与申报相符，才算正式快照。
            seal_formal = bool(
                manifest_match
                and read is not None
                and abs(read - gross) <= 1e-6
            )
        return {
            "ticket_code": ticket_code,
            "bl_ref": ticket["bl_ref"],
            "gross_t": ticket["gross_t"],
            "manifest_matches_bl": manifest_match,
            "has_seal": seal is not None,
            "seal_formal": seal_formal,
            "seal_generation": seal["ticket_generation"] if seal else None,
            "released": release is not None,
            "overload_closed": overload_closed,
            "coherent": bool(
                seal_formal and overload_closed and (seal or release)
            ),
        }