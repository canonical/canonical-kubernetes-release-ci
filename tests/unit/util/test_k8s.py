import time
from unittest.mock import MagicMock, patch

import pytest
import requests
from tenacity import RetryCallState, wait_none
from util.k8s import (
    _fetch_tags_page,
    _wait_strategy,
    get_k8s_tags,
    get_latest_releases_by_minor,
    get_latest_stable,
    is_stable_release,
)

SAMPLE_TAGS = [
    {"name": "v1.33.0-alpha.0"},
    {"name": "v1.32.0-rc.0"},
    {"name": "v1.31.6"},
    {"name": "v1.31.5"},
    {"name": "v1.30.9"},
    {"name": "v1.29.10"},
]


@pytest.fixture(autouse=True)
def patch_tenacity_wait(monkeypatch):
    monkeypatch.setattr(_fetch_tags_page.retry, "wait", wait_none())


@pytest.fixture
def mock_requests_get():
    with patch("requests.get") as mock_get:
        mock_response = mock_get.return_value
        mock_response.headers = {}
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_TAGS

        yield mock_get


def test_get_k8s_tags(mock_requests_get):
    tags = get_k8s_tags()
    assert tags == [
        "v1.33.0-alpha.0",
        "v1.32.0-rc.0",
        "v1.31.6",
        "v1.31.5",
        "v1.30.9",
        "v1.29.10",
    ]


def test_get_k8s_tags_authenticates_with_github_token(mock_requests_get, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    get_k8s_tags()
    headers = mock_requests_get.call_args.kwargs["headers"]
    assert headers == {"Authorization": "Bearer test-token"}


def test_get_k8s_tags_without_token_sends_no_auth(mock_requests_get, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    get_k8s_tags()
    headers = mock_requests_get.call_args.kwargs["headers"]
    assert headers == {}


def test_get_latest_stable(mock_requests_get):
    latest_stable = get_latest_stable()
    assert latest_stable == "v1.31.6"


def test_get_latest_releases_by_minor(mock_requests_get):
    by_minor = get_latest_releases_by_minor()
    assert by_minor == {
        "1.33": "v1.33.0-alpha.0",
        "1.32": "v1.32.0-rc.0",
        "1.31": "v1.31.6",
        "1.30": "v1.30.9",
        "1.29": "v1.29.10",
    }


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.31.6", True),
        ("v1.31.0-beta.1", False),
        ("v1.32.0-rc.0", False),
        ("v1.33.0-alpha.0", False),
    ],
)
def test_is_stable_release(tag, expected):
    assert is_stable_release(tag) == expected


def test_get_k8s_tags_uses_request_timeout(mock_requests_get):
    get_k8s_tags()
    assert mock_requests_get.call_args.kwargs["timeout"] == 15


@pytest.mark.parametrize(
    "transient_exc",
    [
        requests.exceptions.ReadTimeout("read timed out"),
        requests.exceptions.ConnectionError("connection reset"),
    ],
)
def test_get_k8s_tags_retries_transient_network_error_then_succeeds(
    transient_exc,
):
    success_response = MagicMock()
    success_response.headers = {}
    success_response.status_code = 200
    success_response.json.return_value = SAMPLE_TAGS

    with patch("requests.get", side_effect=[transient_exc, success_response]) as mock_get:
        tags = get_k8s_tags()
        assert tags == [
            "v1.33.0-alpha.0",
            "v1.32.0-rc.0",
            "v1.31.6",
            "v1.31.5",
            "v1.30.9",
            "v1.29.10",
        ]
        assert mock_get.call_count == 2


@pytest.mark.parametrize(
    "status_code,headers,reason,body",
    [
        (500, {}, "Internal Server Error", ""),
        (503, {}, "Service Unavailable", ""),
        (429, {}, "Too Many Requests", ""),
        (429, {"retry-after": "5"}, "Too Many Requests", ""),
        (403, {"x-ratelimit-remaining": "0"}, "Forbidden", ""),
        (403, {"retry-after": "60"}, "Forbidden", ""),
        (403, {}, "Forbidden", "API rate limit exceeded for user"),
        (403, {}, "rate limit exceeded", ""),
    ],
)
def test_get_k8s_tags_retries_transient_http_error_then_succeeds(
    status_code, headers, reason, body
):
    err_resp = requests.Response()
    err_resp.status_code = status_code
    err_resp.headers.update(headers)
    err_resp.reason = reason
    if body:
        err_resp._content = body.encode("utf-8")
    http_err = requests.exceptions.HTTPError(response=err_resp)

    success_response = MagicMock()
    success_response.headers = {}
    success_response.status_code = 200
    success_response.json.return_value = SAMPLE_TAGS

    with patch("requests.get", side_effect=[http_err, success_response]) as mock_get:
        tags = get_k8s_tags()
        assert len(tags) == len(SAMPLE_TAGS)
        assert mock_get.call_count == 2


