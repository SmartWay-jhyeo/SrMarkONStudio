# MarkON Studio 시리얼 프로토콜 규격 v3

**대상:** STM32 v2.0 (STM32H723ZGT6) ↔ 호스트 (Windows / Jetson)
**설계 근거:** `docs/superpowers/specs/2026-08-13-config-control-gui-design.md`

## 1. 물리 계층

| 채널 | 경로 | 용도 |
|---|---|---|
| 설정·제어 | H723 USART3 (PB10/PB11) → F103 USART2 (PA2/PA3) → USB-C (J2) | GUI |
| 데이터 | H723 USART2 (PA2/PA3) / USART1 (PA9/PA10) → J29 | Jetson |

두 채널은 물리적으로 분리돼 있어 동시에 살아 있어도 서로 방해하지 않는다.

⚠️ 상용 Black Magic Probe 펌웨어의 VCP는 F103 USART1(PB6/PB7)에 묶여 있고 그
핀은 **미연결**이다. USBUSART를 USART2(PA2/PA3)로 바꿔 재빌드해야 USB 한
가닥으로 설정 채널이 열린다.

## 2. 줄 형식

두 종류의 줄이 같은 스트림에 섞여 흐른다. 첫 글자로 구분한다.

| 첫 글자 | 종류 |
|---|---|
| `$` | 명령 / 응답 / 하트비트 |
| `{` | NDJSON 텔레메트리 또는 설정 응답 |

모든 줄은 `\r\n`으로 끝난다. 수신측은 `\n`만 와도 받아들인다.

## 3. `$` 줄 — 체크섬

```
$<PAYLOAD>*<CS>\r\n
```

`<CS>`는 `<PAYLOAD>` 전체 문자의 XOR을 **대문자 2자리 16진수**로 쓴 것이다
(NMEA 0183과 같은 방식). `$`와 `*`는 계산에 포함하지 않는다.

검증 벡터:

| 줄 | PAYLOAD | XOR |
|---|---|---|
| `$HB*0A` | `HB` | `0x48 ^ 0x42 = 0x0A` |
| `$ID*0D` | `ID` | `0x49 ^ 0x44 = 0x0D` |

체크섬이 맞지 않는 줄은 폐기하고 `$SACK,<verb>,ERR,CHECKSUM`을 보낸다.
verb를 읽을 수 없을 만큼 깨졌으면 조용히 버린다 (링크는 유지).

## 4. 호스트 → 보드 명령

| 명령 | 인자 | CONFIG 전용 |
|---|---|---|
| `$HB` | — | 아니오 |
| `$ID` | — | 아니오 |
| `$CFG,LIST` | — | 아니오 |
| `$CFG,GET` | `<key>` | 아니오 |
| `$CFG,SET` | `<key>,<value>` | **예** |
| `$CFG,SAVE` | — | **예** |
| `$CFG,RESET` | — | **예** |
| `$STAT` | — | 아니오 |

## 5. 보드 → 호스트 응답

```
$SACK,<verb>,OK*<CS>
$SACK,<verb>,ERR,<reason>*<CS>
```

`<verb>`는 요청한 명령의 첫 토큰(`CFG`, `ID`, `STAT`)이다.

| reason | 의미 |
|---|---|
| `RANGE` | 값이 허용 범위 밖 |
| `UNKNOWN_KEY` | 존재하지 않는 설정 키 |
| `READONLY` | 읽기 전용 항목 |
| `INTERLOCK` | 안전 정책상 거부 |
| `MODE` | RUN 모드에서 설정 변경 시도 |
| `CHECKSUM` | 체크섬 불일치 |
| `BUSY` | Flash 쓰기 중 |

검증 순서는 위 표의 순서가 아니라 다음과 같다. 값이 틀린 것과 안전상
거부된 것은 사용자에게 다른 메시지여야 하므로 인터록을 범위 검사 뒤에 둔다.

```
체크섬 → 모드 → 키 존재 → 타입·범위 → 인터록 → 읽기 전용 → 수락
```

🔴 **인터록이 읽기 전용보다 우선한다.** `pwr.5v`처럼 둘 다 해당하는 항목은
`READONLY`가 아니라 `INTERLOCK`을 반환한다. 사용자가 "왜 안 되는지"를 알아야
하기 때문이다 — `note`에 담긴 "쿨링 팬이 5V 레일 직결"이라는 하드웨어 사실이
곧 사유가 된다. `READONLY`만 돌려주면 그 이유가 사라진다.

**현재값과 같은 값을 쓰는 것은 거부하지 않는다.** `pwr.5v`를 `true`로 두는
요청은 아무것도 바꾸지 않으므로 `OK`다. 호스트가 전체 설정을 한꺼번에
되돌려 쓸 때 불필요한 거부가 나지 않게 한다.

## 6. 모드

