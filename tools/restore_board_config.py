"""굽고 나면 플래시의 설정이 지워진다 — 실기기 확인용 설정을 한 번에 되세운다.

🔴 `load` 는 설정 영역까지 지운다(실증 2026-08-19 — J12 I2C 설정이 날아갔다).
   구운 뒤 매번 손으로 여섯 개씩 넣지 않으려고 둔다. 여기 값은 **지금 실기기에
   물려 있는 것**의 값이지 규격이 아니다 — 배선이 바뀌면 여기도 바뀐다.
"""
import sys, time
sys.path.insert(0, r'D:\SourceCode\MarkON_Studio')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from host.service.board_service import SerialTransport, BoardService

# (키, 값, 무엇인가)
# [2026-08-26 전면 갱신] 최종 시험 배선: J3 유량1 / J4 유량2 / J5 유압 /
# J20 Sol / J13 온습도 / GNSS / LED 3. 값은 같은 날 실물 카탈로그 덤프에서
# 옮겼다(영점은 그날 영점 잡기 결과). ain 의 .cloud 는 이날부터 자유
# 문자열이다 — 젯슨 레코드의 type 이 그대로 이 글자가 된다.
PLAN = [
    ("pwr.24v",        "true",     "4~20mA 루프 전원 — 없으면 채널이 0mA 로 보인다"),
    ("pwr.14v9",       "true",     "젯슨 전원(J30)"),
    ("adc.drate",      "500",      "3채널×10ms 에 필요 (60이면 채널당 52ms — 실측 2026-08-26)"),
    ("tx.period_ms",   "10",       ""),
    ("tx.fields_ain",  "184",      "필드 마스크 — 사용자 선택 그대로"),
    ("tx.fields_i2c",  "128",      ""),
    ("tx.fields_gnss", "7168",     ""),
    ("ain0.enabled",   "true",     "J3 유량1"),
    ("ain0.period_ms", "10",       ""),
    ("ain0.zero",      "3.841",    "2026-08-26 영점 잡기"),
    ("ain0.scale",     "3.75",     ""),
    ("ain0.unit",      "lpm",      "젯슨 레코드의 값 필드 이름이 된다"),
    ("ain0.name",      "유량1",     "화면 표시용"),
    ("ain0.cloud",     "flow",     "젯슨 type — 사용자가 GUI 에서 flow_front 등으로 바꾼다"),
    ("ain1.enabled",   "true",     "J4 유량2"),
    ("ain1.period_ms", "10",       ""),
    ("ain1.zero",      "4.0",      ""),
    ("ain1.scale",     "3.75",     ""),
    ("ain1.unit",      "lpm",      ""),
    ("ain1.name",      "유량2",     ""),
    ("ain1.cloud",     "flow",     ""),
    ("ain2.enabled",   "true",     "J5 유압"),
    ("ain2.period_ms", "10",       ""),
    ("ain2.zero",      "3.992",    ""),
    ("ain2.scale",     "15.625",   "0~250 bar / 16 mA"),
    ("ain2.unit",      "bar",      ""),
    ("ain2.name",      "유압",      ""),
    ("ain2.cloud",     "pressure_paint", ""),
    ("din20.name",     "Sol Valve", "J20 옵토 입력"),
    # [2026-08-31] valve 레코드 발행원이 OR 합성에서 dinN.cloud 로 이동
    # (HANDOFF_0831 검토 5). 이 줄이 없으면 새 펌웨어에서 valve 가 안 나간다.
    ("din20.cloud",    "valve",    "젯슨 valve 레코드의 발행원"),
    # [2026-08-31] 온습도는 J12 다 — 8/30~31 현장에서 J13→J12 로 옮겼고,
    # 그날 주소 중복(i2c12+i2c13 둘 다 92) 사고를 정리하며 실보드 확정.
    ("i2c12.kind",     "2",        "J12 AM2320 온습도"),
    ("i2c12.addr",     "92",       "0x5C"),
    ("i2c12.enabled",  "true",     ""),
    ("i2c12.name",     "온습도",    ""),
    ("gnss.enabled",   "true",     "UM981"),
    ("gnss.echo",      "false",    "원문 에코 끔"),
    ("gnss.imu",       "true",     "UM981 RAWIMUX 10Hz → 젯슨 imu 레코드"),
    ("lcd.enabled",    "true",     "J25 ILI9488 화면"),
    ("led.count",      "3",        "상태 LED 3개 (J21~J23) — 0이면 전부 꺼진다"),
]

def main(port="COM23", extra=()):
    tr = SerialTransport(port, 921600)
    svc = BoardService(tr, clock=lambda: int(time.time()*1000))
    svc.heartbeat(); time.sleep(0.3); svc.pump()
    bad = []
    for key, val, why in list(PLAN) + list(extra):
        ok, err = svc.set_config(key, val)
        mark = "  " if ok else "🔴"
        print(f"{mark} {key:16s} = {val:8s} {why}")
        if not ok:
            bad.append((key, val, err))
    # 🔴 플래시에 저장한다. 안 하면 설정이 RAM 에만 남아, NRST 로 리셋하거나
    #    전원을 껐다 켜면 전부 기본값으로 돌아간다 — `lcd.enabled` 기본이
    #    꺼짐이라 화면이 그냥 안 나오고, 원인이 펌웨어처럼 보인다. 실제로
    #    그렇게 헤맸다(2026-08-19).
    try:
        ack = svc.send("CFG", "SAVE")
        print(f"   플래시에 저장: {ack.args}")
    except Exception as e:
        print(f"🔴 저장 실패: {e}")
        bad.append(("$CFG,SAVE", "", str(e)))

    svc.close()
    if bad:
        print("\n실패:", bad)
        return 1
    print("\n설정 복구 완료.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(*(sys.argv[1:2] or ["COM23"])))
