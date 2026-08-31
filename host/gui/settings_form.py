"""설정 화면의 로직. Qt 를 import 하지 않는다.

🔴 항목을 하드코딩하지 않는다
=============================

화면은 `$CFG,LIST` 응답만으로 만들어진다. 어떤 항목이 있는지, 무슨 타입인지,
범위가 얼마인지 전부 보드가 알려 준다.

이유는 펌웨어를 단계로 올리기 때문이다. 2단계에서 설정 항목이 늘고 3단계에서
또 는다. 호스트에 목록을 박아 두면 그때마다 GUI 를 다시 배포해야 하고,
보드마다 펌웨어 버전이 다르면 맞출 방법이 없다.

여기서 하는 일
-------------
    항목을 그룹으로 묶고 순서를 정한다
    `vtype` 으로 어떤 입력 방식인지 정한다 (체크박스·숫자·선택·문자열)
    편집 가능한지, 안 되면 왜인지 정한다
    사용자가 고친 것만 골라 보낼 목록을 만든다

무엇을 안 하나
-------------
    Qt 위젯을 만들지 않는다. 위젯은 여기서 나온 값을 보고 그리기만 한다.
    인터록을 판정하지 않는다 — 보드가 한다(규격 §5). 호스트가 흉내 내면
    두 판정이 갈리고, 갈리면 사용자는 GUI 를 믿을 수 없게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from host.core.config_schema import ConfigItem, ConfigSchema
from host.core.errors import ConfigError
from host.core.limits import MAX_ARG_BYTES
from host.gui.link_baud import choice_label as baud_choice_label
from host.gui.link_baud import is_link_baud


class Widget(Enum):
    """이 항목을 어떤 입력으로 그릴까. `vtype` 에서 정해진다."""

    TOGGLE = "toggle"      #: bool
    NUMBER = "number"      #: u8·u16·u32·f32
    CHOICE = "choice"      #: enum
    TEXT = "text"          #: str


_WIDGET_OF = {
    "bool": Widget.TOGGLE,
    "u8": Widget.NUMBER,
    "u16": Widget.NUMBER,
    "u32": Widget.NUMBER,
    "f32": Widget.NUMBER,
    "enum": Widget.CHOICE,
    "str": Widget.TEXT,
}


@dataclass(frozen=True)
class Row:
    """화면의 한 줄. 위젯이 그리는 데 필요한 전부."""

    key: str
    label: str
    widget: Widget
    value: str
    """지금 화면에 떠 있는 값. 문자열로 들고 있는다 — 전선에 나갈 형태다."""

    default: str
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    choice_labels: tuple[str, ...] = ()
    """열거 항목의 이름표. 비어 있으면 화면이 값을 그대로 보여 준다."""
    editable: bool = True
    reason: str = ""
    """편집이 **안 되는** 이유. 툴팁에 그대로 띄운다."""

    note: str = ""
    """보드가 이 항목에 붙여 보낸 사유. 편집 가능해도 실린다.

    🔴 예전에는 읽기 전용일 때만 `reason` 으로 옮겨 담고 나머지는 버렸다.
       그러다 `pwr.5v` 를 끌 수 있게 하면서(사용자 확정 2026-08-14) 문제가
       드러났다 — "끄면 쿨링 팬·아날로그 수집·WS2812 가 함께 멈춘다" 는
       경고가 화면에 뜨지 않았다. 인터록을 푼 대신 남긴 유일한 안전장치가
       바로 그 문구인데, 편집 가능해졌다는 이유로 버려지고 있었다.

       보드가 사유를 붙였으면 이유가 있다. 편집 여부와 무관하게 전달한다."""

    is_integer: bool = False

    @property
    def dirty(self) -> bool:
        """화면 값이 보드 값과 다른가 — `original` 과 비교는 Form 이 한다."""
        return False   # Row 는 불변이라 Form 이 판정한다


@dataclass
class Group:
    name: str
    rows: list[Row] = field(default_factory=list)


#: 표로 접을 만한 최소 반복 수. 이보다 적으면 헤더 줄이 본문보다 길어져
#: 폼보다 읽기 나빠진다.
MATRIX_MIN_ROWS = 3


@dataclass(frozen=True)
class MatrixRow:
    """표의 한 줄 — 번호 하나가 가진 칸들. 열 순서대로 늘어놓는다."""

    index: int
    label: str
    cells: tuple[Row | None, ...]


@dataclass(frozen=True)
class Matrix:
    """반복되는 항목들을 표로 접은 것. 행 = 번호, 열 = 속성."""

    prefix: str
    columns: tuple[str, ...]
    """키의 뒷부분들 (`zero` · `scale` …). 화면은 안 쓰지만 시험이 짚는다."""

    column_labels: tuple[str, ...]
    rows: tuple[MatrixRow, ...]

    leftovers: tuple[Row, ...] = ()
    """접히지 않고 남은 항목들. 화면이 표 위에 폼으로 그린다.

    🔴 그룹에 반복이 아닌 항목이 섞여 있다고 반복분까지 못 접을 이유는
       없다. LED 그룹이 그렇다 — `체인 LED 수`·`밝기` 는 하나씩이고
       J21~J24 의 R·G·B 는 반복이다.
    """


def _split_key(key: str) -> tuple[str, int, str] | None:
    """`ain3.zero` → (`ain`, 3, `zero`). 그 꼴이 아니면 None."""
    head, _, tail = key.partition(".")
    if not tail:
        return None
    digits = len(head) - len(head.rstrip("0123456789"))
    if digits == 0 or digits == len(head):
        return None
    return head[:-digits], int(head[-digits:]), tail


def _common_prefix(texts: list[str]) -> str:
    if not texts:
        return ""
    out = texts[0]
    for other in texts[1:]:
        i = 0
        while i < len(out) and i < len(other) and out[i] == other[i]:
            i += 1
        out = out[:i]
    return out.rstrip()


def matrix_of(group: Group) -> Matrix | None:
    """그룹이 표로 접을 만한 모양인지 보고, 그렇다면 접어서 돌려준다.

    🔴 **항목을 하드코딩하지 않는다** — 이 파일의 전제다(머리말). 그래서
       "ain 그룹은 표" 라고 적지 않고, 키가 `<접두><번호>.<속성>` 꼴로
       반복되는지를 **구조로 판정**한다. 보드가 채널을 8 개로 늘리거나
       속성을 추가해도 화면이 따라간다.

    🔴 왜 접는가. 45 개 항목 중 35 개가 7 채널 × 5 속성이다. 세로로 35 줄을
       쌓으면 스크롤이 길어지는 것보다 나쁜 일이 생긴다 — "J5 의 영점이
       J6 보다 큰가" 를 볼 수 없다. 채널끼리 비교하는 것이 이 화면의 주된
       용도인데도.
    """
    # 🔴 반복되는 것만 골라 낸다. 예전에는 "하나라도 어긋나면 표가 아니다"
    #    였는데, 그러면 LED 그룹처럼 스칼라 두 개가 섞였다는 이유로 J21~J24
    #    의 R·G·B 열두 줄이 통째로 늘어선다. 섞였다고 반복을 못 접을 이유가
    #    없다 — 남는 것은 표 위에 폼으로 그린다.
    parsed: list[tuple[str, int, str, Row]] = []
    leftovers: list[Row] = []
    for row in group.rows:
        bits = _split_key(row.key)
        if bits is None:
            leftovers.append(row)
        else:
            parsed.append((*bits, row))

    prefixes = {p for p, _, _, _ in parsed}
    if len(prefixes) != 1:
        return None

    indices = sorted({i for _, i, _, _ in parsed})
    if len(indices) < MATRIX_MIN_ROWS:
        return None

    # 열 순서는 **처음 나온 순서**다. 보드가 보낸 순서에 뜻이 있다고 보고
    # 재정렬하지 않는다 (groups 프로퍼티와 같은 규칙).
    columns: list[str] = []
    for _, _, suffix, _ in parsed:
        if suffix not in columns:
            columns.append(suffix)
    if len(columns) < 2:
        return None                          # 한 열짜리 표는 폼만 못하다

    prefix = next(iter(prefixes))
    by_cell = {(i, s): r for _, i, s, r in parsed}

    rows: list[MatrixRow] = []
    for index in indices:
        labels = [r.label for (i, _s), r in by_cell.items() if i == index]
        # 🔴 라벨이 `J3 영점` · `J3 스케일` 처럼 생겼으므로, 한 번호의
        #    라벨들이 공통으로 가진 앞부분이 곧 행 이름이다. 공통 부분이
        #    없으면 키로 되돌아간다 — 빈 행 이름을 내놓지 않는다.
        label = _common_prefix(labels) or f"{prefix}{index}"
        rows.append(MatrixRow(
            index=index,
            label=label,
            cells=tuple(by_cell.get((index, s)) for s in columns),
        ))

    return Matrix(
        prefix=prefix,
        columns=tuple(columns),
        column_labels=tuple(
            _column_label(by_cell, indices, rows, columns, k)
            for k in range(len(columns))
        ),
        rows=tuple(rows),
        leftovers=tuple(leftovers),
    )


def _column_label(by_cell, indices, rows, columns, k: int) -> str:
    """열 제목 — 항목 라벨에서 행 이름을 뺀 나머지.

    🔴 행 이름이 안 붙은 라벨이면 키의 뒷부분을 쓴다. 보드가 라벨을 어떻게
       주든 화면이 빈 제목을 내놓지 않아야 한다.
    """
    suffix = columns[k]
    for row in rows:
        cell = row.cells[k]
        if cell is None:
            continue
        text = cell.label
        if row.label and text.startswith(row.label):
            text = text[len(row.label):].strip()
        if text:
            return text
    return suffix


#: 그룹 이름을 사람이 읽는 말로. 없는 그룹은 이름 그대로 쓴다 —
#: 🔴 보드가 새 그룹을 보내도 화면이 깨지지 않아야 한다.
GROUP_LABELS = {
    "dev": "장치",
    "tx": "전송",
    "pwr": "전원",
    "adc": "ADC",
    "ain": "아날로그 입력",
    "sol": "디지털 입력",
    "led": "LED",
    "i2c": "I2C 센서",
    "gnss": "GNSS",
    "lcd": "화면",
    # 🔴 "링크" 가 아니라 "호스트 링크" 다. 이 보드에는 GNSS·I2C·Jetson 등
    #    여러 통신선이 있고, 여기서 바꾸는 것은 **지금 이 화면이 쓰고 있는**
    #    그 선 하나다(규격 §4.2).
    "link": "호스트 링크",
}


def group_label(name: str) -> str:
    return GROUP_LABELS.get(name, name)


def cloud_duplicate_warning(duplicates: dict[str, list[int]]) -> str:
    """역매핑이 가릴 수 없는 타입 중복의 경고문 (HANDOFF_0831 결정 2 보완).

    🔴 레코드에 채널 번호가 없으므로(사용자 결정 2026-08-30) 같은 타입
    문자열이 두 채널에 있으면 — 같은 이름을 친 아날로그든, 동종 I2C
    2대든 — 스트림·차트·영점이 어느 채널의 값인지 가릴 수 없다. 펌웨어는
    막지 않는다(사용자 결정) — 이 경고가 설정 단계의 안전망이다.
    2026-08-31 온습도 진단에서 실제로 이 구성(i2c12+i2c13 중복)이 사고를
    냈다 — 그날은 경고가 없어서 늦게 찾았다.
    """
    if not duplicates:
        return ""
    parts = [
        f"{t}({'·'.join(f'J{c}' for c in cs)})"
        for t, cs in sorted(duplicates.items())
    ]
    return ("타입 중복 — 채널을 가릴 수 없다: " + ", ".join(parts)
            + ". 설정에서 타입을 다르게 하거나 한쪽을 꺼라")


#: 규격 §7.2 가 이름 지은 전송 설정 항목들. 카탈로그 항목을 하드코딩하는
#: 것과 다르다 — 이것은 **규격이 정한 계약**이고, 규격이 바뀌면 여기도 바뀐다.
#: 규격 §7.2.1 이 이름 지은 채널 환산 항목의 뒷부분.
#: `value = (ma - ainN.zero) * ainN.scale`
COL_ZERO = "zero"
COL_SCALE = "scale"
COL_UNIT = "unit"
#: I2C 포트 표의 열 이름 (`i2cN.kind` · `i2cN.enabled`).
COL_KIND = "kind"
COL_ENABLED = "enabled"

#: 🔴 [개정, 2026-08-19] `tx.fields` 하나였던 마스크가 레코드 종류별로
#:    셋으로 나뉘었다(규격 §7.2·§7.5·§7.6) — `ain` 의 `raw` 를 꺼도 `i2c` 는
#:    무관해야 하는데 마스크가 하나면 그럴 수 없었다. 키 이름은 규격이
#:    정한 계약이라 여기 적는다 — 카탈로그 항목을 하드코딩하는 것과는
#:    다르다(머리말 참고, `KEY_PERIOD_MS` 와 같은 성격).
KEY_FIELD_MASK_AIN = "tx.fields_ain"
KEY_FIELD_MASK_I2C = "tx.fields_i2c"
KEY_FIELD_MASK_DIN = "tx.fields_din"
#: 🔴 [신설, 2026-08-20] 네 번째. GNSS 측위 레코드(규격 §7.8)의 마스크.
KEY_FIELD_MASK_GNSS = "tx.fields_gnss"
KEY_FIELD_MASK_IMU = "tx.fields_imu"
KEY_PERIOD_MS = "tx.period_ms"
KEY_FLOAT_DIGITS = "tx.float_digits"
#: GNSS 를 켜고 끄는 항목(규격 §7.8.4 — 꺼져 있으면 아무것도 안 나간다).
KEY_GNSS_ENABLED = "gnss.enabled"

#: 레코드 종류 → 자기 마스크 설정 키. 화면이 카드를 넷으로 나눠 그릴 때
#: 이 순서를 따른다(ain·i2c·din·gnss — 규격이 절을 나눈 순서와 같다).
FIELD_MASK_KEYS: dict[str, str] = {
    "ain": KEY_FIELD_MASK_AIN,
    "i2c": KEY_FIELD_MASK_I2C,
    "din": KEY_FIELD_MASK_DIN,
    "gnss": KEY_FIELD_MASK_GNSS,
    # [2026-08-22] imu — 젯슨 링크 전용 레코드(UM981 RAWIMUX)지만 필드
    # 선택은 같은 화면이 다룬다 (사용자 확정 — "IMU 도 GNSS 와 분리").
    "imu": KEY_FIELD_MASK_IMU,
}

#: GNSS 측위 레코드가 나가는 주기(규격 §7.8.6).
#:
#: 🔴 `tx.period_ms` 가 아니다. 모듈이 RMC 한 줄을 완성하는 것이 사건이고,
#:    §4.1.1 의 초기화 명령이 `ONTIME 1` 이라 1 Hz 다. 여기를 `tx.period_ms`
#:    로 두면 사용자가 송신 주기를 10 ms 로 줄일 때 있지도 않은 GNSS 트래픽
#:    100 배가 화면에 나타난다.
#:
#: 🔴 사용자가 `$GNSS` 로 `LOG GPRMC ONTIME 0.1` 을 직접 걸면 실제 줄 수는
#:    이보다 많아지고 화면의 사용량은 그만큼 낙관적이 된다. 보드는 모듈에
#:    무엇을 명령했는지 기억하지 않으므로(규격 §4.1) 지어내지 않는다.
GNSS_PERIOD_MS = 1000

#: GNSS 를 켜고 끄는 항목 옆의 IMU 토글 — 켜면 보드가 UM981 에
#: `RAWIMUXA 0.1`(10 Hz)을 걸어 젯슨 링크로 imu 레코드를 낸다.
KEY_GNSS_IMU = "gnss.imu"

#: imu 레코드 주기. 펌웨어 mk_gnssctl.c 가 거는 `RAWIMUXA 0.1` = 10 Hz 다 —
#: `tx.period_ms` 와 무관하다(GNSS 와 같은 이유: 주기를 정하는 것은 모듈이다).
IMU_PERIOD_MS = 100

#: 레코드 종류 → 그 종류의 "채널 수" 를 셀 그룹. `din`(J18~J20)은 켜고 끄는
#: 채널이 없다 — 남는 카탈로그 항목이 `sol.debounce_ms` 하나뿐이라
#: `matrix_of` 가 표로 접지 못한다(반복 최소 3줄, MATRIX_MIN_ROWS). 그래서
#: `din` 의 대역폭은 "채널 수 × 주기" 로 어림할 수 없다 — 애초에 이벤트성
#: 레코드라(규격 §7.6) 지어내지 않는다(설계 원칙 3·4와 같은 결).
#:
#: 🔴 `gnss` 는 표로 접히는 그룹이 아니라 **항목 하나**(`gnss.enabled`)가
#:    켜고 끈다 — J16 하나뿐이라 반복이 없다. `record_shape` 이 그 자리를
#:    따로 다룬다.
RECORD_GROUPS: dict[str, str] = {"ain": "ain", "i2c": "i2c", "din": "sol",
                                 "gnss": "gnss",
                                 # imu 를 켜는 항목(`gnss.imu`)은 gnss 그룹에
                                 # 있다 — 채널 수는 `record_shape` 의 특례가
                                 # 세므로 이 표는 요약 카드 배치에만 쓰인다.
                                 "imu": "gnss"}

#: 이 필드는 잠겨 있다 — 마스크 비트가 아예 없어 항상 실린다(규격 §7.1·
#: §7.5·§7.6). 카드가 "잠김" 으로 보여 줄 항목들이다. `time_source`는
#: 모든 레코드에 공통이고, 나머지는 그 레코드가 아니면 뜻이 없는 필드다.
LOCKED_FIELDS: dict[str, tuple[str, ...]] = {
    # 🔴 connector_id 는 ain·i2c 에서도 잠겼다(규격 §7.2·§7.5 개정
    #    2026-08-21) — 어느 카드에 붙일지가 빠지면 레코드가 버려진다.
    "ain": ("time_source", "connector_id"),
    "i2c": ("time_source", "quantity", "value", "connector_id"),
    "din": ("time_source", "connector_id", "state"),
    # 🔴 위치가 빠진 GNSS 레코드는 아무 말도 안 한다(규격 §7.8.5). `fix_t`
    #    도 함께 잠근다 — "어디에" 만 있고 "언제" 가 없으면 주행 궤적을
    #    되짚을 수 없다.
    "gnss": ("time_source", "lat", "lon", "fix_t"),
    # [2026-08-22] imu(젯슨 링크) — 6축이 빠진 imu 레코드는 아무 말도
    # 안 한다. 지자기는 UM981 에 없어 아예 안 실린다(계약 v1.7.0 완화).
    "imu": ("time_source", "ax", "ay", "az", "gx", "gy", "gz"),
}

#: 필드 카드 안의 소구획 (2026-08-22 사용자 요청 — "I2C 는 센서별로
#: 나눠야"). 특정 센서에만 뜻이 있는 비트를 그 센서 이름 아래 묶어
#: 보여준다. **표시용일 뿐**이다 — 마스크는 여전히 레코드 종류당 하나고,
#: 여기 없는 비트는 카드의 공통 구획에 그려진다. 이름은 cfg_field 의
#: `name` 이다(라벨이 아니라 — 라벨은 보드가 바꿀 수 있다).
FIELD_SUBSECTIONS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "i2c": (
        ("적외 온도", ("temp_ambient",)),
        ("온습도", ("dewpoint",)),
    ),
    # (imu_temp 는 이제 제 카드 — IMU 필드 카드 — 에 있다, 2026-08-22.)
}

#: 잠긴 필드의 사람이 읽는 이름표. 보드가 이 이름을 알려 주지 않는다 —
#: 마스크 비트가 없어 `cfg_field` 로도 안 온다. `schema_ver`·`seq`·`t`·
#: `type` 이 이미 `field_budget.ALWAYS_ON` 으로 하드코딩돼 있는 것과 같은
#: 성격이다 — 카탈로그 항목이 아니라 **규격이 고정한 이름**이다.
LOCKED_FIELD_LABELS: dict[str, str] = {
    "time_source": "시간 소스",
    "quantity": "측정량",
    "value": "측정값",
    "connector_id": "커넥터 번호",
    "state": "상태",
    "lat": "위도",
    "lon": "경도",
    "fix_t": "측위 시각",
    "ax": "가속도 X", "ay": "가속도 Y", "az": "가속도 Z",
    "gx": "각속도 X", "gy": "각속도 Y", "gz": "각속도 Z",
}


@dataclass(frozen=True)
class TelemetryShape:
    """지금 설정이 만들어 낼 텔레메트리의 모양. 대역폭 계산의 입력이다."""

    channels: int
    """주기마다 나갈 **레코드 수**. 켜진 커넥터 수가 아니다 —
    🔴 I2C 는 포트 하나가 양을 둘 낼 수 있다(온습도). `labels` 와 수가
       다른 것이 정상이다."""

    period_ms: int
    float_digits: int

    labels: tuple[str, ...] = ()
    """켜진 커넥터의 이름들 (`J3` · `J12` …).

    🔴 "몇 %" 만으로는 무엇을 끄면 되는지 알 수 없다. 어느 커넥터 때문에
       그 수가 나왔는지 화면이 말할 수 있어야 한다. 이름은 카탈로그 라벨
       에서 온다 — 채널 번호로 커넥터 번호를 짐작하지 않는다(설계 원칙 1:
       화면은 `ain0` 이 아니라 `J3` 을 쓴다)."""


def record_shape(form: "SettingsForm", kind: str = "ain") -> TelemetryShape:
    """`kind`(`ain`·`i2c`·`din`) 레코드가 만들어 낼 텔레메트리의 모양.

    🔴 [개정, 2026-08-19] 채널 수는 **그 종류의 그룹 하나만** 센다
       (`RECORD_GROUPS`). `tx.fields` 가 하나였던 시절에는 모든 그룹의
       토글 열을 통째로 더해도 우연히 맞았다(꺼진 그룹은 0을 보탤 뿐이라)
       — 마스크를 셋으로 나눈 지금은 `ain` 카드가 `i2c` 채널까지 세면
       안 된다.

    🔴 값을 정수로 못 읽으면 **기본값으로 돌아간다.** 사용자가 주기를
       지우는 동안 값은 빈 문자열이고, 그때 예외가 나면 숫자 하나 지웠다고
       설정 화면이 통째로 죽는다.

    🔴 채널 수는 키 이름을 짐작하지 않고 **반복 그룹의 토글 열**에서 센다
       (`matrix_of`). 보드가 채널 항목의 이름을 바꿔도 따라간다. `din`은
       채널을 켜고 끄는 항목이 없어(J18~J20은 항상 감시된다) 표로 접히지
       않고, `channels`는 항상 0이다 — 이벤트성 레코드라 "채널 수 × 주기"
       로 대역폭을 어림할 근거 자체가 없다(모듈 위 `RECORD_GROUPS` 주석).

    🔴 `i2c`의 전송 주기도 `ain`과 같은 `tx.period_ms`다(규격 §7.1) —
       포트마다 다른 `i2cN.period_ms`는 **수집** 주기이지 **송신** 주기가
       아니다.

    🔴 [개정, 2026-08-20] `i2c`의 `channels`는 **켜진 포트 수가 아니라 그
       포트들이 내는 양(quantity)의 수**다. 포트 하나가 레코드 하나가 아니다 —
       온습도 센서는 `temp`와 `humidity`를 따로 내고(device_sim `_emit_i2c`,
       펌웨어 mk_i2c.c), 종류를 안 고른 포트(`kind`=없음)는 켜도 아무것도
       안 낸다. 포트로 세면 앞은 절반으로, 뒤는 있지도 않은 트래픽으로 잡힌다.
    """
    if kind == "gnss":
        # 🔴 표로 접히지 않는다 — J16 하나뿐이라 반복이 없고, 켜고 끄는 것도
        #    항목 하나(`gnss.enabled`)다. 그리고 **주기가 `tx.period_ms` 가
        #    아니다**(규격 §7.8.6, `GNSS_PERIOD_MS` 주석).
        on = False
        try:
            on = form.row(KEY_GNSS_ENABLED).value == "true"
        except KeyError:
            on = False                        # 옛 펌웨어 — GNSS 항목이 없다
        return TelemetryShape(
            channels=1 if on else 0,
            period_ms=GNSS_PERIOD_MS,
            float_digits=_int_or(form, KEY_FLOAT_DIGITS, 4, minimum=0),
            labels=("J16",) if on else (),
        )

    if kind == "imu":
        # [2026-08-22] GNSS 와 같은 특례 — J16 하나뿐이고, 켜는 것도 항목
        # 하나(`gnss.imu`, 단 GNSS 자체가 켜져 있어야 한다). 주기는 펌웨어가
        # 모듈에 거는 10 Hz 고정(`IMU_PERIOD_MS`).
        on = False
        try:
            on = (form.row(KEY_GNSS_ENABLED).value == "true"
                  and form.row(KEY_GNSS_IMU).value == "true")
        except KeyError:
            on = False                        # 옛 펌웨어 — IMU 항목이 없다
        return TelemetryShape(
            channels=1 if on else 0,
            period_ms=IMU_PERIOD_MS,
            float_digits=_int_or(form, KEY_FLOAT_DIGITS, 4, minimum=0),
            labels=("J16",) if on else (),
        )

    group_name = RECORD_GROUPS.get(kind, kind)
    enabled: list[tuple[int, str]] = []          # (번호, 커넥터 이름)
    for group in form.groups:
        if group.name != group_name:
            continue
        matrix = matrix_of(group)
        if matrix is None:
            continue
        for mrow in matrix.rows:
            for cell in mrow.cells:
                if cell is not None and cell.widget is Widget.TOGGLE:
                    if cell.value == "true":
                        enabled.append((mrow.index, mrow.label))
                    break

    if kind == "i2c":
        ports = i2c_ports(form)
        channels = sum(i2c_record_count(ports.get(index, (0, False))[0])
                       for index, _label in enabled)
    else:
        channels = len(enabled)

    return TelemetryShape(
        channels=max(channels, 0),
        period_ms=_int_or(form, KEY_PERIOD_MS, 100, minimum=1),
        float_digits=_int_or(form, KEY_FLOAT_DIGITS, 4, minimum=0),
        labels=tuple(label for _index, label in enabled),
    )


def i2c_record_count(kind_value: int) -> int:
    """I2C 종류 하나가 주기마다 내는 **레코드 수** = 그 종류가 내는 양의 수.

    🔴 표를 여기에 다시 적지 않는다. `host/gui/screen.py`의
       `I2C_KIND_QUANTITIES`가 유일한 출처다(짝은 펌웨어 `mk_i2c.c`).
       두 벌이 되면
       대시보드가 세우는 카드 수와 대역폭 계산이 갈린다.

    함수 안에서 import 하는 이유는 순환을 피하려는 것이 아니라, 설정 폼이
    대시보드 화면에 **모듈 수준으로 매달리지 않게** 하기 위해서다
    (`channel_ranges`가 `host.core.scaling`을 그렇게 쓰는 것과 같다).
    """
    from host.gui.screen import I2C_KIND_QUANTITIES

    return len(I2C_KIND_QUANTITIES.get(kind_value, ()))


def telemetry_shape(form: "SettingsForm") -> TelemetryShape:
    """`ain` 레코드의 모양 — `record_shape(form, "ain")`의 줄임.

    옛 이름을 그대로 남긴다. 예전에는 마스크가 하나뿐이라 이 함수 하나로
    충분했고, 지금도 `ain` 카드는 여전히 이 이름으로 부른다."""
    return record_shape(form, "ain")


def channel_ranges(form: "SettingsForm") -> dict[int, tuple[float, float]]:
    """채널 번호 → (4 mA 일 때, 20 mA 일 때) 물리량.

    🔴 게이지가 `0 – 150 bar` 를 그리려면 설정을 되짚어야 한다. 보드는
       영점·스케일로 들고 있고 사람은 범위로 생각하며, 그 환산은 규격
       §7.2.1 이 정했다.

    되짚을 수 없는 채널(스케일 0, 값이 반쯤 입력된 중)은 **빠진다.** 없는
    것을 지어내면 게이지가 틀린 눈금을 그린다.
    """
    from host.core.scaling import range_of

    out: dict[int, tuple[float, float]] = {}
    for group in form.groups:
        matrix = matrix_of(group)
        if matrix is None or COL_ZERO not in matrix.columns:
            continue
        if COL_SCALE not in matrix.columns:
            continue
        zi = matrix.columns.index(COL_ZERO)
        si = matrix.columns.index(COL_SCALE)
        for mrow in matrix.rows:
            zero, scale = mrow.cells[zi], mrow.cells[si]
            if zero is None or scale is None:
                continue
            try:
                span = range_of(float(zero.value), float(scale.value))
            except (TypeError, ValueError):
                continue
            if span is not None:
                out[mrow.index] = span
    return out


def channel_units(form: "SettingsForm") -> dict[int, str]:
    """채널 번호 → 단위 문자열.

    🔴 `unit` 은 텔레메트리 필드이기도 하지만 기본 마스크에서 **꺼져 있다**
       (규격 §7.2). 대역폭을 아끼려고 끈 것이고, 그렇다고 화면이 단위를
       모를 이유는 없다 — 사용자가 설정에 적어 두었다.
    """
    out: dict[int, str] = {}
    for group in form.groups:
        matrix = matrix_of(group)
        if matrix is None or COL_UNIT not in matrix.columns:
            continue
        ui = matrix.columns.index(COL_UNIT)
        for mrow in matrix.rows:
            cell = mrow.cells[ui]
            if cell is not None and cell.value:
                out[mrow.index] = cell.value
    return out


def i2c_ports(form: "SettingsForm") -> dict[int, tuple[int, bool]]:
    """I2C 포트 번호(10~15) → (종류, 사용 여부).

    🔴 대시보드가 카드를 몇 장 세울지는 이 값에서 정해진다 — `channel_ranges`
       처럼 화면(`host/gui/screen.py`)에 그대로 건넨다. 종류가 무엇을
       뜻하는지(온습도가 카드 두 장인지 등)는 여기서 판단하지 않는다 —
       이 함수는 폼에서 값을 뽑기만 한다.
    """
    out: dict[int, tuple[int, bool]] = {}
    for group in form.groups:
        matrix = matrix_of(group)
        if matrix is None or COL_KIND not in matrix.columns:
            continue
        if COL_ENABLED not in matrix.columns:
            continue
        ki = matrix.columns.index(COL_KIND)
        ei = matrix.columns.index(COL_ENABLED)
        for mrow in matrix.rows:
            kind_cell = mrow.cells[ki]
            if kind_cell is None:
                continue
            try:
                kind = int(float(kind_cell.value))
            except (TypeError, ValueError):
                continue
            enabled_cell = mrow.cells[ei]
            enabled = enabled_cell is not None and enabled_cell.value == "true"
            out[mrow.index] = (kind, enabled)
    return out


def _int_or(form: "SettingsForm", key: str, fallback: int, *,
            minimum: int) -> int:
    try:
        value = int(float(form.row(key).value))
    except (KeyError, TypeError, ValueError):
        return fallback
    return value if value >= minimum else fallback


def _to_text(value: object) -> str:
    """전선에 나갈 문자열로. `$CFG,SET` 의 인자가 되는 형태다."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # 🔴 repr 을 그대로 쓰면 0.30000000000000004 같은 것이 나간다.
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"
    return str(value)


