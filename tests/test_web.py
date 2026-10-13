import logging
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CALL, SECRETS
from transcriber.store import Store
from transcriber.web import app as web

H = {"X-Requested-With": "transcriber"}


@pytest.fixture
def client(tmp_path):
    web.S.store = Store(tmp_path / "cases.db")
    web.S.session = None
    c = TestClient(web.app, base_url="http://127.0.0.1:7860")
    yield c
    web.S.store.close()


def login(c):
    r = c.post("/api/setup", json={"password": "password123"}, headers=H)
    assert r.status_code == 200


def wait_job(c, job_id):
    for _ in range(200):
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job timeout")


def test_host_check_and_auth(client):
    r = client.get("/api/state", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    assert client.get("/api/cases").status_code == 401
    assert client.post("/api/setup", json={"password": "password123"}).status_code == 403  # без заголовка
    login(client)
    assert client.get("/api/cases").status_code == 200
    assert client.post("/api/cases", json={"name": "x"}).status_code == 403  # без заголовка


def test_full_cycle(client, caplog):
    caplog.set_level(logging.DEBUG)
    login(client)
    case = client.post("/api/cases", json={"name": "Продажи"}, headers=H).json()
    text = "\n".join(f"{s}: {t}" for s, t in CALL)
    job = client.post(f"/api/cases/{case['id']}/upload?filename=call.txt", content=text.encode(), headers=H).json()
    job = wait_job(client, job["id"])
    assert job["status"] == "done", job
    tid = job["transcript_id"]

    r = client.get(f"/api/transcripts/{tid}").json()
    assert r["case_code"] == case["code"]
    assert any(s["type"] == "ТЕЛЕФОН" for s in r["spans"])

    # снять маску и вернуть её выделением
    anna = next(s for s in r["spans"] if s["value"] == "Анна")
    r = client.delete(f"/api/transcripts/{tid}/spans/{anna['id']}", headers=H).json()
    assert not any(s["value"] == "Анна" for s in r["spans"])
    assert r["leaks"] == []  # снятая вручную маска не считается утечкой
    utt = r["utterances"][0]["text"]
    start = utt.index("Анна")
    r = client.post(f"/api/transcripts/{tid}/spans", json={"utt": 0, "start": start, "end": start + 4,
                                                            "type": "ФИО"}, headers=H).json()
    assert any(s["value"] == "Анна" for s in r["spans"])

    assert client.post(f"/api/transcripts/{tid}/export", json={"fmt": "docx"}, headers=H).status_code == 409
    client.post(f"/api/transcripts/{tid}/reviewed", json={"reviewed": True}, headers=H)
    exp = client.post(f"/api/transcripts/{tid}/export", json={"fmt": "md"}, headers=H).json()
    assert exp["filename"] == f"{case['code']}_транскрипт.md"

    preview = "\n".join(client.get(f"/api/transcripts/{tid}/preview").json()["lines"])
    for s in SECRETS:
        assert s.lower() not in preview.lower(), s

    petrov = next(e for e in r["entities"] if e["value"] == "Петров Иван Сергеевич")
    answer = f"По делу {case['code']}: перезвонить [ФИО_{petrov['n']}:дат], метка [ФИО_77]."
    res = client.post("/api/unmask", json={"text": answer}, headers=H).json()
    assert "Петрову Ивану Сергеевичу" in res["text"]
    assert res["unknown"] == 1

    # в логах нет текста и значений ПД
    log_text = caplog.text.lower()
    for s in SECRETS:
        assert s.lower() not in log_text


def test_unmask_requires_case(client):
    login(client)
    r = client.post("/api/unmask", json={"text": "без кода [ФИО_1]"}, headers=H)
    assert r.status_code == 422


def test_settings_and_delete(client):
    login(client)
    s = client.put("/api/settings", json={"retention_days": 7, "types": ["ТЕЛЕФОН"]}, headers=H).json()
    assert s["retention_days"] == 7 and s["types"] == ["ТЕЛЕФОН"]
    case = client.post("/api/cases", json={"name": "x"}, headers=H).json()
    assert client.delete(f"/api/cases/{case['id']}", headers=H).status_code == 200
    assert client.get("/api/cases").json() == []
