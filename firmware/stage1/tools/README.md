# 보드 도구 — 굽기·복구·검증

## 순서

```bash
# 1. 빌드 (보드 불필요)
cd firmware/stage1 && make

# 2. 호스트 시험 (보드 불필요) — 여기서 실패하면 굽지 않는다
powershell -File tests/run_tests.ps1
python tests/crosscheck.py && python tests/crosscheck_json.py \
  && python tests/crosscheck_hostlink.py
python -m pytest host/tests -q          # 저장소 루트에서

# 3. 🔴 굽기 전에 현재 플래시를 백업한다
arm-none-eabi-gdb --batch -x tools/backup.gdb
#    -> backup_before_stage1.bin (32 KB)

# 4. 굽기
arm-none-eabi-gdb --batch -x tools/flash.gdb build/markon_stage1.elf

# 5. 실기기 검증
python tools/verify_on_board.py
```

## 🔴 시리얼을 열 때

**DTR/RTS 를 내린 채로 연다.** 세운 채 열면 보드가 멈춘다
(CLAUDE.md §4, 2026-08-14 실증).

- pyserial 을 쓰되 `s.dtr = False; s.rts = False` 를 **열기 전에** 설정
- `serial.Serial("COM23", ...)` 한 줄 생성자는 열면서 세우므로 쓰지 않는다
- PowerShell 의 `System.IO.Ports.SerialPort` 는 아예 쓰지 않는다

## 보드가 멈췄을 때

굽지 말고 리셋한다. BMP 는 대개 살아 있다.

```bash
arm-none-eabi-gdb --batch -x tools/reset.gdb
```

`seq` 나 `t` 가 처음부터 다시 올라가는 것으로 재부팅을 확인한다.

## 되돌리기

두 경로가 있다. 둘 다 같은 바이트임을 확인해 두었다
(`docs/measurements/2026-08-14_stage1_on_hardware.md`).

```bash
# (가) 굽기 직전에 뜬 백업
arm-none-eabi-gdb --batch -ex "target extended-remote \\\\.\\COM24" \
  -ex "monitor swdp_scan" -ex "attach 1" \
  -ex "restore backup_before_stage1.bin binary 0x08000000" \
  -ex "monitor reset" -ex detach -ex quit

# (나) 참고 펌웨어를 소스에서 다시 빌드
#     상위 저장소 hardware/firmware/h723_sensor_read/ 에서 make
```

## 굽는 것에 대한 약속

- **1단계는 전원 레일을 켜지 않는다.** `host/tests/test_firmware_safety.py`
  가 소스 수준에서 지키고, 역어셈블로도 확인했다(GPIOD 에 `0x800` 뿐).
- 옵션 바이트·RDP·부트로더 영역은 건드리지 않는다.
- `set remote memory-write-packet-size 256` + `fixed` 가 필수다. 없으면
  512B 청크 경계에서 데이터가 손상되는데 `compare-sections` 로도 `dump`
  로도 못 잡는다 (CLAUDE.md §4).
