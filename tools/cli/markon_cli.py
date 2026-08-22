"""MarkON Studio 명령줄 도구.

GUI 없이 보드를 설정하고 텔레메트리를 확인한다. **보드가 있어야 한다** —
내장 시뮬레이터는 없앴다(2026-08-20).

  python -m tools.cli.markon_cli --port COM23 list
  python -m tools.cli.markon_cli --port COM23 get tx.period_ms
  python -m tools.cli.markon_cli --port COM23 set tx.period_ms 250
  python -m tools.cli.markon_cli --port COM23 monitor --seconds 10
"""

import argparse
import sys
import time

from host.core.errors import ProtocolError
from host.core.limits import DEFAULT_BAUD, LINK_BAUD_CHOICES
from host.service.board_service import (
    RETIRED_SIM_PORT,
    SIM_REMOVED_MSG,
    BoardService,
    SerialTransport,
)
from host.storage.query import DEFAULT_MAX_AGE_MS, find_nearest, query_range


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="markon", description="STM32 v2.0 보드 설정·모니터 도구"
    )
    # 🔴 기본값을 두지 않는다. 예전 기본값이 `sim` 이었으므로, 아무 생각 없이
    #    실행했을 때 조용히 가짜 보드에 붙는 대신 "포트를 지정해라" 로 멈춘다.
    parser.add_argument(
        "--port", required=True,
        help="시리얼 포트 (예: COM23, /dev/ttyUSB0)",
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

    # 🔴 `set link.baud` 로는 안 된다 (규격 §4.2). 값을 넣는 것으로 끝나지
    #    않고 포트를 새 속도로 다시 열어 확인까지 보내야 하며, 실패하면
    #    옛 속도로 돌아와야 한다. 전용 하위 명령으로 갈라 두면 `set` 이
    #    실수로 링크를 끊는 일이 없다.
    p_baud = sub.add_parser(
        "baud", help="호스트 링크 속도 변경 (규격 §4.2 의 확인 절차를 밟는다)")
    p_baud.add_argument("value", type=int, choices=list(LINK_BAUD_CHOICES))

    p_query = sub.add_parser(
        "query", help="저장된 레코드를 시각 범위(epoch_ms, 양끝 포함)로 조회"
    )
    p_query.add_argument("--data-dir", required=True, help="RecordStore 가 쓴 디렉터리")
    p_query.add_argument("--start", type=int, required=True, help="epoch_ms")
    p_query.add_argument("--end", type=int, required=True, help="epoch_ms")
    p_query.add_argument("--type", dest="type", default=None, help="ain/i2c/din/stat/...")
    p_query.add_argument("--connector-id", type=int, default=None)
    p_query.add_argument("--quantity", default=None, help="i2c 전용 (lux/temp/...)")

    p_near = sub.add_parser(
        "nearest", help="주어진 시각(epoch_ms)에 가장 가까운 레코드 조회"
    )
    p_near.add_argument("--data-dir", required=True)
    p_near.add_argument("--t", type=int, required=True, help="epoch_ms")
    p_near.add_argument("--type", dest="type", default=None)
    p_near.add_argument("--connector-id", type=int, default=None)
    p_near.add_argument("--quantity", default=None)
    p_near.add_argument(
        "--max-age-ms", type=int, default=DEFAULT_MAX_AGE_MS,
        help=f"이보다 오래된 값은 stale 로 표시 (기본 {DEFAULT_MAX_AGE_MS})",
    )

    return parser


def make_service(port: str, baud: int) -> BoardService:
    if port == RETIRED_SIM_PORT:
        raise SystemExit(SIM_REMOVED_MSG)
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


#: 레코드 종류 → 자기 마스크 설정 키. `tx.fields` 를 나눈 세 항목이다
#: (규격 §7.2·§7.5·§7.6).
_FIELD_MASK_KEYS = {"ain": "tx.fields_ain", "i2c": "tx.fields_i2c",
                    "din": "tx.fields_din"}


def cmd_fields(svc: BoardService) -> int:
    """🔴 [개정, 2026-08-19] `tx.fields` 하나였던 것이 셋으로 나뉘어,
    레코드 종류마다 절을 나눠 찍는다 — 비트가 해당 레코드에 없으면
    그 절에는 아예 안 보여 준다(schema.fields[bit].records)."""
    schema = svc.fetch_schema()
    for kind, key in _FIELD_MASK_KEYS.items():
        item = schema.items.get(key)
        mask = int(item.current) if item is not None else 0
        print(f"NDJSON 필드 마스크 ({kind})")
        for bit in sorted(schema.fields):
            f = schema.fields[bit]
            if kind not in f.records:
                continue
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


def cmd_baud(svc: BoardService, value: int) -> int:
    """호스트 링크 속도를 바꾼다 (규격 §4.2).

    🔴 **실기기에서 새 속도를 처음 시험하는 자리가 여기다.** GUI 보다 이쪽이
       낫다 — 창도 워커 스레드도 없어서, 안 됐을 때 무엇이 안 됐는지가
       한 줄로 나온다. 실패해도 보드는 10초 뒤 스스로 옛 속도로 돌아온다.
    """
    # 설정 변경은 CONFIG 모드에서만 받는다 (규격 §6).
    svc.heartbeat()
    result = svc.change_baud(value)
    if result.ok:
        print(f"링크 속도 {result.baud} bps 로 확정됐다.")
        # 🔴 확정과 저장은 다른 일이다. 확정은 "지금 이 대화가 이 속도로
        #    된다" 이고, 저장은 "다음 부팅에도 그렇다" 다. 여기서 저장까지
        #    해 주지 않는 이유: 새 속도를 **얼마간 써 보고** 저장하는 것이
        #    이 항목의 용법이다(규격 §4.2.5 — 아무도 시험한 적이 없다).
        print("아직 Flash 에는 없다 — 다음 부팅에도 남기려면 GUI 설정 화면에서 "
              "저장한다. 그 전에 이 속도로 한동안 돌려 보는 편이 낫다.")
        return 0

    detail = result.reason or result.error or "이유를 모른다"
    print(f"실패({result.stage}): {detail}", file=sys.stderr)
    if result.recovered:
        print(f"옛 속도 {result.baud} bps 로 돌아왔다 — 보드는 살아 있다.",
              file=sys.stderr)
    else:
        print(f"🔴 옛 속도({result.baud} bps)로도 응답이 없다. "
              f"보드 전원을 껐다 켜야 할 수 있다(CLAUDE.md §4).", file=sys.stderr)
    return 1


def cmd_monitor(svc: BoardService, seconds: float) -> int:
    deadline = time.monotonic() + seconds
    last_hb = 0.0
    while time.monotonic() < deadline:
        # 🔴 텔레메트리는 HB 가 신선할 때만 나온다 (규격 §7.1.3, 2026-08-22).
        #    안 보내면 3초 뒤 스트림이 조용해지는데, 그게 게이트의 정상
        #    동작이라 오류로도 안 보인다.
        if time.monotonic() - last_hb >= 1.0:
            svc.heartbeat()
            last_hb = time.monotonic()
        svc.pump()
        # 🔴 `svc.records[seen:]` 커서는 안 쓴다. `records` 가
        #    `deque(maxlen=...)` 가 되면서(host/service/board_service.py)
        #    누적 수신량이 그 상한을 넘는 순간 `seen` 이 실제 길이를 앞질러
        #    IndexError 가 난다. `take_records()` 는 상한과 무관하게
        #    "이번에 새로 온 것" 만 돌려주고 스스로 비운다.
        for rec in svc.take_records():
            print(rec)
        time.sleep(0.02)

    t = svc.seq_tracker
    print(
        f"\n수신 {t.received_total}건, 누락 {t.missing_total}건, "
        f"깨짐 {svc.corrupt_total}건"
    )
    return 0


def cmd_query(data_dir, start: int, end: int, *, type, connector_id, quantity) -> int:  # noqa: A002
    """저장소에서 시각 범위를 조회해 한 줄씩 찍는다. 보드가 필요 없다."""
    rows = query_range(
        data_dir, start, end, type=type, connector_id=connector_id, quantity=quantity
    )
    for row in rows:
        print(row)
    print(f"\n{len(rows)}건")
    return 0


def cmd_nearest(data_dir, t: int, *, type, connector_id, quantity,  # noqa: A002
                 max_age_ms: int) -> int:
    """저장소에서 t 에 가장 가까운 레코드를 찾아 찍는다.

    🔴 stale(너무 묵음)이면 값을 찾았어도 종료 코드를 1 로 돌려준다 —
    카메라 프레임 보정 같은 자동화 스크립트가 "값은 있었다"와 "쓸 만큼
    가까운 값이었다"를 종료 코드만으로 구분할 수 있어야 한다.
    """
    result = find_nearest(
        data_dir, t, type=type, connector_id=connector_id, quantity=quantity,
        max_age_ms=max_age_ms,
    )
    # 🔴 em dash(—) 등을 쓰지 않는다. Windows 콘솔이 cp949(한글) 코드페이지면
    # 이모지·특수 유니코드 기호에서 UnicodeEncodeError 로 죽는다 — 실제로
    # 겪었다. 순수 ASCII 로만 구두점을 쓴다.
    if not result.found:
        print("찾지 못함: 그 근처에 레코드가 없다")
        return 1
    print(result.record)
    print(f"차이 {result.age_ms}ms" + (" (너무 묵었다 - stale)" if result.stale else ""))
    return 1 if result.stale else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # query·nearest 는 보드를 안 본다 — 서비스를 만들기 전에 갈라진다.
    if args.command == "query":
        return cmd_query(
            args.data_dir, args.start, args.end,
            type=args.type, connector_id=args.connector_id, quantity=args.quantity,
        )
    if args.command == "nearest":
        return cmd_nearest(
            args.data_dir, args.t,
            type=args.type, connector_id=args.connector_id, quantity=args.quantity,
            max_age_ms=args.max_age_ms,
        )

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
        if args.command == "baud":
            return cmd_baud(svc, args.value)
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
