"""보드가 파싱할 수 있는 줄의 상한.

규격: protocol/specification.md §3.1

🔴 이 값들은 취향이 아니라 하드웨어적 상한이다. 보드는 동적 할당 없이
   고정폭 버퍼로 파싱하고, **넘치는 입력을 잘라 담지 않고 조용히 버린다.**
   조용히 버리는 이유는 verb 가 버퍼에 담기지 않으면 `$SACK,<verb>,...` 를
   만들 재료 자체가 없기 때문이다(§3.1).

   그래서 호스트가 상한을 넘겨 보내면 **응답이 오지 않는다.** 오류도 아니고
   거부도 아니고 침묵이다. 원인이 프로토콜 어디에도 드러나지 않는다.
   보내기 전에 여기서 막는 것이 유일한 방어다.

펌웨어 쪽 같은 값은 `firmware/stage1/app/mk_framing.h` 에 있다. 두 곳이
어긋나지 않도록 `host/tests/test_limits.py` 가 헤더를 읽어 대조한다.
"""

# firmware/stage1/app/mk_framing.h 의 MK_LINE_MAX
MAX_PAYLOAD_BYTES = 192

# MK_VERB_MAX
MAX_VERB_BYTES = 12

# MK_ARG_MAX
MAX_ARG_BYTES = 23

# MK_ARGS_MAX — verb 는 개수에 들어가지 않는다
MAX_ARGS = 4


#: 보드와 호스트가 쓰는 UART 속도.
#:
#: 🔴 한 곳에만 둔다. 호스트와 보드가 다른 속도를 쓰면 전선에 쓰레기만
#:    흐르고, 증상은 "아무것도 안 나온다" 라 원인을 찾기 어렵다.
#:    펌웨어 쪽 값은 `firmware/stage1/main.c` 의 `UART_BAUD` 이고,
#:    `host/tests/test_limits.py` 가 둘이 같은지 대조한다.
#:
#: 921600 인 이유 (사용자 확정 2026-08-14):
#:
#:   115200 에서는 기본 설정(7채널·100 ms)이 링크의 94.8% 를 쓴다.
#:   $CFG,LIST 카탈로그 8.8 KB 를 남는 600 B/s 로 흘리면 **설정 화면
#:   여는 데 16.7초**가 걸린다 — GUI 로 설정한다는 요구가 성립하지 않는다.
#:   921600 이면 11.8% 를 쓰고 카탈로그가 0.1초에 온다.
#:   (docs/measurements/2026-08-14_link_budget.md)
#:
#: BMP 의 USB-VCP 가 이 속도를 통과시키는지는 미확인이었으나 **실기기에서
#: 확인했다** — $ID 200회 왕복에 응답 누락 0, JSON 깨짐 0, 체크섬 깨짐 0.
#: (docs/measurements/2026-08-14_baud_921600.md)
DEFAULT_BAUD = 921600
