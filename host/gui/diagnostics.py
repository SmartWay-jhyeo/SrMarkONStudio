"""`$STAT` 을 사람이 읽는 말로 옮긴다. **Qt 를 모른다.**

🔴 **왜 이 층이 따로 있나**

   보드는 `$STAT` 하나로 많은 것을 말한다 — 시간축을 무엇으로 보간하는지
   (`clock.src`), PPS 펄스가 실제로 오는지(`pps_raw_count`), 표본을 버렸는지
   (`queues[].drops`), 화면이 몇 번 깨졌다 되살아났는지(`lcd.reinit`).
   그런데 이 값들은 **그대로 보여 주면 아무 뜻도 없다.** `"hsi"` 라는 글자
   넉 자가 "초 안쪽 시각이 ±1 % 흔들린다" 는 뜻이라는 것을 화면 앞의 사람이
   알 수는 없다.

   실제로 그래서 헤맸다(2026-08-19): `pps_age_ms` 가 `null` 인 것만 보고
   "PPS 가 안 온다"고 배선을 의심해 GDB 로 TIM8 CCR3 을 직접 읽었는데,
   펄스는 정확히 1초 간격으로 들어오고 있었고 **짝지을 유효 RMC 가 없었을
   뿐**이었다(규격 §7.4). 그 구분을 화면이 말해 줬으면 GDB 를 안 붙였다.

🔴 **정상인 것을 경고로 만들지 않는다.**

   센서 미연결은 정상이고(설계 원칙 3), 실내에서 위성이 안 잡히는 것도
   정상이며, 되읽기가 안 되는 3.5" LCD 모듈도 정상이다. 이런 것을 빨갛게
   칠하면 화면이 늘 빨갛고, 그때부터 진짜 고장이 묻힌다. 경고는 **보드가
   실제로 이상하다고 관측한 것** 에만 붙인다.

🔴 **모르는 것은 "모름" 이다.** 항목이 빠졌으면 0 이라고 쓰지 않는다.
   `$STAT` 자체가 없으면(보드 미연결·응답 없음) 전부 모름이다 — 그때
   "정상" 을 그리면 화면이 지어낸 것이다(CLAUDE.md §5).

Qt 위젯(`host/gui/qt/diagnostics_page.py`)은 여기서 나온 것을 그리기만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from host.gui.widgets.status_chip import Level, Verification, rail_label

#: 값을 모를 때 화면에 쓰는 말. 한 곳에서만 정한다 — 시험도 이것을 본다.
UNKNOWN_TEXT = "모름"

#: `$STAT` 을 다시 물어보는 주기(초).
#:
#: 🔴 **텔레메트리를 방해하면 안 된다.** `$STAT` 은 명령·응답 왕복이라
#:    자주 부르면 링크를 먹는다. 응답은 대략 500 B 이므로 1.5 초 주기면
#:    115200 baud 에서 5000 bit / 1.5 s ≈ 3.3 kbps, 링크의 **약 2.9 %** 다.
#:
#:    아래로 더 내릴 이유도 없다 — 이것은 사람이 눈으로 읽는 화면이고,
#:    클럭 출처·유실 건수·LCD 계수기는 1~2 초 늦게 보여도 판단이 달라지지
#:    않는다(실시간 계측은 텔레메트리가 따로 한다).
#:
#:    하트비트 주기(1.0 초)와 어긋난 값을 골랐다. 같은 값이면 매번 같은
#:    스텝에 겹쳐 그 스텝만 유난히 길어진다.
STAT_INTERVAL_S = 1.5

#: `$STAT` 이 실패한 뒤 다시 시도하기까지의 간격(초).
#:
#: 🔴 링크가 죽으면 `send()` 는 타임아웃(2초)까지 기다린다. 실패한 채로
#:    1.5 초마다 다시 물으면 워커가 계속 그 대기에 붙들려 하트비트가 늦고,
#:    보드는 3초 뒤 RUN 으로 떨어진다 — 진단을 보려다 링크를 더 망친다.
STAT_RETRY_S = 5.0

#: 이 시간이 지난 `$STAT` 은 "지금 값" 이 아니다(초).
#:
#: 🔴 주기의 세 배로 잡는다. 한두 번 걸러진 것까지 "낡았다" 고 하면 정상
#:    동작 중에 경고가 깜빡인다.
STALE_AFTER_S = STAT_INTERVAL_S * 3


@dataclass(frozen=True)
class Reading:
    """진단 항목 하나 — 이름표 · 값 · 뜻."""

    key: str
    label: str
    value: str = UNKNOWN_TEXT
    note: str = ""
    """이 값이 무슨 뜻인지 사람 말로. 🔴 여기가 이 층의 존재 이유다."""
    level: Level = Level.IDLE
    verification: Verification = Verification.UNKNOWN

    @property
    def known(self) -> bool:
        return self.verification is not Verification.UNKNOWN

    @property
    def warning(self) -> bool:
        """사람이 지금 봐야 하는가.

        🔴 `Level.IDLE` 은 경고가 아니다 — "정상인데 꺼져 있음"·"해당 없음"
           이 전부 여기로 온다.
        """
        return self.level in (Level.WARN, Level.FAULT)


@dataclass(frozen=True)
class Group:
    """화면에서 한 구획으로 묶이는 항목들."""

    key: str
    title: str
    readings: tuple[Reading, ...] = ()

    @property
    def warnings(self) -> tuple[Reading, ...]:
        return tuple(r for r in self.readings if r.warning)


@dataclass(frozen=True)
class DiagnosticsState:
    """진단 화면 전체. 위젯은 이것만 받는다."""

    groups: tuple[Group, ...] = ()
    fresh: bool = False
    """마지막으로 읽은 `$STAT` 이 지금 값이라고 할 수 있는가."""
    age_s: float | None = None
    error: str = ""

    @property
    def readings(self) -> tuple[Reading, ...]:
        return tuple(r for g in self.groups for r in g.readings)

    @property
    def warnings(self) -> tuple[Reading, ...]:
        return tuple(r for r in self.readings if r.warning)

    def reading(self, key: str) -> Reading | None:
        for r in self.readings:
            if r.key == key:
                return r
        return None

    @property
    def age_text(self) -> str:
        return format_age(self.age_s)

    @property
    def headline(self) -> str:
        """한 줄 요약. 🔴 모르는 것을 "이상 없음" 으로 말하지 않는다."""
        if self.error:
            return f"보드 상태를 못 읽었다 — {self.error}"
        if not self.groups or all(not r.known for r in self.readings):
            return "아직 보드 상태를 읽지 못했다 — 전부 모름"
        n = len(self.warnings)
        if n:
            return f"주의 {n} 건"
        return "특이사항 없음"


def format_age(age_s: float | None) -> str:
    """"언제 읽은 값인가". 🔴 모르면 빈 문자열이다 — "방금" 을 지어내지 않는다."""
    if age_s is None:
        return ""
    if age_s < 1.0:
        return "방금"
    return f"{age_s:.0f}초 전"


# --------------------------------------------------------------- 작은 도구

def _sub(stat: dict | None, key: str) -> dict | None:
    """`$STAT` 안의 중첩 객체. 없거나 모양이 다르면 `None` — 곧 "모름" 이다."""
    if not isinstance(stat, dict):
        return None
    value = stat.get(key)
    return value if isinstance(value, dict) else None


def _int(src: dict | None, key: str) -> int | None:
    if not isinstance(src, dict):
        return None
    v = src.get(key)
    # bool 은 int 의 서브클래스라 따로 걸러낸다 — `true` 가 1 로 읽히면
    # 계수기가 조용히 틀린 값을 말한다.
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _bool(src: dict | None, key: str) -> bool | None:
    if not isinstance(src, dict):
        return None
    v = src.get(key)
    return v if isinstance(v, bool) else None


def _str(src: dict | None, key: str) -> str | None:
    if not isinstance(src, dict):
        return None
    v = src.get(key)
    return v if isinstance(v, str) else None


def _unknown(key: str, label: str, note: str = "") -> Reading:
    return Reading(key=key, label=label, value=UNKNOWN_TEXT, note=note)


def _count(n: int, unit: str = "회") -> str:
    return f"{n:,} {unit}"


# --------------------------------------------------------------- 시간축

_TIME_SOURCE = {
    "gnss_pps": ("GNSS PPS 동기",
                 "PPS 에지와 RMC 문장이 짝지어졌다. 레코드의 `t` 는 UTC "
                 "epoch ms 이고, 이 시스템에서 가장 정확한 상태다"),
    "gnss_nmea": ("GNSS NMEA",
                  "RMC 는 유효한데 PPS 짝을 못 지었다. `t` 는 UTC epoch ms "
                  "이지만 PPS 만큼 정밀하지 않다"),
    "device_clock": ("보드 자체 시계",
                     "🔴 `t` 가 UTC 가 아니라 부팅 후 경과 ms 다 — 시각으로 "
                     "저장하면 재부팅 때 시간축이 리셋된다(규격 §7.1.2). "
                     "GNSS 를 안 쓰는 벤치에서는 정상이다"),
}


def _clock_group(stat: dict | None) -> Group:
    clock = _sub(stat, "clock")
    src = _str(clock, "src")
    hz = _int(clock, "sysclk_hz")

    if clock is None or src is None:
        # 🔴 시뮬레이터·클럭을 안 붙인 빌드는 `null` 을 낸다(규격 §7.4).
        #    "이 장치는 답할 수 없다" 이지 고장이 아니다.
        src_r = _unknown("clock.src", "시스템 클럭 출처",
                         "이 장치는 클럭 출처를 답하지 않는다(시뮬레이터 또는 "
                         "클럭 보고가 없는 펌웨어). 고장이 아니라 답이 없는 것이다")
    elif src == "hse_pll":
        src_r = Reading(
            "clock.src", "시스템 클럭 출처", "크리스털 → PLL (HSE)",
            "초 안쪽 보간이 크리스털 정확도로 이뤄진다 — 이 시스템이 노리는 "
            "상태다",
            Level.OK, Verification.VERIFIED)
    elif src == "hsi":
        src_r = Reading(
            "clock.src", "시스템 클럭 출처", "내부 RC (HSI)",
            "🔴 내부 RC 로 동작 중 — 시간 정확도 ±1 %, 1초당 최대 10 ms 가 "
            "흔들린다. 카메라 프레임 정렬처럼 초 안쪽 정밀도가 걸린 용도는 "
            "이 상태의 시간축을 못 믿는다. 보드가 고장난 것이 아니라 "
            "크리스털이 안 떠서 폴백한 것이다 — Y1·C90·C91 을 본다",
            Level.WARN, Verification.VERIFIED)
    else:
        src_r = Reading(
            "clock.src", "시스템 클럭 출처", src,
            "규격 §7.4 에 없는 값이다 — 무슨 뜻인지 판단할 수 없다")

    if hz is None:
        hz_r = _unknown("clock.sysclk_hz", "시스템 클럭",
                        "보드가 클럭 주파수를 답하지 않는다")
    else:
        hz_r = Reading(
            "clock.sysclk_hz", "시스템 클럭", f"{hz / 1_000_000:.2f} MHz",
            "보드가 레지스터를 읽어 계산한 실제 값이지 상수가 아니다",
            Level.IDLE, Verification.VERIFIED)

    source = _str(stat, "time_source") if isinstance(stat, dict) else None
    quality = _int(stat, "time_quality") if isinstance(stat, dict) else None
    if source is None:
        src_time = _unknown("time.source", "시각 기준",
                            "보드가 시각 기준을 답하지 않았다")
    else:
        label, note = _TIME_SOURCE.get(
            source, (source, "규격 §7.1.2 에 없는 값이다 — 판단할 수 없다"))
        grade = f" (등급 {quality})" if quality is not None else ""
        known = source in _TIME_SOURCE
        src_time = Reading(
            "time.source", "시각 기준", f"{label}{grade}", note,
            Level.OK if source == "gnss_pps" else Level.IDLE,
            Verification.VERIFIED if known else Verification.UNKNOWN)

    return Group("clock", "시간축", (src_time, src_r, hz_r))


# --------------------------------------------------------------- GNSS / PPS

def _gnss_group(stat: dict | None) -> Group:
    g = _sub(stat, "gnss")
    if g is None:
        return Group("gnss", "GNSS · PPS", (
            _unknown("gnss.sats", "위성 수"),
            _unknown("gnss.pps_paired", "채택된 PPS"),
            _unknown("gnss.pps_raw", "원시 PPS 펄스"),
            _unknown("gnss.pps_unpaired", "짝짓기"),
            _unknown("gnss.init", "GNSS 초기화"),
        ))

    sats = _int(g, "sats")
    paired = _int(g, "pps_age_ms")
    raw_age = _int(g, "pps_raw_age_ms")
    raw_count = _int(g, "pps_raw_count")
    reason = g.get("pps_unpaired_reason")

    # 위성 수 ------------------------------------------------------------
    if sats is None:
        sats_r = _unknown("gnss.sats", "위성 수",
                          "GGA 문장을 한 번도 못 받았다 — 0 을 지어내지 않는다")
    else:
        sats_r = Reading(
            "gnss.sats", "위성 수", _count(sats, "개"),
            ("잡힌 위성이 없다 — 실내에서는 정상이다. 하늘이 보이는 곳으로 "
             "옮기면 잡힌다" if sats == 0 else "마지막 GGA 문장이 말한 수다"),
            Level.IDLE, Verification.VERIFIED)

    # 채택된 PPS ---------------------------------------------------------
    if paired is None:
        paired_r = Reading(
            "gnss.pps_paired", "채택된 PPS", "짝지어진 적 없음",
            "시간축이 아직 PPS 를 쓰지 못하고 있다. 🔴 펄스가 안 온다는 뜻이 "
            "아니다 — 아래 `원시 PPS 펄스` 를 함께 본다",
            Level.IDLE, Verification.VERIFIED)
    else:
        near_demotion = paired >= 1200
        paired_r = Reading(
            "gnss.pps_paired", "채택된 PPS", f"{paired:,} ms 전",
            ("🔴 1.5 초를 넘으면 시각 기준이 gnss_nmea 로 내려간다 — 곧 그럴 "
             "참이다" if near_demotion
             else "PPS 가 시간축에 실제로 쓰이고 있다"),
            Level.IDLE, Verification.VERIFIED)

    # 원시 PPS 펄스 -------------------------------------------------------
    if raw_count is None:
        raw_r = _unknown("gnss.pps_raw", "원시 PPS 펄스",
                         "보드가 원시 캡처 수를 답하지 않았다")
    elif raw_count == 0:
        raw_r = Reading(
            "gnss.pps_raw", "원시 PPS 펄스", "한 번도 안 들어옴",
            "PPS 선을 안 물렸다면 정상이다. 물렸는데도 0 이면 배선·모듈의 "
            "PPS 출력 설정을 본다",
            Level.IDLE, Verification.VERIFIED)
    else:
        age = f" · 마지막 {raw_age:,} ms 전" if raw_age is not None else ""
        raw_r = Reading(
            "gnss.pps_raw", "원시 PPS 펄스", f"{raw_count:,} 회{age}",
            "짝짓기 성공과 무관하게 **펄스가 실제로 들어온** 횟수다. 이 값이 "
            "오르고 있으면 배선·입력 캡처는 정상이다",
            Level.IDLE, Verification.VERIFIED)

    # 짝짓기 실패 이유 ----------------------------------------------------
    unpaired_r = _unpaired_reading(reason, paired, raw_count)

    # 초기화 시퀀스 -------------------------------------------------------
    init_r = _init_reading(g)

    return Group("gnss", "GNSS · PPS",
                 (sats_r, paired_r, raw_r, unpaired_r, init_r))


def _unpaired_reading(reason, paired: int | None,
                      raw_count: int | None) -> Reading:
    """`pps_unpaired_reason` 을 뜻으로 옮긴다 (규격 §7.4)."""
    key, label = "gnss.pps_unpaired", "짝짓기"

    if reason is None:
        if paired is not None:
            return Reading(key, label, "정상",
                           "방금 짝지어졌다 — 설명할 실패가 없다",
                           Level.OK, Verification.VERIFIED)
        return Reading(key, label, "판단할 사건 없음",
                       "PPS 도 유효한 RMC 도 아직 한 번도 오지 않았다. GNSS "
                       "를 안 쓰는 벤치에서는 정상이다",
                       Level.IDLE, Verification.VERIFIED)

    if reason == "no_valid_nmea":
        return Reading(
            key, label, "위성 고정 없음 (RMC 가 무효)",
            "🔴 PPS 는 들어오는데 위성 고정이 없어 시간축이 그 펄스를 못 쓴다. "
            "실내에서는 정상이다 — 배선을 의심하기 전에 하늘이 보이는 곳으로 "
            "옮긴다(실기기 관측 2026-08-19, 규격 §7.4)",
            Level.IDLE, Verification.VERIFIED)

    if reason == "no_pps":
        if raw_count:
            # 🔴 펄스는 들어오는데 창 안에서 짝을 못 지었다. 보드가 관측한
            #    진짜 이상이므로 경고다.
            return Reading(
                key, label, "짝지을 PPS 캡처 없음",
                "유효한 RMC 는 왔는데 그 순간 짝지을 원시 PPS 캡처가 창 안에 "
                "없었다. 펄스 자체는 들어오고 있으므로 타이밍 문제다",
                Level.WARN, Verification.VERIFIED)
        # 🔴 펄스가 한 번도 안 온 것은 "PPS 선을 안 물렸다" 일 수 있고,
        #    그것은 정상적인 사용 방식이다(설계 원칙 3). 경고로 만들지 않는다.
        return Reading(
            key, label, "PPS 펄스 없음",
            "유효한 RMC 는 오는데 PPS 펄스가 한 번도 안 들어왔다. PPS 선을 "
            "안 물렸다면 정상이고, 물렸다면 그 배선을 본다",
            Level.IDLE, Verification.VERIFIED)

    # 🔴 모르는 값은 아는 척하지 않는다. 원문을 그대로 보여 준다.
    return Reading(key, label, str(reason),
                   "규격 §7.4 에 없는 값이다 — 무슨 뜻인지 판단할 수 없다")


def _init_reading(g: dict) -> Reading:
    """`init_sent`·`init_exhausted`·`sentence_seen` 세 개를 한 문장으로."""
    key, label = "gnss.init", "GNSS 초기화"
    sent = _bool(g, "init_sent")
    exhausted = _bool(g, "init_exhausted")
    seen = _bool(g, "sentence_seen")

    if sent is None or exhausted is None or seen is None:
        return _unknown(key, label, "보드가 초기화 상태를 답하지 않았다")

    if exhausted:
        return Reading(
            key, label, "재시도 소진 — 응답 없음",
            "🔴 보드가 `LOG` 명령을 재시도 상한까지 다 보냈는데 체크섬이 "
            "통과한 NMEA 문장이 한 줄도 안 왔다. 보드는 할 수 있는 것을 다 "
            "했으므로 배선·baud·모듈 전원을 본다(규격 §4.1.1)",
            Level.WARN, Verification.VERIFIED)

    if not sent:
        return Reading(
            key, label, "아직 안 보냄",
            "`gnss.enabled` 가 꺼져 있거나 보드가 첫 시도를 아직 못 했다. "
            "GNSS 를 안 쓰는 설정이면 정상이다",
            Level.IDLE, Verification.VERIFIED)

    if seen:
        return Reading(
            key, label, "문장 수신됨",
            "모듈이 최소 한 번은 응답했다. 이 뒤로 등급이 떨어졌다면 초기화 "
            "문제가 아니라 신호가 끊긴 것이다",
            Level.OK, Verification.VERIFIED)

    return Reading(
        key, label, "보냄 · 응답 대기",
        "명령은 나갔고 아직 재시도가 남아 있다", Level.PROBING,
        Verification.VERIFIED)


# --------------------------------------------------------------- 수집 큐

def _queue_group(stat: dict | None) -> Group:
    queues = stat.get("queues") if isinstance(stat, dict) else None
    if not isinstance(queues, list):
        return Group("queues", "수집 큐", (
            _unknown("queues.drops", "버린 표본",
                     "보드가 큐 상태를 답하지 않았다"),
        ))

    rows = [q for q in queues if isinstance(q, dict)]
    drops = sum(_int(q, "drops") or 0 for q in rows)

    if drops:
        # 🔴 이 시스템에서 가장 나쁜 실패다. 버린 표본은 영영 없다.
        drops_r = Reading(
            "queues.drops", "버린 표본", _count(drops, "건"),
            "🔴 큐가 가득 차 가장 오래된 표본을 버렸다 — 그만큼의 측정이 "
            "영영 없다. 수집 주기를 늦추거나 필드 마스크를 줄여 링크가 "
            "따라오게 한다(규격 §5.4)",
            Level.FAULT, Verification.VERIFIED)
    elif rows:
        drops_r = Reading(
            "queues.drops", "버린 표본", "없음",
            "표본을 하나도 안 버렸다 — 링크가 수집 속도를 따라오고 있다",
            Level.OK, Verification.VERIFIED)
    else:
        drops_r = Reading(
            "queues.drops", "버린 표본", "켜진 채널 없음",
            "수집 중인 채널이 하나도 없다. 설정에서 켜지 않았다면 정상이다",
            Level.IDLE, Verification.VERIFIED)

    out = [drops_r]
    for q in rows:
        ch = _int(q, "ch")
        if ch is None:
            continue
        depth, peak, drop = (_int(q, "depth"), _int(q, "peak"),
                             _int(q, "drops") or 0)
        # 🔴 `ch` 는 채널 번호이지 배열 첨자가 아니다(규격 §7.4) — 꺼진
        #    채널은 목록에서 빠지므로 둘이 일치하지 않는다.
        out.append(Reading(
            f"queue.{ch}", f"채널 {ch} (J{ch + 3})",
            f"지금 {depth if depth is not None else '?'} 개 · "
            f"최대 {peak if peak is not None else '?'} 개",
            (f"🔴 이 채널에서 {drop:,} 건을 버렸다"
             if drop else "쌓였다 빠진 최대 깊이가 `최대` 다"),
            Level.FAULT if drop else Level.IDLE, Verification.VERIFIED))

    return Group("queues", "수집 큐", tuple(out))


# --------------------------------------------------------------- 화면(LCD)

def _lcd_group(stat: dict | None) -> Group:
    lcd = _sub(stat, "lcd")
    if lcd is None:
        return Group("lcd", "화면 회복", (
            _unknown("lcd.readback", "되읽기"),
            _unknown("lcd.reinit", "재초기화"),
            _unknown("lcd.rejected", "거부된 그리기"),
            _unknown("lcd.redraw", "전면 갱신"),
            _unknown("lcd.verify", "되읽기 대조"),
        ))

    readback = _bool(lcd, "readback")
    if readback is None:
        readback_r = Reading(
            "lcd.readback", "되읽기", UNKNOWN_TEXT,
            "아직 한 번도 안 물어봤다. 화면이 안 붙은 보드(시뮬레이터 포함)가 "
            "정확히 이 값을 낸다 — 못 믿는다는 뜻이 아니다",
            Level.IDLE, Verification.UNKNOWN)
    elif readback:
        readback_r = Reading(
            "lcd.readback", "되읽기", "믿을 수 있다",
            "패널 레지스터를 되읽어 자기가 넣은 값과 대조할 수 있다 — 화면이 "
            "깨졌는지 보드가 스스로 안다",
            Level.OK, Verification.VERIFIED)
    else:
        # 🔴 흔한 3.5" 모듈은 SDO 가 안 물려 있다. 고장이 아니라 사양이다.
        readback_r = Reading(
            "lcd.readback", "되읽기", "되돌려주지 않는다",
            "이 화면 모듈은 상태를 되돌려주지 않는다 — 자가진단을 못 쓴다. "
            "흔한 3.5\" 모듈이 그렇다(SDO 미연결). 회복 수단은 주기적 전면 "
            "갱신뿐이므로 화면이 깨지면 `lcd.redraw_ms` 를 짧게 한다",
            Level.IDLE, Verification.VERIFIED)

    reinit = _int(lcd, "reinit")
    if reinit is None:
        reinit_r = _unknown("lcd.reinit", "재초기화")
    elif reinit:
        reinit_r = Reading(
            "lcd.reinit", "재초기화", _count(reinit),
            "🔴 되읽은 레지스터가 달라 하드웨어 리셋부터 다시 했다 — 명령이 "
            "실제로 깨지고 있다는 뜻이다. SPI 신호 무결성 문제이므로 "
            "`lcd.spi_khz` 를 낮춰 본다",
            Level.WARN, Verification.VERIFIED)
    else:
        reinit_r = Reading(
            "lcd.reinit", "재초기화", "없음",
            "패널 레지스터가 한 번도 어긋나지 않았다",
            Level.OK, Verification.VERIFIED)

    rejected = _int(lcd, "rejected")
    if rejected is None:
        rejected_r = _unknown("lcd.rejected", "거부된 그리기")
    elif rejected:
        rejected_r = Reading(
            "lcd.rejected", "거부된 그리기", _count(rejected, "건"),
            "🔴 보드가 받아들이지 못한 그리기 요청이다 — 화면 배치가 틀렸고 "
            "그 칸은 영영 안 그려진다",
            Level.WARN, Verification.VERIFIED)
    else:
        rejected_r = Reading(
            "lcd.rejected", "거부된 그리기", "없음", "",
            Level.OK, Verification.VERIFIED)

    redraw = _int(lcd, "redraw")
    epoch = _int(lcd, "epoch")
    if redraw is None:
        redraw_r = _unknown("lcd.redraw", "전면 갱신")
    else:
        era = f" · 갱신 세대 {epoch}" if epoch is not None else ""
        redraw_r = Reading(
            "lcd.redraw", "전면 갱신", f"{redraw:,} 회{era}",
            "값이 안 바뀌어도 화면 전체를 다시 그린 횟수다. 부분 갱신만으로는 "
            "한 번 깨진 그림이 저절로 안 돌아오기 때문에 주기적으로 한다 — "
            "늘어나는 것이 정상이다",
            Level.IDLE, Verification.VERIFIED)

    ok, fail = _int(lcd, "verify_ok"), _int(lcd, "verify_fail")
    if ok is None or fail is None:
        verify_r = _unknown("lcd.verify", "되읽기 대조")
    else:
        verify_r = Reading(
            "lcd.verify", "되읽기 대조", f"성공 {ok:,} · 실패 {fail:,}",
            ("대조가 어긋난 적이 있다. 되돌리는 일은 위의 `재초기화` 가 한다"
             if fail else ""),
            Level.IDLE, Verification.VERIFIED)

    return Group("lcd", "화면 회복",
                 (readback_r, reinit_r, rejected_r, redraw_r, verify_r))


# --------------------------------------------------------------- 입력·전원

#: 규격 §7.4 의 `rails` 키 → 화면 이름표. 순서는 기동 순서다.
_RAIL_LABELS = (("v5", "5V"), ("v14v9", "14.9V"), ("v24", "24V"))

#: 디지털 입력 커넥터 (데이터시트 §5.7).
_DIN_PORTS = (18, 19, 20)


def _io_group(stat: dict | None) -> Group:
    rails = _sub(stat, "rails")
    out: list[Reading] = []
    for key, label in _RAIL_LABELS:
        on = _bool(rails, key)
        if on is None:
            out.append(_unknown(f"rail.{key}", f"{label} 레일"))
            continue
        # 🔴 설계 원칙 4 — 피드백 회로가 없으므로 보드도 실제 전압을 모른다.
        #    "정상 ON" 이라고 쓰면 화면이 확인하지 않은 것을 주장하게 된다.
        out.append(Reading(
            f"rail.{key}", f"{label} 레일",
            rail_label(on, Verification.COMMANDED),
            "명령 상태다. 피드백 회로가 없어 실제 전압은 보드도 모른다",
            Level.OK if on else Level.IDLE, Verification.COMMANDED))

    din = stat.get("din") if isinstance(stat, dict) else None
    states: dict[int, int] = {}
    if isinstance(din, list):
        for d in din:
            cid, st = _int(d, "connector_id"), _int(d, "state")
            if cid is not None and st is not None:
                states[cid] = st

    for cid in _DIN_PORTS:
        st = states.get(cid)
        if st is None:
            out.append(_unknown(f"din.{cid}", f"디지털 입력 J{cid}"))
            continue
        # 🔴 `rails` 와 반대로 이것은 **실측**이다 — 보드가 핀을 직접 읽는다.
        out.append(Reading(
            f"din.{cid}", f"디지털 입력 J{cid}",
            rail_label(bool(st), Verification.VERIFIED),
            "실측이다 — 보드가 EXTI 로 핀을 직접 읽는다",
            Level.OK if st else Level.IDLE, Verification.VERIFIED))

    return Group("io", "디지털 입력 · 전원", tuple(out))


# --------------------------------------------------------------- 보드

def _board_group(stat: dict | None) -> Group:
    uptime = _int(stat, "uptime_ms") if isinstance(stat, dict) else None
    if uptime is None:
        up_r = _unknown("board.uptime", "가동 시간")
    else:
        s = uptime // 1000
        up_r = Reading(
            "board.uptime", "가동 시간",
            f"{s // 3600}시간 {s % 3600 // 60}분 {s % 60}초",
            "갑자기 줄었으면 보드가 재부팅한 것이다 — `device_clock` 일 때는 "
            "레코드의 `t` 도 함께 리셋된다",
            Level.IDLE, Verification.VERIFIED)

    mode = _str(stat, "mode") if isinstance(stat, dict) else None
    ctl = _str(stat, "ctl_mode") if isinstance(stat, dict) else None
    mode_r = (_unknown("board.mode", "접속 모드") if mode is None else Reading(
        "board.mode", "접속 모드", mode,
        "`CONFIG` 는 하트비트가 살아 있어 설정을 바꿀 수 있는 상태다",
        Level.IDLE, Verification.VERIFIED))
    ctl_r = (_unknown("board.ctl_mode", "제어 모드") if ctl is None else Reading(
        "board.ctl_mode", "제어 모드", ctl,
        "`TEST` 에서는 출력이 저장되지 않고 모드를 벗어날 때 기본값으로 "
        "돌아간다(규격 §6.4)",
        Level.IDLE, Verification.VERIFIED))

    return Group("board", "보드", (up_r, mode_r, ctl_r))


# --------------------------------------------------------------- 조립

def build_diagnostics(stat: dict | None, *, error: str = "",
                      age_s: float | None = None) -> DiagnosticsState:
    """`$STAT` 응답 하나를 진단 화면 상태로.

    🔴 `stat` 이 `None` 이면 **전부 "모름"** 이다. 항목 자리는 그대로 남긴다 —
       무엇을 못 읽고 있는지가 사람에게는 정보이기 때문이다. 비워 버리면
       "이 보드에는 GNSS 가 없나 보다" 로 읽힌다.
    """
    groups = (
        _clock_group(stat),
        _gnss_group(stat),
        _queue_group(stat),
        _lcd_group(stat),
        _io_group(stat),
        _board_group(stat),
    )
    fresh = (
        stat is not None
        and not error
        and (age_s is None or age_s <= STALE_AFTER_S)
    )
    return DiagnosticsState(groups=groups, fresh=fresh, age_s=age_s,
                            error=error)
