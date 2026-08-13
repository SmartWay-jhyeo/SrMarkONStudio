"""시뮬레이터 설정 저장소 — 펌웨어 config_store 모듈의 기준 구현.

여기서 정한 항목·타입·범위·기본값이 그대로 펌웨어로 간다.
검증 순서는 규격 §5 를 따른다: 키 존재 → 읽기 전용 → 타입·범위 → 인터록.

인터록을 범위 검사 뒤에 두는 이유: 값 자체가 틀린 것과 안전 정책상 거부된
것은 사용자에게 다른 메시지여야 한다.
"""

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from host.core.errors import ConfigError, Reason
from host.core.records import SCHEMA_VER

#: NDJSON 필드 마스크 비트 정의 (규격 §7.2)
FIELD_BITS: tuple[tuple[int, str, bool, str], ...] = (
    (0, "device_id", False, "보드 식별자"),
    (1, "time_source", True, "시간 소스"),
    (2, "time_quality", False, "시간 품질"),
    (3, "raw", True, "ADS1256 원시 카운트"),
    (4, "ma", True, "전류 (mA)"),
    (5, "value", True, "물리량 환산"),
    (6, "unit", False, "단위"),
    (7, "status", True, "채널 상태"),
    (8, "capture_counter", False, "획득 카운터"),
    (9, "connector_id", True, "커넥터 번호"),
)

#: ADS1256 지원 DRATE (SPS)
DRATE_CHOICES = (2, 5, 10, 15, 25, 30, 50, 60, 100, 500, 1000, 2000, 3750, 7500)

_TRUE_WORDS = ("true", "1", "on", "yes")
_FALSE_WORDS = ("false", "0", "off", "no")
_INT_TYPES = ("u8", "u16", "u32")

#: 프로토콜 구분자. 설정값에 들어가면 줄 구조가 깨진다.
_PROTOCOL_DELIMITERS = frozenset("$,*")

#: 🔴 설정 문자열에 허용되는 문자 — 인쇄 가능 ASCII 에서 구분자를 뺀 것.
#:
#: `isascii()` 만으로는 부족하다. 제어문자가 전부 ASCII 이기 때문이다:
#: '\n'.isascii() 도 '\x00'.isascii() 도 True 다.
#:
#: 이 한 줄이 막는 것들:
#:   '\r' '\n'  — 값 안에 줄바꿈을 넣어 `$CFG,SET,dev.id,x\r\n$HB*0A` 처럼
#:                완결된 명령을 주입하는 공격. 주입된 $HB 는 체크섬까지
#:                유효해서 보드가 진짜 하트비트로 받아 CONFIG 모드를 연다
#:   '\x00'     — C 펌웨어에서 문자열이 중간에 끝난 것처럼 처리됨.
#:                호스트가 검증한 값과 펌웨어가 해석한 값이 달라진다
#:   ','        — 명령 인자를 추가로 쪼갬
#:   '*'        — 체크섬 구분자로 오인
#:   '$'        — 명령 시작으로 오인
#:
#: 단위는 'degC'·'kPa'·'LPM'·'%' 처럼 쓴다. 비 ASCII 를 막는 부수 효과로
#: 문자 수 = 바이트 수가 되어 str<=7 이 펌웨어 고정폭 버퍼와 정확히 맞는다.
_ALLOWED_STR_CHARS = frozenset(
    chr(c) for c in range(0x20, 0x7F)
) - _PROTOCOL_DELIMITERS


@dataclass
class SimConfigItem:
    key: str
    group: str
    vtype: str
    default: object
    current: object
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    readonly: bool = False
    label: str = ""
    note: str = ""
    choices: tuple = ()
    #: 참이면 값 변경 시도를 INTERLOCK 으로 거부한다 (현재값과 같으면 통과)
    interlocked: bool = False


