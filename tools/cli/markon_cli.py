"""MarkON Studio 명령줄 도구.

GUI 없이 보드를 설정하고 텔레메트리를 확인한다. --port sim 이면 내장
시뮬레이터를 쓰므로 보드 없이도 전 기능을 시험할 수 있다.

  python -m tools.cli.markon_cli list
  python -m tools.cli.markon_cli --port COM7 get tx.period_ms
  python -m tools.cli.markon_cli --port COM7 set tx.period_ms 250
  python -m tools.cli.markon_cli --port COM7 monitor --seconds 10
"""

import argparse
import sys
import time

from host.core.errors import ProtocolError
from host.core.limits import DEFAULT_BAUD
from host.service.board_service import BoardService, LoopbackTransport, SerialTransport
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim

SIM_PORT = "sim"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="markon", description="STM32 v2.0 보드 설정·모니터 도구"
    )
    parser.add_argument(
        "--port", default=SIM_PORT,
        help="시리얼 포트 (예: COM7, /dev/ttyUSB0). 기본 'sim' = 내장 시뮬레이터",
    )
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="설정 카탈로그 출력")
    sub.add_parser("fields", help="NDJSON 필드 마스크 현황 출력")

    p_get = sub.add_parser("get", help="설정값 조회")
    p_get.add_argument("key")

    p_set = sub.add_parser("set", help="설정값 변경")
    p_set.add_argument("key")
    p_set.add_argument("value")

    p_mon = sub.add_parser("monitor", help="텔레메트리 수신")
    p_mon.add_argument("--seconds", type=float, default=5.0)

    return parser


def make_service(port: str, baud: int) -> BoardService:
    if port == SIM_PORT:
        transport = LoopbackTransport(DeviceSim(default_store()))
        start = time.monotonic()
        return BoardService(
            transport, clock=lambda: int((time.monotonic() - start) * 1000)
        )
    start = time.monotonic()
    return BoardService(
        SerialTransport(port, baud),
        clock=lambda: int((time.monotonic() - start) * 1000),
    )


# ------------------------------------------------------------------- 하위 명령
def cmd_list(svc: BoardService) -> int:
    schema = svc.fetch_schema()
    for group in schema.groups():
        print(f"\n[{group}]")
        for item in schema.items.values():
            if item.group != group:
                continue
            flags = " [읽기전용]" if item.readonly else ""
            unit = f" {item.unit}" if item.unit else ""
            print(f"  {item.key:24} = {item.current}{unit}{flags}")
            if item.note:
                print(f"  {'':24}   → {item.note}")
    print()
    return 0


def cmd_fields(svc: BoardService) -> int:
    schema = svc.fetch_schema()
    mask = int(schema.items["tx.fields"].current)
    print("NDJSON 필드 마스크")
    for bit in sorted(schema.fields):
        f = schema.fields[bit]
        state = "ON " if mask & (1 << bit) else "off"
        print(f"  bit{bit:>2}  {state}  {f.name:16} {f.label}")
    return 0


def cmd_get(svc: BoardService, key: str) -> int:
    ack = svc.send("CFG", "GET", key)
    if ack.args[-1] != "OK":
        print(f"실패: {ack.args[-1]}")
        return 1
    print(svc.last_payload["cur"])
    return 0


def cmd_set(svc: BoardService, key: str, value: str) -> int:
    # 설정 변경은 CONFIG 모드에서만 받는다 (규격 §6).
    svc.heartbeat()
    ok, reason = svc.set_config(key, value)
    if not ok:
        print(f"거부됨: {reason}")
        return 1
    print(f"{key} = {value}")
    return 0


def cmd_monitor(svc: BoardService, seconds: float) -> int:
    deadline = time.monotonic() + seconds
    seen = 0
    while time.monotonic() < deadline:
        svc.pump()
        while seen < len(svc.records):
            rec = svc.records[seen]
            seen += 1
            print(rec)
        time.sleep(0.02)

    t = svc.seq_tracker
    print(
        f"\n수신 {t.received_total}건, 누락 {t.missing_total}건, "
        f"깨짐 {svc.corrupt_total}건"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    svc = make_service(args.port, args.baud)
    try:
        if args.command == "list":
            return cmd_list(svc)
        if args.command == "fields":
            return cmd_fields(svc)
        if args.command == "get":
            return cmd_get(svc, args.key)
        if args.command == "set":
            return cmd_set(svc, args.key, args.value)
        if args.command == "monitor":
            return cmd_monitor(svc, args.seconds)
    except ProtocolError as exc:
        print(f"프로토콜 오류: {exc}", file=sys.stderr)
        return 2
    finally:
        svc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
