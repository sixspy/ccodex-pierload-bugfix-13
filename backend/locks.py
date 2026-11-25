"""码头锁与争用登记（写入路径嵌入）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])

# 锁存活上限：进程崩溃遗留下来的业务锁超过此时长即视为陈旧，可被接管。
LOCK_TTL_SECONDS = 120


def _is_stale(acquired_at: str | None) -> bool:
    if not acquired_at:
        return True
    from datetime import datetime

    try:
        ts = datetime.fromisoformat(acquired_at)
    except ValueError:
        return True
    return (datetime.now() - ts).total_seconds() > LOCK_TTL_SECONDS


def sweep_stale_locks(conn) -> int:
    """回收陈旧锁（启动时与抢锁前各清一次），避免崩溃遗留把票永久钉死。"""
    rows = conn.execute(
        "SELECT lock_key, acquired_at FROM pier_locks"
    ).fetchall()
    stale = [r["lock_key"] for r in rows if _is_stale(r["acquired_at"])]
    for key in stale:
        conn.execute("DELETE FROM pier_locks WHERE lock_key=?", (key,))
    return len(stale)


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def try_acquire(conn, lock_key: str, holder: str, actor: str | None = None) -> bool:
    actor = actor or holder
    now = now_iso()
    # 陈旧锁先行回收，防止崩溃遗留把票永久钉死。
    row = conn.execute(
        "SELECT holder, acquired_at FROM pier_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row is not None and _is_stale(row["acquired_at"]):
        conn.execute("DELETE FROM pier_locks WHERE lock_key=?", (lock_key,))
        conn.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, actor, "回收陈旧锁", f"原持有者={row['holder']}", now),
        )
    # 原子抢锁：INSERT OR IGNORE 命中唯一键即证明已被他人持有，避免 SELECT→INSERT
    # 之间的 TOCTOU 让两端都看到空行、其中一端抛 IntegrityError 成 500。
    cur = conn.execute(
        "INSERT OR IGNORE INTO pier_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
        (lock_key, holder, now),
    )
    if cur.rowcount == 1:
        return True
    row = conn.execute(
        "SELECT holder FROM pier_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (lock_key, actor, "争用", f"持有者={row['holder'] if row else '未知'}", now),
    )
    return False


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM pier_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        # 争用痕须在失败回滚后仍可查：先落库再抛，不随业务事务回滚。
        conn.commit()
        raise HTTPException(409, "争用：后到只记争用，不写业务")


def session_holder(user: str) -> str:
    """按请求生成持有者令牌，令同账号不同会话/标签页也互斥，不可重入。"""
    import uuid

    return f"{user}#{uuid.uuid4().hex}"


def acquire_for_request(conn, lock_key: str, user: str) -> str:
    """签发一次性业务锁并落库；已被任何会话持有时记争用痕并 409。"""
    holder = session_holder(user)
    if not try_acquire(conn, lock_key, holder, actor=user):
        conn.commit()  # 争用痕先落库，不随业务回滚
        raise HTTPException(409, "争用：后到只记争用，不写业务")
    conn.commit()
    return holder


def release_request(conn, lock_key: str, holder: str) -> None:
    release(conn, lock_key, holder)
    conn.commit()


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pier_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, user)
        write_audit(conn, user, "lock_acquire", body.lock_key, {"ok": ok})
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, body.lock_key, user)
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
        return {"ok": True}