def _widget_of(item: ConfigItem) -> Widget:
    """모르는 vtype 은 문자열로 그린다.

    🔴 예외를 던지지 않는다. 보드가 새 vtype 을 보냈다고 설정 화면이 통째로
       안 열리면, 사용자는 나머지 44개 항목도 못 고친다. 하나를 문자열로
       그리는 편이 낫다 — 보드가 값을 검사하므로 잘못된 값은 거부된다.
    """
    return _WIDGET_OF.get(item.vtype, Widget.TEXT)


def build_row(item: ConfigItem) -> Row:
    """카탈로그 항목 하나를 화면 한 줄로."""
    editable = not item.readonly
    reason = ""
    if item.readonly:
        # 🔴 `note` 가 있으면 그것이 이유다. 보드가 왜 못 바꾸는지 알려
        #    주는데(예: 쿨링 팬이 5V 레일 직결), 그것을 버리고 "읽기 전용"
        #    이라고만 쓰면 사용자는 이유를 알 수 없다.
        reason = item.note or "이 항목은 보드에서 변경할 수 없다"

    choice_labels = tuple(item.choice_labels)
    # 🔴 링크 속도의 선택지에는 꼬리표를 붙인다 (규격 §4.2.5).
    #
    #    보드가 붙여 보낼 수도 있었지만 안 한다 — 카탈로그 한 줄의 상한
    #    (384바이트)에 여섯 개의 한글 꼬리표가 들어가지 않고, 무엇보다
    #    "실기기에서 확인됐는가" 는 보드가 알 수 있는 사실이 아니다.
    #    그것은 사람이 쌓은 기록이고, 그 기록은 호스트에 있다.
    if is_link_baud(item.key) and not choice_labels:
        choice_labels = tuple(baud_choice_label(c) for c in item.choices)

    return Row(
        key=item.key,
        label=item.label or item.key,
        widget=_widget_of(item),
        value=_to_text(item.current),
        default=_to_text(item.default),
        unit=item.unit,
        minimum=item.minimum,
        maximum=item.maximum,
        choices=tuple(item.choices),
        choice_labels=choice_labels,
        editable=editable,
        reason=reason,
        # 🔴 편집 가능해도 사유를 버리지 않는다. Row.note 의 설명 참조.
        note=item.note,
        is_integer=item.vtype in ("u8", "u16", "u32"),
    )


