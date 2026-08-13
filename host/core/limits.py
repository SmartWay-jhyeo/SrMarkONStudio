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
