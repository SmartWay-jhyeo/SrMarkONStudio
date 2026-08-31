"""GUI 와 워커 스레드 사이의 명령 큐.

🔴 **왜 필요한가**

`BoardService.send()` 는 응답이 올 때까지 최대 2초 기다린다. `$CFG,LIST` 는
7 KB 라 실기기에서 실제로 그만큼 걸린다. 이걸 GUI 스레드에서 부르면 창이
그동안 얼어붙는다 — 사용자가 스핀박스를 한 칸 돌릴 때마다.

그래서 GUI 는 명령을 **넣기만 하고 즉시 반환**한다. 워커가 꺼내 보내고
결과를 되돌려 놓으면, GUI 가 타이머로 걷어 화면에 반영한다.

이 모듈은 **Qt 도 BoardService 도 import 하지 않는다.** 순수 자료구조라
스레드를 띄우지 않고 시험할 수 있다.
"""

import itertools
import threading
from dataclasses import dataclass, field

_counter = itertools.count(1)


@dataclass(frozen=True)
class Command:
    verb: str
    args: tuple[str, ...]
    #: 화면에서 합칠 때 쓰는 열쇠. 같은 항목이면 여러 번 보내도 같다.
    tag: str
    #: 이 **전송 한 번**의 고유 번호.
    #:
    #: 🔴 태그와 분리해야 한다. 같은 항목을 응답 전에 두 번 보내면 태그는
    #:    같지만 전송은 둘이다. 태그로 in-flight 를 세면 첫 응답 하나에
    #:    둘 다 끝난 것으로 처리되고, **두 번째(최신) 결과가 사라진다.**
    #:    두 번째가 거부였다면 화면은 첫 번째 성공만 보고 끝난다.
    send_id: int = 0


@dataclass(frozen=True)
class Result:
    tag: str
    ok: bool
    #: 보드가 돌려준 거부 사유(INTERLOCK/RANGE/...). 거부일 때만 채워진다.
    reason: str | None = None
    #: 조회 명령의 응답 본문.
    payload: dict | None = None
    #: 보드에 닿지 못한 경우. 거부와는 다른 사실이다.
    error: str | None = None
    #: 어느 전송에 대한 결과인가. 같은 태그를 여러 번 보냈을 때 순서를
    #: 가리는 데 쓴다 — 큰 번호가 나중에 보낸 것이다.
    send_id: int = 0


class CommandQueue:
    """제출·수거만 하는 스레드 안전 큐."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: 태그 → 아직 안 보낸 명령. dict 라 같은 태그가 덮어써진다.
        self._pending: dict[str, Command] = {}
        #: 전송 번호 → 태그. 🔴 태그 집합이 아니라 전송별로 센다.
        self._in_flight: dict[int, str] = {}
        self._results: list[Result] = []
        self._next_send_id = 1

    # ------------------------------------------------------------- GUI 쪽
    def submit(self, verb: str, *args: str, tag: str | None = None) -> str:
        """명령을 넣고 즉시 반환한다. **절대 블록하지 않는다.**

        🔴 **태그를 주면 합쳐지고, 안 주면 안 합쳐진다.**

        같은 `tag` 로 다시 넣으면 아직 안 보낸 이전 명령을 덮어쓴다.
        스핀박스를 빠르게 돌리면 중간값이 쏟아지는데, 전부 보내면 링크가
        막히고 어차피 **마지막 값만 의미가 있다** — 설정값은 최종 상태만
        중요하기 때문이다. (실측: 2000회 제출이 1회로 합쳐지고 마지막 값이
        살아남는다.)

        **합치면 안 되는 것에는 태그를 주지 않는다.** `$CFG,SAVE` 를 두 번
        누른 것은 두 번 저장하겠다는 뜻이고, 펄스 출력을 두 번 누른 것은
        두 번 내보내겠다는 뜻이다. 태그를 생략하면 매번 고유 태그가 붙어
        하나도 합쳐지지 않는다.

        판단 기준: **"마지막 것만 반영되면 되는가?"**
          - 예 → 태그를 준다 (스핀박스·체크박스·콤보 등 값 설정)
          - 아니오 → 태그를 생략한다 (SAVE·RESET·펄스 등 동작 명령)
        """
        tag = tag or f"cmd-{next(_counter)}"
        with self._lock:
            self._pending[tag] = Command(verb=verb, args=tuple(args), tag=tag)
        return tag

    def take_results(self) -> list[Result]:
        """쌓인 결과를 걷어 간다. 같은 결과가 두 번 나오지 않는다."""
        with self._lock:
            out, self._results = self._results, []
        return out

    @property
    def in_flight(self) -> int:
        with self._lock:
            return len(self._pending) + len(self._in_flight)

    @property
    def pending_tags(self) -> set[str]:
        with self._lock:
            return set(self._pending)

    # ---------------------------------------------------------- 워커 쪽
    def drain_pending(self, limit: int | None = None) -> list[Command]:
        """보낼 명령을 꺼내 간다. 꺼낸 것은 다시 나오지 않는다.

        🔴 `limit` 을 주면 그만큼만 꺼내고 **나머지는 큐에 그대로 둔다.**

        꺼낸 뒤 되돌리는 방식은 쓰지 않는다. 되돌리는 사이에 같은 태그로
        새 값이 들어오면 오래된 값이 새 값을 덮어쓰고, 되돌리려고 만든
        가짜 결과가 진짜 결과에 섞인다. 애초에 안 꺼내는 편이 안전하다.
        """
        with self._lock:
            keys = list(self._pending)
            if limit is not None:
                keys = keys[:limit]
            cmds = []
            for k in keys:
                cmd = self._pending.pop(k)
                # 🔴 전송 번호는 **꺼낼 때** 붙인다. 제출 때 붙이면 합쳐진
                #    명령들이 번호를 낭비하고, 무엇보다 실제로 나간 것과
                #    번호가 어긋난다.
                sid = self._next_send_id
                self._next_send_id += 1
                cmd = Command(verb=cmd.verb, args=cmd.args, tag=cmd.tag,
                              send_id=sid)
                self._in_flight[sid] = cmd.tag
                cmds.append(cmd)
        return cmds

    def complete(self, send_id: int, *, ok: bool, reason: str | None = None,
                 payload: dict | None = None, error: str | None = None) -> None:
        """전송 하나의 결과를 되돌려 놓는다.

        🔴 태그가 아니라 **전송 번호**로 받는다. 같은 항목을 응답 전에 두 번
           보내면 태그는 같지만 전송은 둘이고, 결과도 둘이어야 한다.

        모르는 번호는 조용히 무시한다 — 워커가 늦게 끝난 명령을 보고하거나
        같은 번호를 두 번 보고해도 큐가 깨지면 안 된다.
        """
        with self._lock:
            tag = self._in_flight.pop(send_id, None)
            if tag is None:
                return
            self._results.append(
                Result(tag=tag, ok=ok, reason=reason, payload=payload,
                       error=error, send_id=send_id)
            )
