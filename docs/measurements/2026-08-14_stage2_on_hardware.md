# 2단계 설정 통신 실기기 검증 (2026-08-14) [실증]

**GUI 에서 바꾼 설정이 실물 보드에 들어갔다.** 사용자 요구의 핵심 흐름이
끝에서 끝까지 동작한다.

## 결과 — 전부 통과

```
$CFG,LIST    56줄 (cfg_item 45 + cfg_field 10 + cfg_end 1)
             호스트 parse_catalog 가 그대로 읽는다 — 항목 45, 필드 10
             cfg_end.count 55 = 실제 55 (잘리지 않았다)
$CFG,GET     {"type":"cfg_value","key":"tx.period_ms","cur":100}
             모르는 키 -> $SACK,CFG,ERR,UNKNOWN_KEY

RUN 에서 SET     -> $SACK,CFG,ERR,MODE
CONFIG 에서 SET  -> $SACK,CFG,OK, 값이 실제로 바뀐다
범위 밖          -> $SACK,CFG,ERR,RANGE
pwr.5v 끄기      -> $SACK,CFG,ERR,INTERLOCK
```

## 저장이 진짜인지

처음 검증에서 `cur:250` 이 처음부터 나왔다. 이전 세션의 저장이 살아남은
것이지만, **그 상태에서 250 을 다시 쓰고 250 을 확인하면 아무것도 증명하지
못한다** — 저장이 안 돼도 통과한다. 그래서 따로 확인했다
(`tools/verify_persist_on_board.py`).

```
RESET -> SAVE -> 리셋 -> 100     기본값이 저장된다
SET 777 -> SAVE -> 리셋 -> 777   새 값이 저장된다
SET 1234 (SAVE 없이) -> 리셋 -> 777   저장 안 한 것은 안 남는다
dev.id = "boardX" -> SAVE -> 리셋 -> "boardX"   문자열도 남는다
```

세 번째 줄이 요점이다. 저장하지 않은 변경이 살아남는다면 그것은 저장이
아니라 우연이다.

## GUI 에서

```
상단 식별: COM23 · id 1 · fw 0.1.0 · rev 2.0
설정 화면: 45개 항목이 $CFG,LIST 만으로 그려진다
           5V 전원이 비활성 + "쿨링 팬이 5V 전원에 직결이라 끌 수 없다"
GUI 에서 tx.period_ms 를 333 으로 바꾸고 [보드에 적용]
  -> 보드: {"key":"tx.period_ms","cur":333}
```

## 겪은 것 — 굽기가 계속 실패했다

11 KB 짜리 1단계는 여러 번 깨끗이 구워졌는데, 20 KB 로 커진 2단계에서
5회 중 4회가 실패했다.

```
Error writing data to flash
Error erasing flash with vFlashErase packet
Remote connection closed
Attaching to Remote target failed: FF
```

### 알아낸 것 1 — `load` 앞에 `monitor reset` 을 넣으면 안 된다

`.text` 가 MIS-MATCHED 로 반쯤 써지자 펌웨어가 HardFault 로 들어갔다.
그 상태에서 `attach` 앞뒤로 리셋을 넣었더니 **죽은 코드가 다시 달리기
시작해** 다음 굽기를 계속 막았다. `attach` 는 코어를 멈춘 채 붙으므로
그대로 두고 굽는 것이 맞다.

### 알아낸 것 2 — `monitor connect_rst enable`

깨진 펌웨어가 붙기도 전에 실행되면 `attach` 자체가 실패한다. 리셋을 건
채로 접속하면 코드가 돌지 않아 언제나 붙을 수 있다. 정상일 때 손해가
없으므로 굽기 기본값으로 두었다.

### 🔴 알아낸 것 3 — 이 보드는 USB 로 전원을 받지 않는다

USB 를 뽑았다 꽂아도 **보드가 리셋되지 않았다.** GDB 로 `uwTick` 을 읽어
확인했다 — 재연결 전후로 가동 시간이 이어졌다(28분 → 30.5분).

이것이 중요한 이유는, F103(BMP)의 UART 브리지가 멈췄을 때 **USB 재연결로는
풀리지 않기 때문**이다. F103 도 전원을 유지하므로 리셋되지 않는다.

그때 H723 쪽은 완전히 정상이었다 — GDB 로 확인한 것:

```
mk_uart_write(data="$HB*0A\r\n", len=8)   중단점 도달
USART3 CR1  = 0x2D    UE·RE·TE·RXNEIE 켜짐
USART3 BRR  = 69      921600 (64 MHz / 69 = 927,536, 오차 +0.64%)
USART3 ISR  = 0x6000D0  TC·TXE 세워짐 = 전송 완료
GPIOB AFRH  = 0x7700  PB10·PB11 = AF7 (USART3)
```

바이트는 PB10 으로 나갔고 F103 이 넘겨 주지 않았다. baud 를 9600 부터
921600 까지 바꿔가며 CDC 재설정을 유도해도 풀리지 않았다.

**보드 전원을 완전히 끊었다 넣으니 즉시 살아났다.**

→ 시리얼이 조용한데 H723 이 정상이면, USB 가 아니라 **보드 전원**을
  껐다 켜야 한다.

## 코드에서 고친 것

`mk_flash.c` 의 staging 버퍼를 512 로 어림잡았는데 실제 저장 덩어리는
45항목 × 24바이트 = **1,080 바이트**였다. `$CFG,SAVE` 가 `ERR,BUSY` 로
떨어지고 나서야 알았다 — 실기기에서만 드러나는 종류다.

2048 로 키우고, 같은 어림 실수가 반복되지 않게 컴파일 시점 검사를 넣었다.

```c
_Static_assert(sizeof(MkValue) * ITEM_COUNT + 32u <= 2048u,
               "저장 덩어리가 mk_flash.c 의 staging 버퍼를 넘는다");
```

되돌려 512 로 낮추면 컴파일이 실패하는 것을 확인했다.

## 아직 확인 안 한 것

- ADS1256 이 들어온 뒤의 실제 부하 [미확인] — 지금은 링크가 거의 비어 있다
- Jetson 직결(J29)에서의 921600 [미확인] — BMP 를 거치지 않으므로 별개다
- 굽기 실패가 20 KB 부터 잦아진 이유 [미확인] — 전압이 3.15~3.37 V 로
  흔들렸다. BMP 쪽 안정성으로 보이나 원인을 특정하지 못했다.
