"""NTRIP 포워더 — 캐스터의 RTCM3 보정을 받아 보드로 가는 시리얼에 그대로 붓는다.

젯슨에서 돈다:  NTRIP 캐스터 ──TCP──▶ 이 프로세스 ──/dev/ttyTHS1──▶ 보드
                                                      (J29 핀3 → PA3 → UM981)

🔴 **해석하지 않는다.** 프레임 경계·CRC 검증은 보드 펌웨어(app/mk_rtcm.c)의
   몫이다 — 여기가 파싱을 시작하면 같은 일이 두 곳에서 갈라진다. 이 프로세스는
   "TCP 를 시리얼로 옮기는 관"이고, 관측(끊김·정지·재접속)만 책임진다.

🔴 **시리얼을 읽지 않는다.** /dev/ttyTHS1 의 수신 방향은 기존 수신 프로그램
   (lanemark 앱, 부팅 자동 실행)이 소유한다 — 우리가 read 를 섞으면 그쪽
   레코드가 사라진다. 그래서 exclusive 잠금도 걸지 않는다(쓰기 전용 공유).

원형은 선행 프로젝트 Q2 의 JetsonCode/ntrip_forwarder.py 다. 그쪽이 실측으로
남긴 결함 넷을 고쳐서 가져왔다 (각각의 사연은 test_ntrip_forwarder.py 머리말):

  1. 소켓 누수      → 연결 수명 전체를 try/finally 로 감싼다.
                      (TIME_WAIT 16,696개 사건 — Q2 HANDOFF 2026-06)
  2. stop 경합      → 스레드를 없앴다. 단일 루프 + 플래그라 "닫힌 포트에
                      write" 부류가 구조적으로 사라진다.
  3. 고정 5초 백오프 → 지수(0.5→30s) + 지터. 죽은 캐스터에 초당 재접속으로
                      포트 풀을 갉지 않는다.
  4. 정지 스트림    → 연결은 살았는데 바이트가 없는 상태를 STALE_AFTER_S
                      로 판정해 끊고 다시 붙는다. Q2 는 영원히 기다렸다.

VRS(가상 기준국)는 지원하지 않는다 — 사용자의 캐스터는 단일 기준국 직접
수신(INCH-RTCM32류)이라 GGA 업로드가 필요 없다(사용자 확인 2026-08-28).
필요해지면 여기에 "주기적 GGA 업로드" 하나가 추가될 뿐, 구조는 그대로다.

실행 (젯슨):
    MARKON_NTRIP_USER=... MARKON_NTRIP_PASS=... \\
    python3.11 ntrip_forwarder.py --host gnssdata.or.kr --mount INCH-RTCM32
자격증명은 인자나 환경변수로만 받는다 — 저장소 어디에도 적지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import os
import random
import signal
import socket
import sys
import time

#: 재접속 백오프 — 0.5 초에서 배로 늘어 30 초에서 머문다. 잠깐의 끊김에는
#: 빨리 돌아오고, 죽은 캐스터에는 느리게 두드린다(위 결함 3의 고침).
BACKOFF_START_S = 0.5
BACKOFF_CAP_S = 30.0

#: 이 시간 동안 바이트가 한 개도 안 오면 스트림이 정지한 것으로 보고 끊는다.
#: RTCM 보정은 정상일 때 1초 묶음으로 온다 — 30초 무음이면 서른 번을 놓친
#: 것이고, 그 연결은 죽었다(위 결함 4의 고침).
STALE_AFTER_S = 30.0

#: 시리얼 write 한 번의 조각 크기. 캐스터가 밀린 데이터를 한 번에 쏟아도
#: write 가 오래 붙들리지 않게 자른다 — 921600 에서 1024 B ≈ 11 ms.
CHUNK_BYTES = 1024

CONNECT_TIMEOUT_S = 10.0
RECV_TIMEOUT_S = 5.0
STATUS_INTERVAL_S = 10.0


# ---------------------------------------------------------------- 순수 로직
# (소켓·시리얼 없이 시험된다 — host/tests/test_ntrip_forwarder.py)

def build_request(host: str, port: int, mount: str,
                  user: str, password: str) -> bytes:
    """NTRIP v1 접속 요청. 캐스터(gnssdata.or.kr류)는 v1 로 충분하다 —
    Q2 가 같은 요청으로 실사용했다. Host 헤더는 v1 규격 밖이지만 넣는다
    (가상 호스팅 캐스터에서 없으면 404 가 난다)."""
    cred = base64.b64encode(f"{user}:{password}".encode()).decode()
    return (
        f"GET /{mount} HTTP/1.0\r\n"
        f"Host: {host}:{port}\r\n"
        f"User-Agent: NTRIP markon-forwarder/1.0\r\n"
        f"Authorization: Basic {cred}\r\n"
        f"\r\n"
    ).encode("ascii")


def parse_response(buf: bytes) -> tuple[bool, bytes]:
    """헤더를 판정하고, 헤더 뒤에 이미 붙어 온 본문 바이트를 보존한다.

    🔴 "200" 이 들어 있다고 성공이 아니다 — 마운트포인트가 틀리면
       `SOURCETABLE 200 OK` 가 오고, 그것을 통과시키면 RTCM 대신 목록
       텍스트가 보드로 흘러간다. 성공은 `ICY 200 OK`(구형)와
       `HTTP/1.x 200`(신형) 둘뿐이다."""
    head, sep, leftover = buf.partition(b"\r\n\r\n")
    if not sep:
        return False, b""
    first = head.split(b"\r\n", 1)[0]
    ok = first.startswith(b"ICY 200") or (
        first.startswith(b"HTTP/") and b" 200" in first)
    return ok, leftover


def next_backoff(prev: float | None) -> float:
    """None → 0.5, 이후 배씩, 30 에서 상한. 지터는 잠들 때 곱한다(수열
    자체는 결정적이라 시험이 그대로 본다)."""
    if prev is None:
        return BACKOFF_START_S
    return min(BACKOFF_CAP_S, prev * 2.0)


def chunks(data: bytes, n: int = CHUNK_BYTES):
    """data 를 n 바이트 조각으로 — 한 바이트도 잃지 않는다."""
    for i in range(0, len(data), n):
        yield data[i:i + n]


def is_stale(last_data_mono: float, now_mono: float) -> bool:
    return now_mono - last_data_mono > STALE_AFTER_S


# ---------------------------------------------------------------- 본체

def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Forwarder:
    """단일 루프 — 접속 → 인증 → 수신 → 시리얼 기록, 끊기면 백오프 후 재접속.

    `write_fn(bytes)` 는 시리얼로 쓰는 함수다(주입 — 시험·교체 가능).
    쓰기 실패(타임아웃)는 세고 그 조각만 버린다: 보드가 잠깐 느린 것 때문에
    캐스터 연결까지 끊을 이유가 없다 — 보정은 초마다 다시 온다.
    """

    def __init__(self, host: str, port: int, mount: str,
                 user: str, password: str, write_fn) -> None:
        self.host = host
        self.port = port
        self.mount = mount
        self.user = user
        self.password = password
        self.write_fn = write_fn
        self.stop = False
        self.bytes_total = 0
        self.recv_chunks = 0          # recv() 횟수다 — RTCM 프레임 수가 아니다
        self.reconnects = 0
        self.write_timeouts = 0

    # 한 번의 연결 수명. 정상 종료든 예외든 소켓은 반드시 닫힌다(결함 1). */
    def _session(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(CONNECT_TIMEOUT_S)
            sock.connect((self.host, self.port))
            sock.sendall(build_request(self.host, self.port, self.mount,
                                       self.user, self.password))

            buf = b""
            while b"\r\n\r\n" not in buf:
                got = sock.recv(1024)
                if not got:
                    raise ConnectionError("캐스터가 응답 없이 끊었다")
                buf += got
                if len(buf) > 65536:
                    raise ConnectionError("응답 헤더가 64KB 를 넘는다")
            ok, leftover = parse_response(buf)
            if not ok:
                first = buf.split(b"\r\n", 1)[0].decode(errors="replace")
                raise ConnectionError(f"접속 거절: {first!r} — 계정·마운트포인트를 본다")

            _log(f"접속됨 {self.host}:{self.port}/{self.mount}")
            sock.settimeout(RECV_TIMEOUT_S)
            last_data = time.monotonic()
            if leftover:
                self._write(leftover)
                last_data = time.monotonic()

            while not self.stop:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    if is_stale(last_data, time.monotonic()):
                        raise ConnectionError(
                            f"{STALE_AFTER_S:.0f}초째 무음 — 정지 스트림, 다시 붙는다")
                    continue
                if not data:
                    raise ConnectionError("캐스터가 연결을 닫았다")
                last_data = time.monotonic()
                self._write(data)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _write(self, data: bytes) -> None:
        self.bytes_total += len(data)
        self.recv_chunks += 1
        for piece in chunks(data):
            try:
                self.write_fn(piece)
            except Exception:                      # write_timeout 등
                self.write_timeouts += 1           # 조각만 버린다 — 위 클래스 주석

    def run(self) -> None:
        backoff: float | None = None
        last_status = time.monotonic()
        last_bytes = 0
        while not self.stop:
            try:
                self._session()
            except (OSError, ConnectionError) as e:
                if self.stop:
                    break
                backoff = next_backoff(backoff)
                self.reconnects += 1
                _log(f"끊김: {e} — {backoff:.1f}초 뒤 재접속")
                # 지터 — 여럿이 같은 순간 몰려 두드리지 않게 ±20 %.
                time.sleep(backoff * random.uniform(0.8, 1.2))
            now = time.monotonic()
            if now - last_status >= STATUS_INTERVAL_S:
                rate = (self.bytes_total - last_bytes) / (now - last_status)
                _log(f"누적 {self.bytes_total:,} B · {rate:,.0f} B/s · "
                     f"재접속 {self.reconnects} · 쓰기 실패 {self.write_timeouts}")
                last_status, last_bytes = now, self.bytes_total
        _log("종료")


# ---------------------------------------------------------------- 실행

def _open_serial(port: str, baud: int):
    import serial  # 젯슨에서만 필요 — 순수 로직 시험은 이 import 없이 돈다

    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0
    s.write_timeout = 2.0
    # 🔴 열기 전에 내린다 — CLAUDE.md §4 의 보드 멈춤 사고(DTR/RTS 를 세운
    #    채 열면 보드가 멈춘다). ttyTHS 에는 모뎀 선이 없어 보통 무해하지만,
    #    이 스크립트를 실수로 보드 직결 포트에 겨눠도 안전해야 한다.
    s.dtr = False
    s.rts = False
    if os.name == "posix":
        # 쓰기 전용 공유 — 기존 수신 프로그램(lanemark)이 이미 열고 있다.
        s.exclusive = False
    s.open()
    return s


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="NTRIP 캐스터의 RTCM3 보정을 보드로 가는 시리얼에 중계한다 "
                    "(젯슨 무인 운용용 — host/service/collector.py 와 같은 부류)")
    p.add_argument("--host", default=os.getenv("MARKON_NTRIP_HOST", "gnssdata.or.kr"))
    p.add_argument("--port", type=int, default=int(os.getenv("MARKON_NTRIP_PORT", "2101")))
    p.add_argument("--mount", default=os.getenv("MARKON_NTRIP_MOUNT"),
                   help="마운트포인트(예: INCH-RTCM32). 필수")
    p.add_argument("--user", default=os.getenv("MARKON_NTRIP_USER"))
    p.add_argument("--password", default=os.getenv("MARKON_NTRIP_PASS"),
                   help="환경변수 MARKON_NTRIP_PASS 권장 — ps 목록에 안 남는다")
    p.add_argument("--serial", default=os.getenv("MARKON_JET_SERIAL", "/dev/ttyTHS1"))
    p.add_argument("--baud", type=int, default=921600,
                   help="보드 젯슨 링크와 같아야 한다(bsp/mk_jet.h MK_JET_BAUD)")
    args = p.parse_args(argv)

    if not args.mount or not args.user or args.password is None:
        p.error("--mount, --user, --password(또는 환경변수)가 필요하다")

    ser = _open_serial(args.serial, args.baud)
    fwd = Forwarder(args.host, args.port, args.mount,
                    args.user, args.password, ser.write)

    def _stop(_sig, _frm):
        fwd.stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    _log(f"{args.serial} @ {args.baud} → {args.host}:{args.port}/{args.mount}")
    try:
        fwd.run()
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
