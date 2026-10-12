import asyncio
from types import SimpleNamespace

import httpx
import pytest

import transcribe
from app.http_client import PlatformClient
from app.parsers.xiaohongshu import _find_note


def test_xhs_selects_requested_note_not_recommendation():
    requested = {"noteId": "target", "video": {"url": "correct"}}
    recommendation = {"noteId": "other", "video": {"url": "wrong"}, "title": "more fields"}
    assert _find_note({"notes": [recommendation, requested]}, "target") is requested
    assert _find_note({"notes": [recommendation]}, "target") is None


def test_existing_output_fails_before_paid_work(tmp_path, monkeypatch, capsys):
    path = tmp_path / "existing.html"
    path.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(transcribe, "load_config", lambda _: None)
    async def forbidden(_):
        pytest.fail("Existing output must be checked before any parsing or paid request")
    monkeypatch.setattr(transcribe, "process", forbidden)
    monkeypatch.setattr("sys.argv", ["transcribe", "https://v.douyin.com/example", "--output", str(path)])
    assert transcribe.main() == 1
    assert path.read_text(encoding="utf-8") == "keep"
    assert "已存在" in capsys.readouterr().err


def test_bom_config_and_trailing_equals_preserved(tmp_path, monkeypatch):
    path = tmp_path / "config.env"
    path.write_text('MIMO_API_KEY="test=="\n', encoding="utf-8-sig")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    transcribe.load_config(path)
    assert transcribe.os.environ["MIMO_API_KEY"] == "test=="


@pytest.mark.asyncio
async def test_redirect_does_not_send_cookie_to_another_platform():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://www.douyin.com/video/1"})
    client = PlatformClient()
    await client.close()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="另一个平台"):
            await client.resolve("https://www.xiaohongshu.com/explore/1", {"Cookie": "private-test"})
    finally:
        await client.close()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_html_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr("app.http_client.settings", SimpleNamespace(request_timeout=15, max_html_bytes=10))
    client = PlatformClient()
    await client.close()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 11)))
    try:
        with pytest.raises(ValueError, match="超过"):
            await client.get_text("https://www.douyin.com/video/1", {})
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,finish,hint", [(401, None, "密钥"), (402, None, "额度"), (429, None, "受限"), (200, "length", "未完整")])
async def test_mimo_errors_and_truncation_are_not_success(tmp_path, monkeypatch, status, finish, hint):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"test")
    monkeypatch.setenv("MIMO_API_KEY", "test-only")
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json={"choices": [{"finish_reason": finish, "message": {"content": "partial"}}]}))
    monkeypatch.setattr(transcribe.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    with pytest.raises(RuntimeError, match=hint):
        await transcribe.transcribe_mimo(path)


def _mock_mimo(monkeypatch, responses):
    """按顺序返回给定的响应，并记录请求体。"""
    calls = []

    def handler(request):
        calls.append(request)
        status, body = responses[min(len(calls), len(responses)) - 1]
        return httpx.Response(status, json=body)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(transcribe.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    real_sleep = asyncio.sleep
    monkeypatch.setattr(transcribe.asyncio, "sleep", lambda _seconds: real_sleep(0))
    monkeypatch.setenv("MIMO_API_KEY", "test-only")
    return calls


def _ok(text):
    return 200, {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


@pytest.mark.asyncio
async def test_mimo_uses_asr_model_without_prompt(tmp_path, monkeypatch):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"test")
    calls = _mock_mimo(monkeypatch, [_ok("你好")])
    assert await transcribe.transcribe_mimo(path) == "你好"
    body = __import__("json").loads(calls[0].content)
    assert body["model"] == "mimo-v2.5-asr"
    assert [part["type"] for part in body["messages"][0]["content"]] == ["input_audio"]
    assert body["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/mpeg;base64,")


@pytest.mark.asyncio
async def test_mimo_retries_server_errors_and_empty_content(tmp_path, monkeypatch):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"test")
    calls = _mock_mimo(monkeypatch, [(503, {}), _ok(""), _ok("第三次才成功")])
    assert await transcribe.transcribe_mimo(path) == "第三次才成功"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_mimo_gives_up_after_repeated_server_errors(tmp_path, monkeypatch):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"test")
    calls = _mock_mimo(monkeypatch, [(502, {})])
    with pytest.raises(RuntimeError, match="重试 3 次"):
        await transcribe.transcribe_mimo(path)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_mimo_does_not_retry_auth_errors(tmp_path, monkeypatch):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"test")
    calls = _mock_mimo(monkeypatch, [(401, {})])
    with pytest.raises(RuntimeError, match="密钥"):
        await transcribe.transcribe_mimo(path)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_silent_audio_is_reported_not_saved(tmp_path, monkeypatch):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"test")
    monkeypatch.setattr(transcribe, "split_audio", lambda audio, directory: [audio, audio])
    _mock_mimo(monkeypatch, [_ok("")])
    with pytest.raises(RuntimeError, match="没有识别到人声"):
        await transcribe.transcribe_audio(path)


@pytest.mark.asyncio
async def test_chunks_are_transcribed_in_order_and_joined(tmp_path, monkeypatch):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"test")
    monkeypatch.setattr(transcribe, "split_audio", lambda audio, directory: [audio, audio, audio])
    # 第二段是纯音乐：三次都返回空，跳过它，不让整份文稿失败
    _mock_mimo(monkeypatch, [_ok("第一段。"), _ok(""), _ok(""), _ok(""), _ok("第三段。")])
    assert await transcribe.transcribe_audio(path) == "第一段。第三段。"


def test_join_chunks_keeps_english_words_apart():
    assert transcribe.join_chunks(["中文结尾。", "接着说"]) == "中文结尾。接着说"
    assert transcribe.join_chunks(["the end.", "Next part"]) == "the end. Next part"
    assert transcribe.join_chunks(["", " 只有一段 "]) == "只有一段"
