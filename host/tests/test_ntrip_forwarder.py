"""NTRIP 포워더의 순수 로직 — 소켓도 시리얼도 실물도 없이 시험한다.

🔴 **왜 이 시험이 있나**

   선행 프로젝트(Q2)의 포워더는 네 가지 결함을 실측으로 남겼다:
   소켓 누수(예외 경로에서 close 누락 → TIME_WAIT 16,696개가 쌓여 PC 의
   모든 외부 TCP 가 죽었다), stop 경합(join 3s < recv 타임아웃 5s → 닫힌
   포트에 write), 고정 5초 백오프, 정지 스트림 무감지(연결은 살았는데
   바이트 0 인 채 영원히 대기). 여기서 보는 것은 그 고침이 계약으로
   남았는가다 — 접속 요청·응답 판정·백오프 수열·조각내기·정지 판정.
"""
from __future__ import annotations

import base64

from host.service.ntrip_forwarder import (
    BACKOFF_START_S,
    BACKOFF_CAP_S,
    STALE_AFTER_S,
    build_request,
    chunks,
    is_stale,
    next_backoff,
    parse_response,
)


# ---------------------------------------------------------------- 접속 요청

def test_request_is_ntrip_v1_with_basic_auth():
    req = build_request("caster.example", 2101, "INCH-RTCM32", "user@x", "pw")
    text = req.decode("ascii")
    assert text.startswith("GET /INCH-RTCM32 HTTP/1.0\r\n")
    assert "Host: caster.example:2101\r\n" in text
    cred = base64.b64encode(b"user@x:pw").decode()
    assert f"Authorization: Basic {cred}\r\n" in text
    assert text.endswith("\r\n\r\n")


def test_request_never_carries_the_raw_password():
    req = build_request("h", 2101, "M", "u", "secret-pw")
    assert b"secret-pw" not in req          # Basic 인코딩으로만 나간다


# ---------------------------------------------------------------- 응답 판정

def test_icy_200_is_accepted_and_leftover_preserved():
    """구형 캐스터는 ICY 200 OK 로 답하고, 헤더 뒤에 이미 RTCM 이 붙어
    올 수 있다 — 그 바이트를 버리면 첫 프레임이 깨진다(Q2 도 보존했다)."""
    ok, leftover = parse_response(b"ICY 200 OK\r\n\r\n\xd3\x00\x04rest")
    assert ok is True
    assert leftover == b"\xd3\x00\x04rest"


def test_http_200_is_accepted():
    ok, leftover = parse_response(
        b"HTTP/1.1 200 OK\r\nContent-Type: gnss/data\r\n\r\n")
    assert ok is True
    assert leftover == b""


def test_401_is_rejected():
    ok, _ = parse_response(b"HTTP/1.0 401 Unauthorized\r\n\r\n")
    assert ok is False


def test_sourcetable_reply_is_rejected():
    """마운트포인트가 틀리면 캐스터는 200 대신 소스테이블을 준다 —
    SOURCETABLE 200 을 '접속 성공'으로 읽으면 RTCM 대신 목록 텍스트가
    보드로 흘러간다."""
    ok, _ = parse_response(b"SOURCETABLE 200 OK\r\n\r\nSTR;...")
    assert ok is False


# ---------------------------------------------------------------- 백오프

def test_backoff_doubles_from_half_second_and_caps():
    """고정 5초(Q2)가 아니라 지수다 — 캐스터가 죽었을 때 초당 재접속으로
    TIME_WAIT 를 쌓지 않으면서, 잠깐의 끊김에는 빨리 돌아온다."""
    seq = []
    b = None
    for _ in range(10):
        b = next_backoff(b)
        seq.append(b)
    assert seq[0] == BACKOFF_START_S == 0.5
    assert seq[1] == 1.0 and seq[2] == 2.0
    assert max(seq) == BACKOFF_CAP_S == 30.0
    assert seq[-1] == BACKOFF_CAP_S            # 상한에서 머문다


# ---------------------------------------------------------------- 조각내기

def test_chunks_split_and_keep_every_byte():
    data = bytes(range(256)) * 10              # 2560 B
    parts = list(chunks(data, 1024))
    assert [len(p) for p in parts] == [1024, 1024, 512]
    assert b"".join(parts) == data


def test_chunks_of_empty_is_empty():
    assert list(chunks(b"", 1024)) == []


# ---------------------------------------------------------------- 정지 판정

def test_stale_stream_is_detected_after_the_threshold():
    """연결은 살았는데 바이트가 안 오는 캐스터 — Q2 는 이 상태로 영원히
    기다렸다. STALE_AFTER_S 를 넘으면 끊고 다시 붙는 것이 고침이다."""
    t0 = 1000.0
    assert is_stale(t0, t0 + STALE_AFTER_S - 0.1) is False
    assert is_stale(t0, t0 + STALE_AFTER_S + 0.1) is True
