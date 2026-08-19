"""GUI 없이 계속 도는 무인 수집기.

계획서 §1 — Jetson 은 GUI 없이 부팅 후 자동으로 수집·저장해야 한다.
이 모듈은 그 실행기다.

    python -m host.service.collector --port COM23 --data-dir ./data

🔴 연결이 끊겨도 죽지 않는다. 보드를 뽑았다 꽂으면(실제로 겪는 일이다)
다시 붙는다 — 재시도 간격은 아래 `RECONNECT_BACKOFF_*` 근거를 본다.
끊긴 구간은 `host.storage.store.RecordStore.write_event`로 저장소에
그대로 남는다 — 나중에 "이 시간대에 왜 데이터가 없지?"의 답이 파일
안에 있어야 한다.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from collections.abc import Callable
from pathlib import Path

from host.core.errors import ProtocolError
from host.core.limits import DEFAULT_BAUD
from host.core.records import parse_record
from host.service.board_service import BoardService, SerialTransport
from host.storage.store import RecordStore, apply_retention

log = logging.getLogger("markon.collector")

#: 재접속 간격 — 처음엔 짧게, 실패할수록 점점 늘려 상한까지(지수 백오프).
#:
#: 근거: USB 재열거는 보통 1~2초 안에 끝난다 — 운영체제가 장치를 다시
#: 인식하는 시간이다. 너무 짧으면(예: 0.1초) 그 사이 CPU 를 계속 태우고
#: 로그가 초당 열 줄씩 쌓인다. 너무 길면(예: 30초) 다시 꽂았는데 한참
#: 반응이 없어 "고장났나" 오해를 산다. 0.5초 시작 → ×2 배증 → 5초 상한이
#: 그 중간이다 — 몇 번 실패가 계속되면(보드가 정말 없는 상태) 5초마다만
#: 확인해 자원을 아끼고, 금방 다시 꽂히는 흔한 경우는 1~2초 안에 잡는다.
RECONNECT_BACKOFF_START_S = 0.5
RECONNECT_BACKOFF_MAX_S = 5.0

#: 연결된 상태에서 한 스텝을 마친 뒤 쉬는 시간.
#:
#: 실물 시리얼(`SerialTransport`)은 `read()`가 포트의 `timeout`(0.1초)
#: 만큼 자체적으로 블로킹하므로 사실 이 상한이 없어도 CPU 를 다 먹지
#: 않는다. 그래도 `--port sim`처럼 즉시 반환하는 트랜스포트에서 바쁜
#: 대기(busy loop)가 되는 것을 막기 위해 CLI `monitor`(markon_cli.py)와
#: 같은 값(0.02초, 50Hz)을 둔다 — 최소 레코드 주기(10ms)보다 촘촘히
#: 도니 수집 지연으로 느껴지지 않는다.
POLL_INTERVAL_S = 0.02

#: 상태 파일을 갱신하는 주기. 매 스텝마다 쓰면(최대 초당 100줄) 디스크
#: 쓰기가 잦다 — 상태는 사람이나 감시 프로세스가 보는 것이라 1초에 한
#: 번이면 충분하다.
STATUS_WRITE_INTERVAL_S = 1.0

Transport = object  # host.service.board_service.Transport 와 같은 구조적 타입


class Collector:
    """연결→수집→저장을 반복한다.

    끊기면 재시도하고, 끊긴 구간은 저장소에 `link_down`/`link_up`
    이벤트로 남긴다. `step()`이 한 번 도는 단위 — 시험이 반복 호출해
    시간을 통제한다. `run_forever()`는 그것을 무한히 반복하는 껍데기다.
    """

    def __init__(self, connect: Callable[[], Transport], store: RecordStore, *,
                 clock: Callable[[], int] = lambda: int(time.time() * 1000),
                 sleep: Callable[[float], None] = time.sleep,
                 hb_interval_ms: int = 1000,
                 poll_interval_s: float = POLL_INTERVAL_S,
                 status_path: Path | None = None,
                 status_interval_s: float = STATUS_WRITE_INTERVAL_S):
        self._connect = connect
        self.store = store
        self._clock = clock
        self._sleep = sleep
        self.hb_interval_ms = hb_interval_ms
        self._poll_interval_s = poll_interval_s
        self.status_path = Path(status_path) if status_path else None
        self._status_interval_s = status_interval_s

        self.service: BoardService | None = None
        self._backoff_s = RECONNECT_BACKOFF_START_S
        self._had_link_down = False
        self._last_hb_ms: int | None = None
        self._stop = False

        #: 상태 지표 — 세션 전체 누적(board_service.line_total 과 같은 성격).
        self.line_total = 0
        self.record_total = 0
        self.last_line_at: int | None = None
        self._last_status_write = 0.0

    # ------------------------------------------------------------------ 실행
    def stop(self) -> None:
        """다음 `run_forever()` 반복에서 멈추라는 신호. SIGINT/SIGTERM 핸들러가 부른다."""
        self._stop = True

    def run_forever(self) -> None:
        while not self._stop:
            self.step()

    def step(self) -> None:
        """한 번 돈다. 연결 안 됐으면 연결을 시도하고, 됐으면 펌프한다.

        내부에서 재접속 사이 외에는 sleep 하지 않는다(연결돼 있을 때는
        `POLL_INTERVAL_S`만큼만) — 시험이 시간을 통제할 수 있어야 한다.
        """
        if self.service is None:
            self._try_connect()
            return

        now = self._clock()
        if self._last_hb_ms is None or now - self._last_hb_ms >= self.hb_interval_ms:
            self._last_hb_ms = now
            try:
                self.service.heartbeat()
            except Exception as exc:                          # noqa: BLE001
                self._on_disconnect(exc)
                return

        try:
            self.service.pump()
        except Exception as exc:                              # noqa: BLE001
            self._on_disconnect(exc)
            return

        self._ingest_new_records()
        self._maybe_write_status()
        self._sleep(self._poll_interval_s)

    def close(self) -> None:
        """쓰던 것을 마무리하고 끝낸다. SIGINT/SIGTERM 이후 반드시 부른다."""
        if self.service is not None:
            try:
                self.service.close()
            except Exception:                                  # noqa: BLE001
                pass
            self.service = None
        self.store.close()

    # -------------------------------------------------------------- 연결 관리
    def _try_connect(self) -> None:
        try:
            transport = self._connect()
        except Exception as exc:                              # noqa: BLE001
            log.warning("연결 실패, %.1fs 뒤 재시도: %s", self._backoff_s, exc)
            self._sleep(self._backoff_s)
            self._backoff_s = min(self._backoff_s * 2, RECONNECT_BACKOFF_MAX_S)
            return

        self.service = BoardService(transport, clock=self._clock)
        self._backoff_s = RECONNECT_BACKOFF_START_S
        self._last_hb_ms = None

        if self._had_link_down:
            # 🔴 처음 연결 성공은 "재"접속이 아니다 — 끊긴 적이 있을 때만
            # link_up 을 남긴다. 그래야 이 이벤트가 항상 앞선 link_down 과
            # 짝을 이뤄, 파일을 읽는 쪽이 "이 구간은 끊겨 있었다"를 두
            # 이벤트 사이로 바로 읽는다.
            self.store.write_event("link_up", self._clock(), note="재접속")
            self._had_link_down = False
        log.info("연결됨")

    def _on_disconnect(self, exc: Exception) -> None:
        log.warning("연결 끊김: %s", exc)
        if self.service is not None:
            try:
                self.service.close()
            except Exception:                                  # noqa: BLE001
                pass
        self.service = None
        self._had_link_down = True
        self.store.write_event("link_down", self._clock(), note=str(exc))

    # -------------------------------------------------------------- 수집·저장
    def _ingest_new_records(self) -> None:
        assert self.service is not None
        for line in self.service.take_raw_lines():
            self.line_total += 1
            self.last_line_at = self._clock()

            stripped = line.strip()
            if not stripped or stripped.startswith("$"):
                # 명령 응답(`$SACK` 등)이나 하트비트 — 텔레메트리가 아니다.
                # 초당 최대 100줄인 측정 데이터와 성격이 달라 저장 대상에서
                # 뺀다(원한다면 별도 프로토콜 로그로 남기는 것이 더 맞다).
                continue

            try:
                rec = parse_record(stripped)
            except ProtocolError:
                # 🔴 깨진 줄도 원문은 버리지 않는다 — 나중에 규격 문제를
                # 되짚으려면 무엇이 왔는지가 남아야 한다. `t`는 이 줄
                # 자체에서 못 믿으므로 지금 시각(host clock)을 쓴다.
                rec = {
                    "schema_ver": 0, "seq": None, "t": self._clock(),
                    "type": "corrupt",
                }
            self.store.write(rec, stripped)
            self.record_total += 1

    def _maybe_write_status(self) -> None:
        if self.status_path is None:
            return
        now = time.monotonic()
        if now - self._last_status_write < self._status_interval_s:
            return
        self._last_status_write = now
        self._write_status_now()

    def _write_status_now(self) -> None:
        payload = json.dumps(self.status(), ensure_ascii=False, indent=2)
        tmp = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        # 🔴 원자적 치환 — 읽는 쪽(감시 스크립트 등)이 절반만 쓰인 파일을
        # 보지 않게 한다. os.replace 는 같은 파일시스템 안에서 원자적이다.
        tmp.replace(self.status_path)

    def status(self) -> dict:
        """지금 상태 요약. 로그·상태 파일 양쪽에서 쓴다."""
        out = {
            "connected": self.service is not None,
            "line_total": self.line_total,
            "record_total": self.record_total,
            "last_line_at": self.last_line_at,
            "updated_at": self._clock(),
        }
        if self.service is not None:
            t = self.service.seq_tracker
            out["received_total"] = t.received_total
            out["missing_total"] = t.missing_total
            out["resync_count"] = t.resync_count
            out["corrupt_total"] = self.service.corrupt_total
        return out


# ------------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="markon-collector",
        description="GUI 없이 보드에 붙어 계속 수집·저장한다 (Jetson 무인 운용용)",
    )
    parser.add_argument(
        "--port", required=True,
        help="시리얼 포트(예: COM23, /dev/ttyUSB0). 'sim' 이면 내장 시뮬레이터",
    )
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--data-dir", required=True, help="레코드를 쌓을 디렉터리")
    parser.add_argument(
        "--status-file", default=None,
        help="상태 JSON 을 주기적으로 쓸 경로 (생략하면 안 씀)",
    )
    parser.add_argument(
        "--retention-days", type=int, default=14,
        help="시작할 때 이보다 오래된 회전 파일을 지운다 (기본 14일)",
    )
    parser.add_argument("--hb-interval-ms", type=int, default=1000)
    return parser


def _make_connect(port: str, baud: int) -> Callable[[], Transport]:
    if port == "sim":
        # 🔴 보드 없이 수동으로 눈으로 확인해 볼 수 있게 해 둔다 — 실제
        # COM 포트는 절대 열지 않는다. 재접속마다 새 DeviceSim 을 만드는
        # 것은 "뽑았다 꽂았다"를 흉내 내지는 못하지만, sim 경로 자체가
        # 도는지 보는 용도로는 충분하다.
        from tools.simulator.config_store import default_store
        from tools.simulator.device_sim import DeviceSim

        from host.service.board_service import LoopbackTransport

        def connect_sim():
            return LoopbackTransport(DeviceSim(default_store()))

        return connect_sim

    def connect_serial():
        return SerialTransport(port, baud)

    return connect_serial


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = build_parser().parse_args(argv)

    data_dir = Path(args.data_dir)
    store = RecordStore(data_dir)

    deleted = apply_retention(
        data_dir, older_than_days=args.retention_days, now_ms=int(time.time() * 1000)
    )
    if deleted:
        log.info("보존 기간(%d일) 초과 파일 %d개 삭제", args.retention_days, len(deleted))

    status_path = Path(args.status_file) if args.status_file else None
    collector = Collector(
        _make_connect(args.port, args.baud), store,
        hb_interval_ms=args.hb_interval_ms, status_path=status_path,
    )

    def _handle_signal(signum, _frame) -> None:
        log.info("종료 신호(%s) 받음 — 쓰던 것을 마무리한다", signum)
        collector.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        # Windows 는 SIGTERM 을 대부분 못 받지만(taskkill 은 강제 종료),
        # 걸 수 있으면 걸어 둔다 — Jetson(Linux/systemd)에서는 정상 경로다.
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass

    try:
        collector.run_forever()
    finally:
        collector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
