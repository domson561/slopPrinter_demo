import pytest


@pytest.mark.django_db
def test_healthz_answers_ok(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.content == b"ok"
