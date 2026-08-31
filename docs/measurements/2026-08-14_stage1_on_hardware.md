# 1단계 펌웨어 실기기 검증 (2026-08-14) [실증]

**처음으로 MarkON Studio 펌웨어를 보드에 구웠다.** 계약이 호스트와 보드
사이에서 실제로 맞는지가 여기서 판가름 났다.

## 굽기

```
build/markon_stage1.bin   11,840 바이트
text 11,808  data 32  bss 2,528
```

굽기 전에 현재 플래시를 통째로 읽어 두었다.

```
dump binary memory backup_before_stage1.bin 0x08000000 0x08008000
```

**백업 32,768 바이트 중 앞 24,928 바이트가 소스 재빌드본
(`h723_sensor_read.bin`)과 100% 일치**했고 나머지는 `0xFF`(빈 플래시)였다.
복구 경로가 둘이다 — 백업 파일과 소스 재빌드본.

굽기는 `load` + `compare-sections` 로 전 섹션 일치를 확인했다.
`set remote memory-write-packet-size 256` + `fixed` 를 썼다 (CLAUDE.md §4).

## 검증 결과 — 전부 통과

| 확인한 것 | 결과 |
|---|---|
| 보드가 `$HB` 를 1 Hz 로 낸다 (§6.1) | 3.5초에 4개 |
| 그 `$HB` 의 체크섬이 맞다 | `$HB*0A` |
| 텔레메트리를 내지 않는다 (ADS1256 없음) | 없음 |
| `$ID` → id 레코드 + `$SACK,ID,OK` (§5.2) | 순서까지 일치 |
| `schema_ver` 3, `type` id, `fw` 0.1.0, `board_rev` 2.0 | 전부 일치 |
| 명령 응답의 `seq` 가 0 (§5.2) | 0 |
| `$STAT` → `ERR,UNSUPPORTED` | `$SACK,STAT,ERR,UNSUPPORTED*34` |
| 체크섬 깨진 `$HB` 에 `$SACK` 없음 (§3 예외) | 없음 |
| 체크섬 깨진 `$ID` 에 `ERR,CHECKSUM` | `$SACK,ID,ERR,CHECKSUM*73` |

실제로 받은 줄:

```
{"schema_ver":3,"seq":0,"t":38080,"type":"id","device_id":"1","fw":"0.1.0","board_rev":"2.0"}
$SACK,ID,OK*13
$SACK,STAT,ERR,UNSUPPORTED*34
$SACK,ID,ERR,CHECKSUM*73
$HB*0A
```

**이 바이트들이 호스트 시험에서 기대하던 것과 같다.** 프레이밍·체크섬·
NDJSON 세 계층이 실기기에서 맞는다는 뜻이다.

## 모드 전환

`$HB` 를 1 Hz 로 보내는 동안과 멈춘 뒤 모두 보드가 계속 살아서 `$HB` 를
냈고, 다시 `$ID` 를 물었을 때 정상 응답했다.

1단계에는 `$STAT` 이 없어 모드를 물어볼 수 없다. 그래서 **LED 깜빡임
주기**를 시각 피드백으로 넣었다 — RUN 2초, CONFIG 0.5초. 눈으로 확인해야
하는 항목이라 여기서는 [미확인] 으로 남긴다.

**모드 판정 로직 자체는 호스트에서 시험된다** (`test_hostlink.c`, 41건).
경계가 `>` 인지 `>=` 인지까지 시뮬레이터와 대조했다.

## 전원 레일 [실증]

**켜지 않았다.** 굽고 나서 24V·14.9V·5V 어느 것도 인가하지 않는다.
역어셈블에서 GPIOD 에 나가는 값이 `0x800`(PD11, 상태 LED) 뿐임을 확인했고,
`host/tests/test_firmware_safety.py` 가 소스 수준에서 같은 것을 지킨다.

## 남은 것

- LED 주기로 모드 전환을 눈으로 확인 [미확인]
- baud 를 올릴 수 있는지 (BMP 의 USB-VCP 통과 여부) [미확인] —
  `docs/measurements/2026-08-14_link_budget.md` 참조
