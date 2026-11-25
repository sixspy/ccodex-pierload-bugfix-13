"""BUGFIX-13 / 第28号 / 毛重修寅：闸代毛重与提单申报不符禁封（甲路径）三红失败测。

口径：毛重不符禁封；不符时封闸当场回滚，正式快照行零增；对账不得把影子
快照当齐套；同票双会话抢封闸须占票锁，败方只留争用痕。两路径不混用。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db, now_iso


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user="admin", password="admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _lane(c: TestClient, h: dict, code: str) -> dict:
    lanes = c.get("/api/lanes", headers=h).json()["items"]
    return next(x for x in lanes if x["lane_code"] == code)


def _register_bl(c: TestClient, h: dict, code: str, declared: float) -> None:
    res = c.post(
        "/api/bl",
        headers=h,
        json={
            "bl_code": code,
            "cargo_desc": "毛重修复验收货",
            "declared_gross_t": declared,
            "vessel_voyage": "验收轮/2801",
        },
    )
    assert res.status_code == 200


def test_fail_a_mismatch_must_not_write_formal_snapshot():
    """失败测甲：毛重不符仍写正式快照即红；钉住不符仍正式快照。"""
    c = TestClient(app)
    h = _auth(c)
    _register_bl(c, h, "BL-2026-0198", 38.5)
    lane = _lane(c, h, "LN-N-01-IN")
    ticket = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": "BL-2026-0198",
            "vehicle_plate": "验A·2801",
            "axle_weights": [{"axle": 1, "w": 22.0}, {"axle": 2, "w": 23.0}],
            "ticket_code": "WT-BUG13-A",
        },
    ).json()
    assert ticket["gross_t"] == 45.0
    assert ticket["status"] == "已裁定"

    res = c.post("/api/seals", headers=h, json={"ticket_id": ticket["id"]})

    # 甲路径：不符即禁封，正式快照行零增，票态与闸代不被推进
    assert res.status_code == 409, res.text
    seals = c.get("/api/seals", headers=h).json()["items"]
    assert not [s for s in seals if s["ticket_code"] == "WT-BUG13-A"]
    detail = c.get(f"/api/tickets/{ticket['id']}", headers=h).json()["ticket"]
    assert detail["generation"] == 1
    assert detail["status"] == "已裁定"

    # 五页同态：提单页 / 对账页 / 过磅页 / 快照页 / 放行页 同一判决
    bls = c.get("/api/bl", headers=h).json()["items"]
    bl_row = next(x for x in bls if x["bl_code"] == "BL-2026-0198")
    assert bl_row["declared_gross_t"] == 38.5
    assert bl_row["weigh_gross_t"] == 45.0
    assert bl_row["gross_match"] is False
    recon = c.get("/api/reconcile/BL-2026-0198", headers=h).json()
    assert recon["balanced"] is False
    assert recon["proof_ready"] is False
    assert recon["seal_count"] == 0
    releases = c.get("/api/gate/releases", headers=h).json()["items"]
    assert not [g for g in releases if g["ticket_code"] == "WT-BUG13-A"]


def test_fail_b_reconcile_must_not_count_shadow_seal_as_proof():
    """失败测乙：断言对账把不匹配影子快照当齐套即红。"""
    c = TestClient(app)
    h = _auth(c)
    _register_bl(c, h, "BL-2026-0197", 50.0)
    lane = _lane(c, h, "LN-S-01-IN")
    ticket = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": "BL-2026-0197",
            "vehicle_plate": "验B·2802",
            "axle_weights": [{"axle": 1, "w": 22.0}, {"axle": 2, "w": 22.0}],
            "ticket_code": "WT-BUG13-B",
        },
    ).json()
    assert ticket["gross_t"] == 44.0
    assert ticket["status"] == "已裁定"

    # 模拟修复前遗留的正式快照（影子）：毛重与申报不符却已写正式行
    with connect() as conn:
        conn.execute(
            """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,
               status,sealed_at) VALUES (?,?,?,?,?,?)""",
            (
                ticket["id"],
                2,
                "票号=WT-BUG13-B;提单=BL-2026-0197;车牌=验B·2802;毛重=44.0t;闸代=2",
                "GS-BUG13-B-G2",
                "已封闸",
                now_iso(),
            ),
        )
        conn.commit()

    recon = c.get("/api/reconcile/BL-2026-0197", headers=h).json()
    assert recon["declared_gross_t"] == 50.0
    assert recon["ticket_sum_t"] == 44.0
    assert recon["balanced"] is False
    assert recon["proof_ready"] is False
    assert recon["shadow_seal_count"] >= 1


def test_fail_d_manifest_self_reported_gross_must_not_split_from_gate_read():
    """双口径分裂测：来单清单自报毛重与闸代读数不等即禁封，不得走容差放行。"""
    c = TestClient(app)
    h = _auth(c)
    _register_bl(c, h, "BL-2026-0196", 40.0)
    lane = _lane(c, h, "LN-N-01-IN")
    ticket = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": "BL-2026-0196",
            "vehicle_plate": "验D·2803",
            "axle_weights": [{"axle": 1, "w": 20.0}, {"axle": 2, "w": 20.0}],
            "ticket_code": "WT-BUG13-D",
        },
    ).json()
    assert ticket["gross_t"] == 40.0
    # 清单自报 42t 与闸代读数 40t 已分裂（落在 8% 容差内也不得放行）
    res = c.post(
        "/api/seals",
        headers=h,
        json={
            "ticket_id": ticket["id"],
            "manifest": "票号=WT-BUG13-D;提单=BL-2026-0196;车牌=验D·2803;毛重=42.0t;闸代=2",
        },
    )
    assert res.status_code == 409, res.text
    seals = c.get("/api/seals", headers=h).json()["items"]
    assert not [s for s in seals if s["ticket_code"] == "WT-BUG13-D"]
    detail = c.get(f"/api/tickets/{ticket['id']}", headers=h).json()["ticket"]
    assert detail["generation"] == 1
    assert detail["status"] == "已裁定"


def test_fail_c_two_sessions_same_ticket_seal_loser_only_contention():
    """争用失败测：双会话同票封闸须占票锁，败方只记争用痕，不写业务。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin", "admin123")
    h_writer = _auth(c, "writer", "writer123")
    tickets = c.get("/api/tickets", headers=h_admin).json()["items"]
    ticket = next(
        x for x in tickets if x["ticket_code"] == "WT-LN-N-01-IN-001"
    )
    assert ticket["status"] == "已裁定"
    lock_key = f"ticket:{ticket['id']}"
    assert (
        c.post(
            "/api/locks/acquire", headers=h_admin, json={"lock_key": lock_key}
        ).status_code
        == 200
    )

    res = c.post(
        "/api/seals", headers=h_writer, json={"ticket_id": ticket["id"]}
    )
    assert res.status_code == 409, res.text

    cont = c.get("/api/contention", headers=h_admin).json()["items"]
    assert any(
        x["lock_key"] == lock_key
        and x["actor"] == "writer"
        and x["action"] == "争用"
        for x in cont
    ), cont
    seals = c.get("/api/seals", headers=h_admin).json()["items"]
    assert not [
        s for s in seals if s["ticket_code"] == "WT-LN-N-01-IN-001"
    ]


