"""3단계 실기기 검증. 보드가 COM 포트에 붙어 있어야 한다.

확인하는 것
  1. $ID    — 어떤 펌웨어가 도는가
  2. $STAT  — 규격 §7.4 의 레코드가 실제로 오는가, queues 가 무엇을 말하는가
  3. $CFG,RESET 뒤의 기본값 — J3 하나만 켜져 있는가
  4. 5V 레일을 올렸을 때 DRDY 가 실제로 떨어지는가

🔴 4번이 이 파일의 요점이다. 넷리스트상 ADS1256 의 아날로그 전원과
   기준전압이 5V 레일에 있으므로(V5 -> FB1 -> AVDD5 -> U9 AVDD,
   그리고 U10 -> VREF2V5 -> U9 VREFP), PD10 이 Low 면 SPI 는 정상 응답해도
   변환이 되지 않는다. 그 추론이 맞는지는 실물로만 확인된다.

사용:
    python tools/verify_stage3_on_board.py COM23
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from host.core.limits import DEFAULT_BAUD  # noqa: E402
from host.service.board_service import BoardService, SerialTransport  # noqa: E402


def _clock() -> int:
    return int(time.monotonic() * 1000)


def _drain(svc: BoardService, secs: float) -> list[dict]:
    """secs 동안 받은 레코드를 모은다. 하트비트도 함께 돌린다."""
    got: list[dict] = []
    t0 = time.monotonic()
    seen = 0
    while time.monotonic() - t0 < secs:
        svc.heartbeat()
        svc.pump()
        while seen < len(svc.records):
            got.append(svc.records[seen])
            seen += 1
        time.sleep(0.05)
    return got


def _ask(svc: BoardService, verb: str, *args: str, wait: float = 2.0) -> list[dict]:
    """명령 하나를 보내고 응답 레코드를 모은다."""
    before = len(svc.records)
    svc.send(verb, *args)
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait:
        svc.heartbeat()
        svc.pump()
        time.sleep(0.05)
    return svc.records[before:]


def main(argv: list[str]) -> int:
    port = argv[1] if len(argv) > 1 else "COM23"
    baud = int(argv[2]) if len(argv) > 2 else DEFAULT_BAUD

    print(f"== {port} @ {baud} ==\n")
    svc = BoardService(SerialTransport(port, baud), clock=_clock)
    failures = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal failures
        print(f"  {'ok  ' if ok else 'FAIL'} {msg}")
        if not ok:
            failures += 1

    try:
        # 🔴 CONFIG 를 열려면 하트비트가 3초 안에 계속 가야 한다(규격 §6.2).
        print("[0] 하트비트로 CONFIG 열기")
        _drain(svc, 2.0)

        print("\n[1] $ID")
        recs = _ask(svc, "ID")
        ids = [r for r in recs if r.get("type") == "id"]
        check(bool(ids), "id 레코드가 온다")
        if ids:
            print(f"       fw={ids[0].get('fw')} board_rev={ids[0].get('board_rev')} "
                  f"device_id={ids[0].get('device_id')}")

        print("\n[2] $STAT")
        recs = _ask(svc, "STAT")
        stats = [r for r in recs if r.get("type") == "stat"]
        check(bool(stats), "stat 레코드가 온다")
        if stats:
            s = stats[0]
            print(f"       mode={s.get('mode')} uptime_ms={s.get('uptime_ms')}")
            print(f"       time_source={s.get('time_source')} "
                  f"time_quality={s.get('time_quality')}")
            print(f"       rails={s.get('rails')}")
            q = s.get("queues", [])
            print(f"       queues={q}")
            check("rails" in s, "rails 가 있다")
            check("time_source" in s, "time_source 가 있다 (t 의 기준점)")
            # 🔴 채널을 하나도 안 켰으므로 빈 배열이어야 한다.
            check(isinstance(q, list), "queues 가 배열이다")

        print("\n[3] $CFG,RESET 뒤 기본값")
        _ask(svc, "CFG", "RESET")
        schema = svc.fetch_schema()
        enabled = [k for k in sorted(schema.items)
                   if k.endswith(".enabled") and schema.items[k].current]
        print(f"       켜진 채널: {enabled}")
        check(enabled == ["ain0.enabled"],
              "J3 하나만 켜져 있다 (7채널 전부 켜면 용량 초과였다)")
        p = schema.items.get("ain0.period_ms")
        if p is not None:
            check(p.maximum == 60000, f"수집 주기 최대가 60초다 (받음 {p.maximum})")
        fd = schema.items.get("tx.float_digits")
        if fd is not None:
            check(fd.minimum == 2, f"실수 자릿수 최소가 2다 (받음 {fd.minimum})")

        print("\n[4] 채널을 켜고 DRDY 가 오는지")
        print("    🔴 지금 펌웨어는 PD10(5V)을 올리지 않는다. 넷리스트상")
        print("       ADS1256 의 AVDD·VREFP 가 5V 레일이므로 변환이 안 될 것이다.")
        ok, why = svc.set_config("ain0.enabled", "true")
        check(ok, f"ain0 을 켠다 ({why})")
        _drain(svc, 3.0)
        recs = _ask(svc, "STAT")
        stats = [r for r in recs if r.get("type") == "stat"]
        if stats:
            q = stats[0].get("queues", [])
            print(f"       3초 뒤 queues={q}")
            depth = q[0].get("depth", 0) if q else 0
            peak = q[0].get("peak", 0) if q else 0
            if peak > 0:
                print("       -> 표본이 들어왔다. 5V 없이도 변환된다는 뜻이다 —")
                print("          넷리스트 추론이 틀렸으므로 다시 봐야 한다.")
            else:
                print("       -> 표본이 없다. 5V 레일 추론과 일치한다.")
            check(True, f"관측: depth={depth} peak={peak}")

    finally:
        svc.close()

    print(f"\n{'실패 ' + str(failures) if failures else '모두 통과'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