class ConfigStore:
    """설정 항목 보관, 검증, 영속."""

    def __init__(self, items: list[SimConfigItem], path: Path | None = None):
        self.items: dict[str, SimConfigItem] = {i.key: i for i in items}
        self.path = path
        self.dirty = False
        self.load_failed = False
        #: load() 가 거부하고 기본값을 유지한 키들
        self.rejected_keys: list[str] = []

    # ------------------------------------------------------------------ 조회
    def get(self, key: str) -> object:
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)
        return item.current

    @property
    def field_mask(self) -> int:
        return int(self.items["tx.fields"].current)

    # ------------------------------------------------------------------ 변경
    def set(self, key: str, raw: str) -> None:
        """문자열 값을 검증해 반영한다. 규격 §5 의 순서를 지킨다."""
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)

        value = _coerce(item, raw)

        if value == item.current:
            return                          # 변화 없음 — 거부할 이유가 없다

        if item.interlocked:
            raise ConfigError(Reason.INTERLOCK, item.note or key)
        if item.readonly:
            raise ConfigError(Reason.READONLY, item.note or key)

        previous = item.current
        item.current = value
        try:
            self._check_combination(previous_margin=_margin_of(self, previous, item))
        except ConfigError:
            item.current = previous
            raise

        self.dirty = True

    def _check_combination(self, *, previous_margin: float) -> None:
        """여러 항목이 얽힌 제약을 검사한다 (설계 §6.4).

        🔴 **실현 가능성 여유(margin)를 비교한다. 수요만 보면 안 된다.**

        이미 초과 상태에 빠진 저장소에서 빠져나올 길은 열어둬야 한다 — 채널을
        끄거나 주기를 늘리거나 **DRATE 를 올리는** 변경은 무조건 통과해야 한다.

        그런데 `required_sps` 만 비교하면 `adc.drate` 를 **낮추는** 변경도
        "수요가 안 늘었다"로 판정되어 검사를 건너뛴다. drate 는 수요가 아니라
        **공급**이기 때문이다. 그러면 7채널 10ms(700 SPS) 상태에서 drate 를
        2 SPS 로 내리는 것이 수락되고, 용량 검사가 막으려던 바로 그 상태가
        된다. 사용자가 가장 흔히 만지는 노브로 기능 전체가 무력화된다.

        여유 = 요구 − 가용 으로 보면 양쪽이 한 식에 들어온다. 여유가 나빠지지
        않는 변경만 검사를 건너뛴다.
        """
        from tools.simulator.capacity import check_capacity, margin_sps

        if margin_sps(self) <= previous_margin:
            return                          # 실현 가능성이 나빠지지 않았다

        check_capacity(self)

    def reset(self) -> None:
        for item in self.items.values():
            item.current = item.default
        self.dirty = True

    # ---------------------------------------------------------------- 영속화
    def save(self) -> None:
        if self.path is None:
            self.dirty = False
            return
        payload = {k: i.current for k, i in self.items.items()}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.dirty = False

    def load(self) -> None:
        """저장된 값을 복원한다. 파일이 없거나 깨졌으면 기본값을 유지한다.

        🔴 **저장값도 전부 재검증한다.** 저장 매체는 신뢰 대상이 아니다 —
        Flash 가 손상되거나 사람이 파일을 손으로 고칠 수 있다. 검증 없이
        대입하면 `pwr.5v = false` 같은 인터록 항목이나 범위 밖 값이
        그대로 살아난다. 그러면 쿨링 팬이 꺼진 채로 부팅한다.

        복원할 수 없는 항목은 **기본값을 유지하고** `rejected_keys` 에
        남긴다. 부팅을 막지는 않는다 — 설정 하나가 깨졌다고 보드가 죽으면
        복구 수단까지 사라진다.
        """
        self.load_failed = False
        self.rejected_keys = []
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("객체가 아님")
        except (json.JSONDecodeError, ValueError, OSError):
            self.load_failed = True
            self.reset()
            self.dirty = False
            return

        for key, value in payload.items():
            item = self.items.get(key)
            if item is None:
                continue                         # 모르는 키는 조용히 무시

            if item.interlocked or item.readonly:
                # 정책·안전 항목은 저장값을 신뢰하지 않는다. 기본값을 쓴다.
                if value != item.default:
                    self.rejected_keys.append(key)
                continue

            try:
                item.current = _coerce(item, _to_raw(value))
            except ConfigError:
                self.rejected_keys.append(key)   # 기본값 유지

        # 🔴 개별 항목이 전부 유효해도 **조합**이 불가능할 수 있다.
        #
        # 손편집하거나 손상된 파일이 7채널 10ms + drate 2 SPS 를 담고 있으면
        # 위 루프는 한 항목도 거부하지 않는다. 그대로 부팅하면 큐가 영구히
        # 넘치는데 아무 신호도 없다. `load()` 의 전제("저장 매체는 신뢰 대상이
        # 아니다")를 항목 단위로만 지킨 셈이다.
        #
        # 부팅을 막지는 않는다 — 기본값으로 되돌리고 표시만 남긴다.
        try:
            from tools.simulator.capacity import check_capacity

            check_capacity(self)
        except ConfigError:
            self.rejected_keys.append("<combination>")
            self.load_failed = True
            self.reset()

        self.dirty = False

    # ---------------------------------------------------------------- 카탈로그
    def catalog_lines(self) -> Iterator[str]:
        """$CFG,LIST 응답 줄들을 생성한다 (규격 §7.3)."""
        for item in self.items.values():
            rec: dict = {
                "schema_ver": SCHEMA_VER,
                "seq": 0,
                "t": 0,
                "type": "cfg_item",
                "key": item.key,
                "grp": item.group,
                "vtype": item.vtype,
                "default": item.default,
                "cur": item.current,
                "ro": item.readonly,
                "label": item.label,
            }
            if item.minimum is not None:
                rec["min"] = item.minimum
            if item.maximum is not None:
                rec["max"] = item.maximum
            if item.unit:
                rec["unit"] = item.unit
            if item.note:
                rec["note"] = item.note
            if item.choices:
                rec["choices"] = list(item.choices)
            yield json.dumps(rec, ensure_ascii=False)

        for bit, name, default, label in FIELD_BITS:
            yield json.dumps(
                {
                    "schema_ver": SCHEMA_VER,
                    "seq": 0,
                    "t": 0,
                    "type": "cfg_field",
                    "bit": bit,
                    "name": name,
                    "default": default,
                    "label": label,
                },
                ensure_ascii=False,
            )

        yield json.dumps(
            {
                "schema_ver": SCHEMA_VER,
                "seq": 0,
                "t": 0,
                "type": "cfg_end",
                "count": len(self.items) + len(FIELD_BITS),
            }
        )


