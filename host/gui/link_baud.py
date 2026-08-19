"""링크 속도를 바꾸는 화면의 말. **Qt 를 모른다.**

🔴 **왜 이 층이 따로 있나**

   `link.baud` 는 카탈로그에 있는 다른 설정과 똑같이 생겼다 — 라벨이 있고
   단위가 있고 고를 값이 있는 열거 항목이다. 그런데 성질이 정반대다.
   다른 항목은 잘못 넣어도 다시 고칠 수 있고, 이것은 못 고친다(규격 §4.2).

   화면이 그 차이를 말해 주지 않으면 사용자는 스핀박스 돌리듯 이것을
   돌린다. 그리고 그 결과는 "GUI 가 멈췄다" 로 보이고, 사람이 할 수 있는
   판단은 전원을 뽑는 것뿐이 된다 — 10초만 기다리면 저절로 살아나는데도.

   그래서 여기에 **묻는 말 · 알리는 말 · 값에 붙는 꼬리표**를 모아 둔다.
   판정도 통신도 하지 않는다. Qt 위젯(`host/gui/qt/settings_page.py`)은
   여기서 나온 문자열을 띄우기만 한다.

🔴 **정직하게 말한다.** 921600 보다 높은 값은 아무도 시험한 적이 없다
   (규격 §4.2.5). "더 빠릅니다" 가 아니라 **"확인된 적 없습니다"** 라고
   쓴다 — 사용자가 실기기에서 시험해 보고 올리는 것이 이 항목의 용법이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from host.core.limits import (
    DEFAULT_BAUD,
    LINK_BAUD_CHOICES,
    LINK_BAUD_CONFIRM_MS,
    LINK_BAUD_KEY,
)

#: 확인 시한(초). 화면에 쓰는 숫자는 여기서만 나온다 — 규격과 갈리지 않게.
CONFIRM_S = LINK_BAUD_CONFIRM_MS // 1000

#: 채널당 10 ms × 7채널을 감당하려면 필요한 최소 속도(bps).
#:
#: 근거: 레코드 하나가 `raw`+`connector_id` 만 실어도 123 바이트이고,
#: 7채널 × 100 Hz = 700줄/초 → 86 KB/s. 8N1 은 바이트당 10비트이므로
#: 약 861 kbps 다. 여기에 여유가 없으면 첨두에서 큐가 넘치므로, 실제로
#: 성립하는 것은 1.5 Mbaud 부터다(921600 은 93 % 를 써 여유가 없다).
TEN_MS_SEVEN_CHANNELS_BAUD = 1_500_000


@dataclass(frozen=True)
class BaudOption:
    """콤보 한 줄. 값과 **그 값이 무엇인지**."""

    baud: int
    label: str
    verified: bool
    """실기기에서 확인된 적이 있는가. 🔴 지금은 기본값 하나뿐이다."""

    note: str = ""


def _option(baud: int) -> BaudOption:
    if baud == DEFAULT_BAUD:
        return BaudOption(
            baud, f"{baud:,} (기본 · 실기기 확인됨)", True,
            "$ID 200회 왕복에 누락·손상 0 — 지금까지 확인된 유일한 속도다")
    if baud < DEFAULT_BAUD:
        return BaudOption(
            baud, f"{baud:,} (느림)", False,
            "기본값보다 느리다 — 링크가 불안정할 때 내려 보는 값이다")
    if baud >= TEN_MS_SEVEN_CHANNELS_BAUD:
        return BaudOption(
            baud, f"{baud:,} (미확인 · 7채널 10 ms 가능)", False,
            "7채널을 10 ms 로 돌릴 수 있는 속도지만 실기기에서 확인된 적이 "
            "없다 — F103 브리지가 견디는지 모른다")
    return BaudOption(
        baud, f"{baud:,} (미확인)", False,
        "실기기에서 확인된 적이 없다 — F103 브리지가 견디는지 모른다")


#: 고를 수 있는 값과 그 꼬리표. 순서는 카탈로그와 같다.
OPTIONS: tuple[BaudOption, ...] = tuple(_option(b) for b in LINK_BAUD_CHOICES)


def option(baud: int) -> BaudOption | None:
    for opt in OPTIONS:
        if opt.baud == baud:
            return opt
    return None


def choice_label(baud: int) -> str:
    """콤보에 그대로 쓰는 글자. 모르는 값이면 숫자만."""
    opt = option(baud)
    return opt.label if opt is not None else f"{baud:,}"


def is_link_baud(key: str) -> bool:
    """이 키가 그 특별한 항목인가.

    🔴 이름으로 알아보는 것 말고는 방법이 없다. 카탈로그는 "이 항목을 바꾸면
       링크가 끊긴다" 를 말할 자리가 없고(그 자리를 만들면 보드가 화면의
       동작을 지시하게 된다), 규격 §4.2 가 키 이름을 계약으로 못박은 것이
       그 대신이다.
    """
    return key == LINK_BAUD_KEY


def confirm_text(old: int, new: int) -> str:
    """바꾸기 전에 사람에게 묻는 말.

    🔴 무엇이 일어나는지, 실패하면 어떻게 되는지, 얼마나 기다려야 하는지
       셋을 다 말한다. 하나라도 빠지면 사용자는 실패했을 때 전원을 뽑는다.
    """
    opt = option(new)
    lines = [
        f"호스트 링크 속도를 {old:,} → {new:,} bps 로 바꾼다.",
        "",
        "🔴 이 설정만 다르다 — 바꾸는 순간 이 대화가 오가는 선 자체가 바뀐다.",
        "",
        "  1. 보드가 옛 속도로 응답을 마친 뒤 속도를 바꾼다",
        "  2. 프로그램이 포트를 닫고 새 속도로 다시 연다",
        f"  3. {CONFIRM_S}초 안에 확인이 오가야 확정된다",
        f"  4. 안 되면 보드가 **스스로 옛 속도로 돌아온다** — "
        f"그동안({CONFIRM_S}초) 화면이 멈춘 것처럼 보인다",
        "",
        "🔴 전원을 뽑지 말고 기다린다. 확정되기 전에는 저장되지 않으므로,",
        "   실패해도 다음 부팅에 되살아나지 않는다.",
    ]
    if opt is not None and not opt.verified:
        lines += [
            "",
            f"⚠ {new:,} bps 는 **실기기에서 확인된 적이 없다.**",
            "   H723 → F103(BMP) → USB 경로가 이 속도를 견디는지 모른다.",
            "   선행 프로젝트에서는 2 Mbps 직결에 약 2 % 유실이 실측됐다.",
        ]
    lines += ["", "계속할까?"]
    return "\n".join(lines)


def outcome_text(result) -> tuple[str, bool]:
    """끝난 뒤 화면에 띄우는 말과 "나쁜 소식인가".

    `result` 는 `host.service.board_service.BaudChangeResult` 다 — 타입으로
    묶지 않고 필드만 읽는다(이 층은 서비스 계층을 몰라도 된다).
    """
    if result.ok and result.stage == "same":
        return "링크 속도가 이미 그 값이다", False
    if result.ok:
        return (f"링크 속도 {result.baud:,} bps 로 확정됐다 — "
                f"저장해야 다음 부팅에도 남는다"), False

    what = {
        "set": "보드가 변경 자체를 거부했다",
        "reopen": "포트를 새 속도로 열지 못했다",
        "confirm": "새 속도로는 보드와 말이 되지 않았다",
    }.get(result.stage, "링크 속도를 바꾸지 못했다")

    detail = result.reason or result.error
    tail = (f" — 옛 속도 {result.baud:,} bps 로 돌아왔다"
            if result.recovered
            else f" — 🔴 옛 속도({result.baud:,} bps)로도 응답이 없다. "
                 f"보드 전원을 껐다 켜야 할 수 있다")
    return f"{what}{f' ({detail})' if detail else ''}{tail}", True


def failure_hint(result) -> str:
    """실패했을 때 다음에 무엇을 해 볼지. 🔴 빈 문자열이면 할 말이 없다는 뜻."""
    if result.ok:
        return ""
    if result.stage == "set" and result.reason == "MODE":
        return ("보드가 RUN 모드다 — 하트비트가 끊겨 있다. 연결을 확인하고 "
                "다시 시도한다(규격 §6.2)")
    if result.stage == "set" and result.reason == "RANGE":
        return "이 보드의 펌웨어가 그 속도를 낼 수 없다(규격 §4.2.6)"
    if result.stage == "confirm":
        return ("이 속도는 이 배선에서 안 된다는 뜻이다. 한 단계 낮은 값을 "
                "시험해 본다 — 실패가 정보다")
    if result.stage == "reopen":
        return "포트를 다른 프로그램이 쥐고 있는지 확인한다"
    return ""
