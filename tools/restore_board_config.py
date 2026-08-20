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
PLAN = [
    ("pwr.24v",       "true",   "4~20mA 루프 전원 — 없으면 J3 이 0mA 로 보인다"),
    ("ain0.zero",     "4.0",    "J3 유압 (ain0 = J3, 0부터 세는 채널 번호)"),
    ("ain0.scale",    "15.625", "0~250 bar / 16 mA"),
    ("ain0.unit",     "bar",    ""),
    ("ain0.enabled",  "true",   ""),
    ("i2c12.kind",    "3",      "J12 MLX90614 적외 온도"),
    ("i2c12.addr",    "90",     "0x5A"),
    ("i2c12.enabled", "true",   ""),
    ("i2c13.kind",    "2",      "J13 AM2320 온습도"),
    ("i2c13.addr",    "92",     "0x5C"),
    ("i2c13.enabled", "true",   ""),
    ("gnss.enabled",  "true",   "UM981"),
    ("gnss.echo",     "false",  "원문 에코 끔 — gnss 정식 레코드가 있고, 안 읽는 구간의 브리지 버퍼 압력을 줄인다"),
    ("lcd.enabled",   "true",   "J25 ILI9488 화면"),
    ("ain0.name",     "유압",     "커넥터 이름 — 대시보드·스트림에 보인다"),
    ("i2c12.name",    "적외온도", ""),
    ("i2c13.name",    "온습도",   ""),
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