| 모드 | 진입 조건 |
|---|---|
| `CONFIG` | 호스트 `$HB`를 3000 ms 안에 받고 있음 |
| `RUN` | `$HB`가 3000 ms 이상 끊김. 부팅 직후 기본값 |

H723은 USB 연결을 감지할 수 없다(VBUS가 MCU에 배선되지 않음). 따라서 모드는
케이블이 아니라 하트비트로 판정한다. 설정을 바꾸려는 호스트는 `$HB`를 1 Hz로
보내야 한다.

수집과 전송은 **두 모드에서 모두 계속된다.** 모드가 바꾸는 것은 설정 변경
수락 여부뿐이다.

## 7. NDJSON

### 7.1 공통 필드

`seq`, `t`, `type`, `schema_ver`는 항상 포함된다. 필드 마스크로 끌 수 없다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `schema_ver` | int | 항상 3 |
| `seq` | uint32 | 레코드마다 1씩 증가. 호스트가 누락을 검출한다 |
| `t` | int64 | epoch_ms |
| `type` | str | 레코드 종류 |

### 7.2 텔레메트리 (`type` = `ain`)

```json
{"schema_ver":3,"seq":1234,"t":1772200855875,"type":"ain","connector_id":3,
 "raw":8388608,"ma":12.0041,"value":3.4210,"unit":"bar","status":0,
 "device_id":"1","time_source":"device_clock","time_quality":0,
 "capture_counter":123456789}
```

| 필드 | 마스크 비트 | 기본 | 의미 |
|---|---|---|---|
| `device_id` | 0 | off | 보드 식별자 |
| `time_source` | 1 | on | `gnss`/`gnss_nmea`/`host_clock`/`device_clock` |
| `time_quality` | 2 | off | 0=미동기 |
| `raw` | 3 | on | ADS1256 원시 카운트 (int32) |
| `ma` | 4 | on | 전류 환산 (mA) |
| `value` | 5 | on | 물리량 환산 |
| `unit` | 6 | off | 단위 문자열 |
| `status` | 7 | on | 0=정상 |
| `capture_counter` | 8 | off | 획득 순간 타이머 값 |
| `connector_id` | 9 | on | 3~9 (J3~J9) |

🔴 **`raw`가 원본이다.** `ma`·`value`는 편의용 파생값이며 `tx.float_digits`
자릿수로 반올림된다. 정밀도가 필요한 분석은 `raw`를 쓴다.

### 7.3 설정 카탈로그 (`$CFG,LIST` 응답)

한 항목당 한 줄, 마지막에 종료 줄.

```json
{"schema_ver":3,"seq":0,"t":0,"type":"cfg_item","key":"tx.period_ms","grp":"tx","vtype":"u16","min":10,"max":10000,"default":100,"cur":100,"unit":"ms","ro":false,"label":"전송 주기"}
{"schema_ver":3,"seq":0,"t":0,"type":"cfg_field","bit":3,"name":"raw","default":true,"label":"ADS1256 원시 카운트"}
{"schema_ver":3,"seq":0,"t":0,"type":"cfg_end","count":42}
```

`vtype`: `bool` | `u8` | `u16` | `u32` | `f32` | `str` | `enum`
`enum`인 경우 `choices` 배열이 함께 온다.
`ro`가 `true`면 변경 불가이며 `note`에 이유가 담긴다.
`cfg_end`의 `count`는 **`cfg_item` + `cfg_field` 합계**다. 수신측은 이를
대조해 전송이 중간에 잘렸는지 판정한다.

**호스트는 설정 항목을 하드코딩하지 않는다.** 이 응답만으로 화면을 구성한다.

### 7.4 상태 (`$STAT` 응답)

```json
{"schema_ver":3,"seq":0,"t":1772200855875,"type":"stat","mode":"CONFIG",
 "fw":"0.1.0","board_rev":"2.0","uptime_ms":123456,
 "rails":{"v24":false,"v14v9":false,"v5":true},
 "queues":[{"ch":0,"depth":0,"peak":3,"drops":0}]}
```

`rails` 값은 **명령 상태**이지 실측이 아니다. 피드백 회로가 없으므로 호스트는
`정상 ON`이 아니라 `ON 명령됨`으로 표시해야 한다.

## 8. 호환성

`schema_ver`가 올라가면 수신측은 모르는 필드를 무시하고 없는 필드는 기본값으로
채운다. 필드 삭제는 major 버전 상승을 요구한다.

> **참고:** Q2 `host_link.h` 주석에는 `$HB*0F`로 적혀 있으나 NMEA XOR 계산으로는
> `0x0A`다. Q2 주석이 오래됐거나 다른 계산을 쓴 것으로 보인다. **우리는 위 검증
> 벡터를 기준으로 하고 펌웨어도 여기에 맞춘다.**