def _to_raw(value: object) -> str:
    """JSON 에서 읽은 값을 _coerce 가 받는 문자열 형태로 되돌린다.

    저장은 JSON 네이티브 타입으로 하지만 검증 경로는 하나뿐이어야 한다.
    파일에서 온 값과 $CFG,SET 으로 온 값이 서로 다른 검사를 받으면,
    한쪽만 막히는 구멍이 반드시 생긴다.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce(item: SimConfigItem, raw: str) -> object:
    """문자열을 vtype 에 맞게 변환하고 범위를 검사한다."""
    if item.vtype == "bool":
        low = raw.strip().lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        raise ConfigError(Reason.RANGE, f"불리언이 아님: {raw!r}")

    if item.vtype == "str":
        bad = [c for c in raw if c not in _ALLOWED_STR_CHARS]
        if bad:
            raise ConfigError(Reason.RANGE, f"허용되지 않는 문자: {bad!r}")
        if item.maximum is not None and len(raw) > int(item.maximum):
            raise ConfigError(
                Reason.RANGE, f"최대 {int(item.maximum)}자, 받음 {len(raw)}자"
            )
        return raw

    if item.vtype == "enum":
        try:
            value: object = int(raw)
        except ValueError:
            value = raw
        if value not in item.choices:
            raise ConfigError(Reason.RANGE, f"허용값 {list(item.choices)}")
        return value

    if item.vtype in _INT_TYPES:
        try:
            # 🔴 base 0 을 쓰면 안 된다. int("08", 0) 은 ValueError 다 —
            # 파이썬이 앞의 0 을 8진수 접두사로 읽기 때문이다. 사용자가
            # 고정폭 습관으로 "08" 을 넣는 건 지극히 자연스럽고, 그게
            # "정수가 아님" 으로 거부되면 원인을 짐작할 수도 없다.
            value = int(raw, 10)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"정수가 아님: {raw!r}") from None
    elif item.vtype == "f32":
        try:
            value = float(raw)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"실수가 아님: {raw!r}") from None
        # 🔴 float("nan") / float("inf") 는 파싱을 통과한다. 그리고 NaN 은
        # 아래 범위 검사도 뚫는다 — nan < min 도 nan > max 도 False 이기
        # 때문이다. 여기서 막지 않으면 변환식에 NaN 이 들어가 모든 측정값이
        # 조용히 NaN 이 된다.
        if not math.isfinite(value):
            raise ConfigError(Reason.RANGE, f"유한한 실수가 아님: {raw!r}")
    else:
        raise ConfigError(Reason.RANGE, f"알 수 없는 타입: {item.vtype}")

    if item.minimum is not None and value < item.minimum:
        raise ConfigError(Reason.RANGE, f"최소 {item.minimum}, 받음 {value}")
    if item.maximum is not None and value > item.maximum:
        raise ConfigError(Reason.RANGE, f"최대 {item.maximum}, 받음 {value}")
    return value


def _default_field_mask() -> int:
    mask = 0
    for bit, _name, default, _label in FIELD_BITS:
        if default:
            mask |= 1 << bit
    return mask


def _margin_of(store: "ConfigStore", previous_value: object,
               item: SimConfigItem) -> float:
    """변경 직전의 실현 가능성 여유(요구 − 가용)를 계산한다.

    수요(`required_sps`)만이 아니라 공급(`available_sps`)까지 포함해야
    `adc.drate` 를 낮추는 변경도 "나빠졌다"로 잡힌다. `_check_combination`
    주석 참조.
    """
    from tools.simulator.capacity import margin_sps

    current = item.current
    item.current = previous_value
    try:
        return margin_sps(store)
    finally:
        item.current = current


def default_store(path: Path | None = None) -> ConfigStore:
    """스펙 §6.1 의 설정 항목 전체를 담은 저장소를 만든다."""
    mask = _default_field_mask()
    items: list[SimConfigItem] = [
        SimConfigItem("dev.id", "dev", "str", "1", "1", maximum=15,
                      label="장치 ID"),
        SimConfigItem("tx.fields", "tx", "u32", mask, mask,
                      minimum=0, maximum=0xFFFFFFFF, label="NDJSON 필드 마스크"),
        SimConfigItem("tx.period_ms", "tx", "u16", 100, 100,
                      minimum=10, maximum=10000, unit="ms", label="전송 주기"),
        SimConfigItem("tx.float_digits", "tx", "u8", 4, 4,
                      minimum=2, maximum=6, label="실수 자릿수"),
        SimConfigItem("adc.pga", "adc", "enum", 1, 1,
                      choices=(1, 2, 4, 8, 16, 32, 64), label="PGA"),
        SimConfigItem("adc.drate", "adc", "enum", 60, 60,
                      choices=DRATE_CHOICES, unit="SPS", label="데이터율"),
        SimConfigItem("pwr.24v", "pwr", "bool", False, False,
                      label="24V 레일 (PD8)"),
        SimConfigItem("pwr.14v9", "pwr", "bool", False, False,
                      label="14.9V 레일 (PD9)"),
        SimConfigItem(
            "pwr.5v", "pwr", "bool", True, True,
            readonly=True, interlocked=True, label="5V 레일 (PD10)",
            note="쿨링 팬이 5V 레일 직결이라 끌 수 없다 (데이터시트 §5.14)",
        ),
        SimConfigItem("pwr.seq_delay_ms", "pwr", "u16", 500, 500,
                      minimum=0, maximum=5000, unit="ms", label="레일 기동 간격"),
    ]

    for ch in range(7):
        connector = ch + 3                       # AIN0 → J3
        items += [
            SimConfigItem(f"ain{ch}.enabled", "ain", "bool",
                          ch == 0, ch == 0, label=f"J{connector} 사용"),
            SimConfigItem(f"ain{ch}.period_ms", "ain", "u16", 100, 100,
                          minimum=10, maximum=60000, unit="ms",
                          label=f"J{connector} 수집 주기"),
            SimConfigItem(f"ain{ch}.zero", "ain", "f32", 4.0, 4.0,
                          unit="mA", label=f"J{connector} 영점"),
            SimConfigItem(f"ain{ch}.scale", "ain", "f32", 1.0, 1.0,
                          label=f"J{connector} 스케일"),
            SimConfigItem(f"ain{ch}.unit", "ain", "str", "", "",
                          maximum=7, label=f"J{connector} 단위"),
        ]

    return ConfigStore(items, path)
