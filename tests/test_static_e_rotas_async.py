"""Regressões dos 500 no painel.

Dois bugs que só apareciam em produção:

1. `_StaticNoCache.file_response` lia `resposta.path`, que não existe no 304.
   Como o painel manda `Cache-Control: no-cache`, o navegador revalida sempre
   e o segundo load de qualquer .js/.css virava 500.
2. Rotas `def` que retornavam a corrotina de uma função `async` — o FastAPI
   tentava serializar a corrotina e quebrava.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import admin as admin_mod
from app import auth, db, waiaconnect
from app.main import STATIC_DIR, app

pytestmark = pytest.mark.anyio if False else []


@pytest.fixture
def c():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. Static: 200 e 304
# ---------------------------------------------------------------------------


def test_static_js_200_com_no_cache(c):
    r = c.get("/static/admin/js/admin.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_static_css_200_com_no_cache(c):
    r = c.get("/static/admin/admin.css")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_revalidacao_etag_retorna_304_e_nao_500(c):
    """O bug: com If-None-Match o StaticFiles devolve NotModifiedResponse,
    que não tem `.path`. Acessar o atributo estourava AttributeError → 500."""
    r = c.get("/static/admin/js/admin.js")
    assert r.status_code == 200
    etag = r.headers["etag"]

    rev = c.get("/static/admin/js/admin.js", headers={"If-None-Match": etag})
    assert rev.status_code == 304, rev.text
    assert rev.content == b""


def test_revalidacao_last_modified_retorna_304_e_nao_500(c):
    r = c.get("/static/admin/admin.css")
    assert r.status_code == 200
    lm = r.headers["last-modified"]

    rev = c.get("/static/admin/admin.css", headers={"If-Modified-Since": lm})
    assert rev.status_code == 304, rev.text


def test_revalidacao_repetida_nao_quebra(c):
    """Browser recarrega a página inteira: vários 304 seguidos, nenhum 500."""
    r = c.get("/static/admin/js/admin.js")
    etag = r.headers["etag"]
    for _ in range(5):
        assert c.get("/static/admin/js/admin.js", headers={"If-None-Match": etag}).status_code == 304


def test_css_e_js_nao_confundem_arquivo_inexistente(c):
    assert c.get("/static/admin/js/nao-existe.js").status_code == 404


def test_figura_nao_ganha_no_cache(c):
    """Só .js/.css recebem no-cache; imagem deve ficar cacheável."""
    r = c.get("/static/admin/vendor/gridstack.min.css")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_static_dir_e_absoluto():
    assert STATIC_DIR.is_absolute()
    assert STATIC_DIR.name == "static"
    assert (STATIC_DIR / "admin" / "js" / "admin.js").is_file()


# ---------------------------------------------------------------------------
# 2. Rotas async
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_autenticado(monkeypatch):
    app.dependency_overrides[admin_mod.autenticar] = lambda: "admin"
    app.dependency_overrides[auth.login_required] = lambda: db.Usuario(
        nome="Teste", email="t@t", papel="admin"
    )

    async def fake_verificar(*a, **k):
        return {"conectado": True}

    async def fake_templates(*a, **k):
        return [{"name": "agendamento", "status": "APPROVED"}]

    monkeypatch.setattr(waiaconnect, "verificar_conexao", fake_verificar)
    monkeypatch.setattr(waiaconnect, "listar_templates", fake_templates)

    def fake_get_instancia(i):
        return db.InstanciaWhatsApp(id=i, nome="Principal") if i == 1 else None

    monkeypatch.setattr(db, "get_instancia", fake_get_instancia)
    yield
    app.dependency_overrides.clear()


def test_whatsapp_estado_devolve_json(c, admin_autenticado):
    r = c.get("/admin/whatsapp/estado")
    assert r.status_code == 200, r.text
    assert r.json() == {"conectado": True}


def test_whatsapp_templates_devolve_json(c, admin_autenticado):
    r = c.get("/admin/whatsapp/templates")
    assert r.status_code == 200, r.text
    assert r.json()["templates"][0]["name"] == "agendamento"


def test_instancia_estado_devolve_json(c, admin_autenticado):
    r = c.get("/admin/instancia/1/estado")
    assert r.status_code == 200, r.text
    assert r.json() == {"conectado": True}


def test_instancia_estado_inexistente_404(c, admin_autenticado):
    r = c.get("/admin/instancia/99/estado")
    assert r.status_code == 404


def test_erro_na_api_vira_502_e_nao_500(c, admin_autenticado, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("api fora")

    monkeypatch.setattr(waiaconnect, "verificar_conexao", boom)
    r = c.get("/admin/whatsapp/estado")
    assert r.status_code == 502
    assert "api fora" in r.json()["erro"]