def test_fail_e_same_user_two_sessions_must_not_both_seal():
    """同账号双会话：票锁须按请求持有，不得按用户名重入而双写正式快照。"""
    c = TestClient(app)
    h = _auth(c, "admin", "admin123")
    ticket = next(
        x
        for x in c.get("/api/tickets", headers=h).json()["items"]
        if x["ticket_code"] == "WT-LN-N-01-IN-001"
    )
    lock_key = f"ticket:{ticket['id']}"
    assert (
        c.post(
            "/api/locks/acquire", headers=h, json={"lock_key": lock_key}
        ).status_code
        == 200
    )
    # 外部已占同票锁（可理解为另一标签页/另一会话），本会话封闸不得重入成功。
    res = c.post("/api/seals", headers=h, json={"ticket_id": ticket["id"]})
    assert res.status_code == 409, res.text
    seals = [
        s
        for s in c.get("/api/seals", headers=h).json()["items"]
        if s["ticket_code"] == "WT-LN-N-01-IN-001"
    ]
    assert not seals
    # 外部锁不得被业务封闸吞掉
    locks = [
        x
        for x in c.get("/api/locks", headers=h).json()["items"]
        if x["lock_key"] == lock_key
    ]
    assert locks, "外部持有的票锁被业务封闸释放"


