"""闸代快照：升代并写清单。毛重不符禁止封闸（甲路径，不符当场回滚）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import acquire_for_request, release_request
from models import SealIn
from validators import (
    format_manifest,
    manifest_gross,
    validate_bl_match,
    validate_ticket_sealable,
)

router = APIRouter(tags=["seal"])


def _seal_verdict(declared, ticket_gross, manifest) -> dict:
    """正式/影子判决：须清单读数与票面闸代读数同源，再与提单申报比对。

    两口径不得混用：清单可被调用方自填，故只有清单读数 == 票面毛重、
    且该毛重与申报相符时，才认定为正式闸代；否则一律影子草稿。
    """
    declared_f = float(declared) if declared is not None else None
    gross_f = float(ticket_gross) if ticket_gross is not None else None
    read = manifest_gross(manifest)
    same_source = (
        read is not None and gross_f is not None and abs(read - gross_f) <= 1e-6
    )
    match = bool(
        same_source
        and declared_f is not None
        and validate_bl_match(declared_f, gross_f)
    )
    return {
        "manifest_gross_t": read,
        "ticket_gross_t": gross_f,
        "gross_match": match,
        "caliber": "正式闸代" if match else "影子草稿",
    }


@router.get("/api/seals")
def list_seals(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*, t.ticket_code, t.vehicle_plate, t.gross_t,
                      b.declared_gross_t
               FROM gate_seals s
               JOIN weigh_tickets t ON t.id=s.ticket_id
               LEFT JOIN bl_manifests b ON b.bl_code=t.bl_ref
               ORDER BY s.id DESC"""
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d.update(
                _seal_verdict(
                    d.get("declared_gross_t"), d.get("gross_t"), d.get("manifest")
                )
            )
            items.append(d)
        return {"items": items}


@router.post("/api/seals")
def create_seal(body: SealIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"ticket:{body.ticket_id}"
    with connect() as conn:
        # 一次性业务锁：同账号不同会话也互斥；不重入、不吞外部锁。
        holder = acquire_for_request(conn, lock_key, user)
        ok = False
        try:
            ticket = conn.execute(
                "SELECT * FROM weigh_tickets WHERE id=?", (body.ticket_id,)
            ).fetchone()
            if not ticket:
                raise HTTPException(404, "过磅票不存在")
            validate_ticket_sealable(dict(ticket))
            if ticket["status"] == "旁路挂起":
                raise HTTPException(409, "旁路挂起不得封闸")
            open_hold = conn.execute(
                """SELECT id FROM overload_holds WHERE ticket_id=? AND status='开放'""",
                (body.ticket_id,),
            ).fetchone()
            if open_hold:
                raise HTTPException(409, "超重开放不得封闸")

            bl = conn.execute(
                "SELECT * FROM bl_manifests WHERE bl_code=?", (ticket["bl_ref"],)
            ).fetchone()
            if not bl:
                raise HTTPException(422, "提单册未登记，禁止封闸")
            declared = float(bl["declared_gross_t"])
            # 闸代读数取过磅票毛重（正式口径）；两边不得混用。
            gate_gross = float(ticket["gross_t"])

            # 双口径分裂：清单自报毛重须与闸代读数同源（严格相等），否则拒封。
            if body.manifest:
                manifest_read = manifest_gross(body.manifest)
                if manifest_read is None:
                    raise HTTPException(422, "清单毛重不可解析")
                if abs(manifest_read - gate_gross) > 1e-6:
                    raise HTTPException(409, "清单毛重与闸代读数分裂，禁止封闸")

            if not validate_bl_match(declared, gate_gross):
                # 甲路径：不符即禁封，正式快照行零增，票态与闸代不推进。
                conn.rollback()
                raise HTTPException(
                    409,
                    f"闸代毛重{gate_gross}t与提单申报{declared}t不符，禁止封闸",
                )

            next_gen = int(ticket["generation"]) + 1
            manifest = body.manifest or format_manifest(
                dict(ticket), gate_gross, next_gen
            )
            now = now_iso()
            seal_ref = f"GS-{ticket['ticket_code']}-G{next_gen}"
            cur = conn.execute(
                """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,status,sealed_at)
                   VALUES (?,?,?,?,?,?)""",
                (body.ticket_id, next_gen, manifest, seal_ref, "已封闸", now),
            )
            conn.execute(
                """UPDATE weigh_tickets SET generation=?, status='已封闸' WHERE id=?""",
                (next_gen, body.ticket_id,),
            )
            write_audit(
                conn,
                user,
                "gate_seal",
                seal_ref,
                {
                    "seal_id": cur.lastrowid,
                    "generation": next_gen,
                    "manifest": manifest,
                    "declared_gross_t": declared,
                    "gross_match": True,
                },
            )
            conn.commit()
            ok = True
            return {
                "id": cur.lastrowid,
                "seal_ref": seal_ref,
                "generation": next_gen,
                "manifest": manifest,
                "status": "已封闸",
            }
        finally:
            if not ok:
                conn.rollback()
            release_request(conn, lock_key, holder)


@router.get("/api/seals/{seal_ref}")
def get_seal(seal_ref: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT s.*, t.ticket_code, t.axle_weights, t.gross_t,
                      b.declared_gross_t
               FROM gate_seals s
               JOIN weigh_tickets t ON t.id=s.ticket_id
               LEFT JOIN bl_manifests b ON b.bl_code=t.bl_ref
               WHERE s.seal_ref=?""",
            (seal_ref,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "快照不存在")
        data = dict(row)
        data["axle_weights"] = json.loads(data["axle_weights"] or "[]")
        data.update(
            _seal_verdict(
                data.get("declared_gross_t"), data.get("gross_t"), data.get("manifest")
            )
        )
        return data