@pytest.mark.parametrize(
    "status_code,headers",
    [
        (400, {}),
        (401, {}),
        (404, {}),
        (403, {}),
        (403, {"x-ratelimit-remaining": "60"}),
    ],
)
def test_get_k8s_tags_non_retryable_4xx_raises_immediately(status_code, headers):
    err_resp = requests.Response()
    err_resp.status_code = status_code
    err_resp.headers.update(headers)
    err_resp.reason = "Client Error"
    http_err = requests.exceptions.HTTPError(response=err_resp)

    with patch("requests.get", side_effect=http_err) as mock_get:
        with pytest.raises(requests.exceptions.HTTPError):
            get_k8s_tags()
        assert mock_get.call_count == 1


def test_get_k8s_tags_403_with_reset_header_and_remaining_quota_raises_immediately():
    err_resp = requests.Response()
    err_resp.status_code = 403
    err_resp.headers.update(
        {
            "x-ratelimit-remaining": "42",
            "x-ratelimit-reset": "1730000000",
        }
    )
    err_resp.reason = "Forbidden"
    err_resp._content = b'{"message": "Resource not accessible by integration"}'
    http_err = requests.exceptions.HTTPError(response=err_resp)

    with patch("requests.get", side_effect=http_err) as mock_get:
        with pytest.raises(requests.exceptions.HTTPError):
            get_k8s_tags()
        assert mock_get.call_count == 1


@pytest.mark.parametrize(
    "status_code,headers",
    [
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(time.time() + 3600)}),
        (429, {"retry-after": "120"}),
    ],
)
def test_get_k8s_tags_rate_limit_exceeding_max_wait_raises_immediately(status_code, headers):
    err_resp = requests.Response()
    err_resp.status_code = status_code
    err_resp.headers.update(headers)
    err_resp.reason = "Rate Limit Exceeded"
    http_err = requests.exceptions.HTTPError(response=err_resp)

    with patch("requests.get", side_effect=http_err) as mock_get:
        with pytest.raises(requests.exceptions.HTTPError):
            get_k8s_tags()
        assert mock_get.call_count == 1


def test_get_k8s_tags_persistent_transient_failure_exhausts_retries():
    with patch(
        "requests.get", side_effect=requests.exceptions.ReadTimeout("read timed out")
    ) as mock_get:
        with pytest.raises(requests.exceptions.ReadTimeout):
            get_k8s_tags()
        assert mock_get.call_count == 3


def test_wait_strategy_uses_retry_after_header():
    resp = requests.Response()
    resp.headers["retry-after"] = "45"
    state = MagicMock(spec=RetryCallState)
    state.outcome = MagicMock(failed=True)
    state.outcome.exception.return_value = requests.exceptions.HTTPError(response=resp)
    assert _wait_strategy(state) == 45.0

    # Over-cap delay is capped at 60.0s
    resp.headers["retry-after"] = "120"
    assert _wait_strategy(state) == 60.0


def test_wait_strategy_uses_ratelimit_reset_header():
    now = time.time()
    resp = requests.Response()
    resp.headers["x-ratelimit-remaining"] = "0"
    resp.headers["x-ratelimit-reset"] = str(now + 30)
    state = MagicMock(spec=RetryCallState)
    state.outcome = MagicMock(failed=True)
    state.outcome.exception.return_value = requests.exceptions.HTTPError(response=resp)
    wait_seconds = _wait_strategy(state)
    assert 29.0 <= wait_seconds <= 31.0

    # Over-cap reset delay is capped at 60.0s
    resp.headers["x-ratelimit-reset"] = str(now + 3600)
    assert _wait_strategy(state) == 60.0


def test_wait_strategy_falls_back_to_exponential_backoff():
    state = MagicMock(spec=RetryCallState)
    state.outcome = MagicMock(failed=True)
    state.outcome.exception.return_value = requests.exceptions.ReadTimeout("timeout")
    state.attempt_number = 1
    wait_seconds = _wait_strategy(state)
    assert wait_seconds >= 1.0
