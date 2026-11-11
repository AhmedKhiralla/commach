"""Pilot readiness: the platform owner's system check after a deployment."""


def test_system_check(platform, two_companies):
    status, out, _ = platform.get("/api/platform/system-check")
    assert status == 200, out
    by = {c["key"]: c for c in out["checks"]}
    for key in ("database", "migrations", "row_security", "audit_chain", "files", "clock"):
        assert by[key]["status"] == "ok", by[key]
    assert "intact" in by["audit_chain"]["detail"]
    # the test server runs with the scheduler off, insecure cookies allowed and no mail: reported, not hidden
    assert by["scheduler"]["status"] == "warn" and by["email"]["status"] == "warn" and by["backups"]["status"] == "info"
    assert out["status"] in ("warn", "fail") and out["counts"]["companies"] >= 2 and out["counts"]["users"] >= 2
    # only the platform owner may run it
    assert two_companies["ACME"]["admin"].get("/api/platform/system-check")[0] == 403
