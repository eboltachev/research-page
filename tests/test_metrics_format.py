from app.metrics import prometheus_text


def test_prometheus_text_format() -> None:
    text = prometheus_text({"http_requests_total": 3, "http_429_total": 1})
    assert "# TYPE http_requests_total counter" in text
    assert "http_requests_total 3" in text
    assert "# TYPE http_429_total counter" in text
    assert text.endswith("\n")