def test_fail_f_legacy_manifest_gross_must_not_overrule_ticket_gross():
    """读侧双口径：清单伪报毛重与票面闸代读数不符时，不得计入正式齐套。"""
    c = TestClient(app)
    h = _auth(c)
    _register_bl(c, h, "BL-2026-0195", 100.0)
    lane = _lane(c, h, "LN-S-01-IN")
    t1 = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": "BL-2026-0195",
            "vehicle_plate": "验F·2804",
            "axle_weights": [{"axle": 1, "w": 25.0}, {"axle": 2, "w": 25.0}],
            "ticket_code": "WT-BUG13-F1",
        },
    ).json()
    t2 = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": "BL-2026-0195",
            "vehicle_plate": "验F·2805",
            "axle_weights": [{"axle": 1, "w": 25.0}, {"axle": 2, "w": 25.0}],
            "ticket_code": "WT-BUG13-F2",
        },
    ).json()
    assert t1["gross_t"] == 50.0 and t2["gross_t"] == 50.0

    # 遗留/伪造快照：清单文本写满 100t，与票面 50t 读数不符
    with connect() as conn:
        conn.execute(
            """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,
               status,sealed_at) VALUES (?,?,?,?,?,?)""",
            (
                t1["id"],
                2,
                "票号=WT-BUG13-F1;提单=BL-2026-0195;车牌=验F·2804;毛重=100.0t;闸代=2",
                "GS-BUG13-F1-G2",
                "已封闸",
                now_iso(),
            ),
        )
        conn.commit()

    seal_row = next(
        s
        for s in c.get("/api/seals", headers=h).json()["items"]
        if s["seal_ref"] == "GS-BUG13-F1-G2"
    )
    assert seal_row["gross_match"] is False, seal_row
    assert seal_row["caliber"] == "影子草稿"

    recon = c.get("/api/reconcile/BL-2026-0195", headers=h).json()
    assert recon["shadow_seal_count"] >= 1
    assert recon["seal_ok_count"] == 0
    assert recon["proof_ready"] is False


def test_fail_g_failed_business_must_not_commit_partial_writes(monkeypatch):
    """异常守卫：业务写已落库但审计抛错时，须整体回滚且不残留票锁。"""
    c = TestClient(app, raise_server_exceptions=False)
    h = _auth(c)
    ticket = next(
        x
        for x in c.get("/api/tickets", headers=h).json()["items"]
        if x["ticket_code"] == "WT-LN-N-01-IN-001"
    )
    before = len(c.get("/api/seals", headers=h).json()["items"])

    import seal_svc

    def boom(*args, **kwargs):
        raise RuntimeError("审计写失败注入")

    monkeypatch.setattr(seal_svc, "write_audit", boom)
    res = c.post("/api/seals", headers=h, json={"ticket_id": ticket["id"]})
    assert res.status_code == 500

    after = len(c.get("/api/seals", headers=h).json()["items"])
    assert after == before, "业务写未随异常回滚"
    locks = c.get("/api/locks", headers=h).json()["items"]
    assert not [
        x for x in locks if x["lock_key"] == f"ticket:{ticket['id']}"
    ], locks
    detail = c.get(f"/api/tickets/{ticket['id']}", headers=h).json()["ticket"]
    assert detail["status"] == "已裁定"
    assert detail["generation"] == 1


def test_fail_h_stale_lock_must_be_reclaimable():
    """陈旧锁：进程崩溃遗留的票锁须可回收，不得把该票永久钉死。"""
    c = TestClient(app)
    h = _auth(c)
    ticket = next(
        x
        for x in c.get("/api/tickets", headers=h).json()["items"]
        if x["ticket_code"] == "WT-LN-N-01-IN-001"
    )
    lock_key = f"ticket:{ticket['id']}"
    # 模拟崩溃遗留：holder 为已不存在请求的 user#uuid，且时间戳很久以前
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pier_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
            (lock_key, "admin#deadbeef", "2000-01-01T00:00:00"),
        )
        conn.commit()

    res = c.post("/api/seals", headers=h, json={"ticket_id": ticket["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["generation"] == 2


def test_fail_i_proof_must_agree_with_formal_shadow_verdict():
    """五页同态：单票证明不得把影子快照判为一致。"""
    c = TestClient(app)
    h = _auth(c)
    _register_bl(c, h, "BL-2026-0194", 38.5)
    lane = _lane(c, h, "LN-N-01-IN")
    t = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": "BL-2026-0194",
            "vehicle_plate": "验I·2806",
            "axle_weights": [{"axle": 1, "w": 19.0}, {"axle": 2, "w": 19.1}],
            "ticket_code": "WT-BUG13-I",
        },
    ).json()
    assert t["gross_t"] == 38.1
    # 遗留伪造快照：清单读数 38.5t 与票面 38.1t 不同源
    with connect() as conn:
        conn.execute(
            """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,
               status,sealed_at) VALUES (?,?,?,?,?,?)""",
            (
                t["id"],
                2,
                "票号=WT-BUG13-I;提单=BL-2026-0194;车牌=验I·2806;毛重=38.5t;闸代=2",
                "GS-BUG13-I-G2",
                "已封闸",
                now_iso(),
            ),
        )
        conn.commit()

    proof = c.get("/api/proof/ticket/WT-BUG13-I", headers=h).json()
    assert proof["seal_formal"] is False, proof
    assert proof["coherent"] is False, proof
