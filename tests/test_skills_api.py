"""Skill 管理 API 测试（repository / 导入模块 monkeypatch，不连库不发网）。"""

import base64
import io
import types
import zipfile
from datetime import datetime, timezone

from a2a_gateway.routes import registry as registry_mod

_NOW = datetime.now(timezone.utc)

SKILL_MD = "---\nname: demo-skill\ndescription: 演示\n---\n正文"


def _skill(**kw):
    base = {
        "id": 1, "name": "demo-skill", "description": "演示", "content": "正文",
        "frontmatter": {}, "files": [], "load_mode": "on_demand",
        "size_bytes": 6, "file_count": 0, "source": "text", "source_ref": "",
        "review_status": "pending", "review_note": "", "reviewed_at": None,
        "enabled": True, "created_at": _NOW, "updated_at": _NOW,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _skill_zip_b64(skill_md: str = SKILL_MD) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo-skill/SKILL.md", skill_md)
    return base64.b64encode(buf.getvalue()).decode()


async def test_skills_require_auth(anon_client):
    assert (await anon_client.get("/api/admin/skills")).status_code == 401


async def test_list_skills(auth_client, monkeypatch):
    async def fake_list(session):
        return [_skill()]

    monkeypatch.setattr(registry_mod.repo, "list_skills", fake_list)
    resp = await auth_client.get("/api/admin/skills")
    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "demo-skill"


async def test_create_skill_rejects_bad_name(auth_client):
    resp = await auth_client.post(
        "/api/admin/skills",
        json={"name": "中文名", "description": "d", "content": "c"},
    )
    assert resp.status_code == 400


async def test_create_skill_rejects_duplicate(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return _skill()

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills", json={"name": "demo-skill", "description": "d", "content": "c"}
    )
    assert resp.status_code == 409


async def test_import_preview_from_text(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview", json={"source": "text", "skill_md": SKILL_MD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["name"] == "demo-skill"
    assert body["items"][0]["conflict"] is False


async def test_import_preview_flags_conflict(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return _skill()

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview", json={"source": "text", "skill_md": SKILL_MD}
    )
    assert resp.json()["items"][0]["conflict"] is True


async def test_import_preview_from_zip(auth_client, monkeypatch):
    async def fake_by_name(session, name):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={"source": "zip", "zip_b64": _skill_zip_b64()},
    )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["name"] == "demo-skill"


async def test_import_preview_reports_parse_error_not_500(auth_client, monkeypatch):
    """zip 内 SKILL.md 不合法：SkillParseError 必须被收口成来源级 error，不能逃逸成 500。"""
    resp = await auth_client.post(
        "/api/admin/skills/import/preview",
        json={"source": "zip", "zip_b64": _skill_zip_b64("缺少 frontmatter 的正文")},
    )
    assert resp.status_code == 200
    assert "frontmatter" in resp.json()["errors"][0]


async def test_import_commit_resets_review_via_overwrite(auth_client, monkeypatch):
    captured = {}

    async def fake_by_name(session, name):
        return _skill()  # 重名

    async def fake_upsert(session, **kwargs):
        captured.update(kwargs)
        return _skill(), False

    async def fake_refresh(session, ids):
        return 0

    async def fake_using(session, skill_id):
        return []

    monkeypatch.setattr(registry_mod.repo, "get_skill_by_name", fake_by_name)
    monkeypatch.setattr(registry_mod.repo, "upsert_imported_skill", fake_upsert)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    resp = await auth_client.post(
        "/api/admin/skills/import/commit",
        json={"source": "text", "skill_md": SKILL_MD, "overwrite": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert captured["overwrite"] is True


async def test_import_url_failure_reported_per_item(auth_client, monkeypatch):
    from a2a_gateway.skill_import import SkillImportError

    async def fake_fetch(url, *, transport=None):
        raise SkillImportError("URL 抓取失败：ConnectError")

    monkeypatch.setattr(registry_mod.si, "fetch_url", fake_fetch)
    resp = await auth_client.post(
        "/api/admin/skills/import/preview", json={"source": "url", "url": "http://x/SKILL.md"}
    )
    assert resp.status_code == 200
    assert "URL 抓取失败" in resp.json()["errors"][0]


async def test_review_endpoint_updates_status(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return _skill(review_status="pending")

    async def fake_set_review(session, skill, review_status, note):
        return _skill(review_status=review_status, review_note=note, reviewed_at=_NOW)

    async def fake_refresh(session, ids):
        return 1

    async def fake_using(session, skill_id):
        return []

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod.repo, "set_skill_review", fake_set_review)
    monkeypatch.setattr(registry_mod.repo, "refresh_agents_for_skills", fake_refresh)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    resp = await auth_client.post(
        "/api/admin/skills/1/review", json={"status": "approved", "note": "ok"}
    )
    assert resp.status_code == 200
    assert resp.json()["review_status"] == "approved"


async def test_delete_skill_requires_force_when_referenced(auth_client, monkeypatch):
    async def fake_get(session, skill_id):
        return _skill()

    async def fake_using(session, skill_id):
        return [types.SimpleNamespace(id=7, name="测试 Agent")]

    async def fake_detach(session, skill_id):
        return 1

    async def fake_delete(session, skill):
        return None

    monkeypatch.setattr(registry_mod.repo, "get_skill", fake_get)
    monkeypatch.setattr(registry_mod.repo, "agents_using_skill", fake_using)
    monkeypatch.setattr(registry_mod.repo, "detach_skill_from_agents", fake_detach)
    monkeypatch.setattr(registry_mod.repo, "delete_skill_record", fake_delete)
    assert (await auth_client.delete("/api/admin/skills/1")).status_code == 409
    assert (await auth_client.delete("/api/admin/skills/1?force=true")).status_code == 204