class SettingsForm:
    """카탈로그로 만든 폼. 편집 중인 값과 보드의 값을 함께 들고 있는다."""

    def __init__(self, schema: ConfigSchema) -> None:
        self._schema = schema
        self._rows: dict[str, Row] = {}
        self._original: dict[str, str] = {}
        self._edited: dict[str, str] = {}

        for key, item in schema.items.items():
            row = build_row(item)
            self._rows[key] = row
            self._original[key] = row.value

    # ------------------------------------------------------------- 읽기

    @property
    def groups(self) -> list[Group]:
        """그룹별로 묶은 줄들. 보드가 보낸 순서를 지킨다."""
        out: dict[str, Group] = {}
        for name in self._schema.groups():
            out[name] = Group(name=name)
        for key, item in self._schema.items.items():
            out.setdefault(item.group, Group(name=item.group))
            out[item.group].rows.append(self.row(key))
        return [g for g in out.values() if g.rows]

    def row(self, key: str) -> Row:
        """지금 화면에 떠 있는 상태의 줄."""
        base = self._rows[key]
        if key in self._edited:
            return Row(**{**base.__dict__, "value": self._edited[key]})
        return base

    def keys(self) -> list[str]:
        return list(self._rows)

    @property
    def fields(self) -> dict[int, object]:
        """NDJSON 필드 비트들 — 보드가 `cfg_field` 로 알려 준 것 (규격 §7.3).

        🔴 비트 목록을 호스트가 들고 있지 않다. 어떤 필드가 있는지, 무슨
           이름인지, 기본으로 켜져 있는지 전부 보드가 말한다. 그래야 펌웨어가
           필드를 늘려도 화면이 따라간다.
        """
        return dict(self._schema.fields)

    # ------------------------------------------------------------- 편집

    def edit(self, key: str, text: str) -> None:
        """화면 값을 바꾼다. 검사는 하지 않는다 — 타이핑 중이기 때문이다.

        🔴 한 글자 칠 때마다 빨간 줄을 긋지 않는다. `12` 를 치는 도중의
           `1` 은 범위 밖일 수 있는데, 그때마다 오류를 띄우면 읽을 수 없다.
           검사는 `validate` 와 보낼 때 한다.
        """
        if key not in self._rows:
            raise KeyError(key)
        self._edited[key] = text

    def revert(self, key: str) -> None:
        self._edited.pop(key, None)

    def revert_all(self) -> None:
        self._edited.clear()

    def reset_to_default(self, key: str) -> None:
        self._edited[key] = self._rows[key].default

    def is_dirty(self, key: str) -> bool:
        return key in self._edited and self._edited[key] != self._original[key]

    @property
    def dirty_keys(self) -> list[str]:
        """보드에 보낼 것들. 원래 값과 같아진 것은 빠진다.

        🔴 고쳤다가 되돌린 항목을 보내지 않는다. 규격상 같은 값을 쓰는 것은
           거부되지 않지만(§5), 보낼 이유가 없는 줄로 링크를 채우지 않는다 —
           115200 에서 여유가 초당 600 바이트뿐이다.
        """
        return [k for k in self._rows if self.is_dirty(k)]

    @property
    def has_changes(self) -> bool:
        return bool(self.dirty_keys)

    # ------------------------------------------------------------- 검사

    def validate(self, key: str) -> str:
        """지금 값이 보낼 수 있는 값인지. 문제가 없으면 빈 문자열.

        보드가 최종 판정을 하지만, 보내기 전에 걸러 주면 왕복 한 번을 아낀다.
        인터록은 여기서 판정하지 않는다 — 보드만 안다.
        """
        row = self.row(key)
        if not row.editable:
            return row.reason
        text = row.value
        if len(text.encode("utf-8")) > MAX_ARG_BYTES:
            # 🔴 전선 상한을 넘기면 보드가 조용히 버린다(규격 §3.1).
            #    응답이 없는 것을 사용자는 "먹통" 으로 읽는다.
            return f"{MAX_ARG_BYTES}바이트를 넘는다 (지금 {len(text.encode())})"
        try:
            self._schema.validate(key, text)
        except ConfigError as exc:
            return exc.detail or exc.reason
        return ""

    def errors(self) -> dict[str, str]:
        """보낼 것들 중 문제가 있는 것만."""
        out = {}
        for key in self.dirty_keys:
            msg = self.validate(key)
            if msg:
                out[key] = msg
        return out

    # ------------------------------------------------------------- 보내기

    def pending_changes(self) -> list[tuple[str, str]]:
        """보낼 (키, 값) 목록. 문제가 있는 것은 빠진다."""
        bad = self.errors()
        return [(k, self.row(k).value) for k in self.dirty_keys if k not in bad]

    def accept(self, key: str) -> None:
        """보드가 받아들였다. 화면 값을 보드의 값으로 굳힌다."""
        if key in self._edited:
            self._original[key] = self._edited[key]
            base = self._rows[key]
            self._rows[key] = Row(**{**base.__dict__, "value": self._edited[key]})
            del self._edited[key]

    def reject(self, key: str, reason: str) -> str:
        """보드가 거부했다. 화면 값은 그대로 두고 사유를 돌려준다.

        🔴 거부됐다고 값을 되돌리지 않는다. 사용자가 방금 친 것을 화면에서
           지우면 무엇을 고치려 했는지 사라진다. 사유를 보여 주고 고칠
           기회를 준다.
        """
        return reason
