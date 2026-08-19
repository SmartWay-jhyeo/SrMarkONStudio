"""시뮬레이터 설정 저장소 — 펌웨어 config_store 모듈의 기준 구현.

여기서 정한 항목·타입·범위·기본값이 그대로 펌웨어로 간다.

검증 순서 (규격 §5.2, 코드와 일치해야 한다):

    키 존재 → 타입·범위 → 현재값과 동일? → 인터록 → 읽기 전용 → 조합 검사

- **인터록이 범위 검사 뒤**인 이유: 값 자체가 틀린 것과 안전 정책상 거부된
  것은 사용자에게 다른 메시지여야 한다.
- **인터록이 읽기 전용보다 앞**인 이유: 둘 다 걸리는 항목(`pwr.5v`)에서
  READONLY 만 돌려주면 `note` 에 담긴 하드웨어 사유가 사라진다.
- **조합 검사가 마지막**인 이유: 어차피 거부될 변경을 미리 반영해 보고
  되돌리는 것은 낭비이고, 읽기 전용 값을 잠시라도 바꾸는 것은 위험하다.
- **현재값과 같은 값을 쓰는 것은 거부하지 않는다**: 호스트가 전체 설정을
  한꺼번에 되쓸 때 불필요한 거부가 나지 않게 한다.
"""

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from host.core.errors import ConfigError, Reason
from host.core.records import SCHEMA_VER

#: NDJSON 필드 마스크 비트 정의 (규격 §7.2)
#:
#: 🔴 [판단, 2026-08-19] `time_source` 는 여기 없다 — 펌웨어
#:    (firmware/stage1/app/mk_cfgtable.c FIELDS)와 같은 이유로 뺐다. `t` 가
#:    UTC epoch_ms 인지 부팅 후 경과 ms 인지는 이 필드만이 말해 주므로,
#:    마스크로 끌 수 있으면 device_clock 의 `t` 를 호스트가 UTC 로 오해해
#:    저장하는 사고가 되돌아온다. `i2c` 의 `quantity`·`value`, `din` 의
#:    `connector_id`·`state` 와 같은 자리로 옮겼다(항상 실린다, telemetry.py
#:    참고). bit=1 자리는 비워 둔다 — 다른 필드가 새로 쓰지 않는다.
#:
#: 🔴 [개정, 2026-08-19] 다섯 번째 자리(`records`)가 늘었다 — 이 비트가
#:    어느 레코드(`ain`·`i2c`·`din`)의 마스크에 해당하는가다. `tx.fields`
#:    하나가 셋을 전부 묶던 것을 나누며(사용자 확정 2026-08-18) 생겼다.
#:    비트 번호는 이 표 하나를 셋이 공유하지만 해당 레코드는 비트마다
#:    다르다 — mk_cfgtable.c 의 FIELDS 와 한 글자도 다르면 안 된다
#:    (crosscheck_cfgtable.py 가 대조한다).
FIELD_BITS: tuple[tuple[int, str, bool, str, tuple[str, ...]], ...] = (
    (0, "device_id", False, "보드 식별자", ("ain", "i2c", "din")),
    (2, "time_quality", False, "시간 품질", ("ain", "i2c", "din")),
    (3, "raw", True, "ADS1256 원시 카운트", ("ain",)),
    (4, "ma", True, "전류 (mA)", ("ain",)),
    (5, "value", True, "물리량 환산", ("ain",)),
    (6, "unit", False, "단위", ("ain",)),
    (7, "status", True, "채널 상태", ("ain", "i2c")),
    (8, "capture_counter", False, "획득 카운터", ("ain",)),
    (9, "connector_id", True, "커넥터 번호", ("ain", "i2c")),
)

#: 레코드 종류 → 자기 마스크 설정 키. `tx.fields` 를 나눈 세 항목이다
#: (규격 §7.2·§7.5·§7.6). `gnss_raw` 는 전용 키가 없다 — `ain` 을 빌려
#: 쓴다(§7.7, mk_telem.c build_gnss_raw_record 와 같은 판단).
FIELD_MASK_KEYS: dict[str, str] = {
    "ain": "tx.fields_ain",
    "i2c": "tx.fields_i2c",
    "din": "tx.fields_din",
}

#: 전선 위 JSON 형식. 🔴 보드가 쓰는 것과 같아야 한다.
#:
#: 기본값(`", "`, `": "`)은 공백을 넣어 줄을 10% 넘게 늘린다. 대역폭 계산
#: (docs/measurements/2026-08-14_link_budget.md)에서 기본 설정이 115200 의
#: 94.8% 를 쓰고 있으므로 그 공백은 공짜가 아니다. 게다가 펌웨어의 mk_json
#: 은 압축 형식만 낸다 — 두 형식이 섞이면 두 구현을 바이트로 대조할 수 없다.
_COMPACT = (",", ":")

#: I2C 포트에 꽂을 수 있는 센서 **종류** — (값, 이름표, 내는 양들).
#:
#: 🔴 칩 모델이 아니라 종류다 (사용자 확정 2026-08-17). 조도계가 BH1750 이든
#:    다른 것이든 호스트가 할 일은 같고, 펌웨어만 드라이버를 고르면 된다.
#:    모델로 두면 새 칩을 살 때마다 카탈로그를 고쳐야 한다.
#:
#: 🔴 값이 둘인 종류는 온습도뿐이다. 나머지는 전부 하나다.
I2C_KIND_NONE = 0
I2C_KINDS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (I2C_KIND_NONE, "없음", ()),
    (1, "조도", ("lux",)),
    (2, "온습도", ("temp", "humidity")),
    (3, "적외 온도", ("temp_object",)),
    (4, "방수 온도", ("temp",)),
)

#: 종류 값 → 내는 양들.
I2C_QUANTITIES = {k: q for k, _l, q in I2C_KINDS}

#: I2C 센서 포트 (데이터시트 §5.4). 짝끼리 같은 버스다 — J10·J11 = I2C3.
I2C_PORTS: tuple[int, ...] = (10, 11, 12, 13, 14, 15)

#: ADS1256 지원 DRATE (SPS)
DRATE_CHOICES = (2, 5, 10, 15, 25, 30, 50, 60, 100, 500, 1000, 2000, 3750, 7500)

#: GNSS UART 통신속도(Phase 3). 업계에서 흔히 쓰는 NMEA 보율 상수다 — UM981
#: 자체의 공장 기본값은 데이터시트를 확보하지 못해 **확인 필요**다(§5).
#: firmware/stage1/app/mk_cfgtable.c 의 GNSS_BAUD_CHOICES 와 값이 같아야
#: 한다 — crosscheck_cfgtable.py 가 대조한다.
GNSS_BAUD_CHOICES = (4800, 9600, 19200, 38400, 57600, 115200, 230400)

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
    #: `choices` 와 같은 순서의 이름표 (규격 §7.3). 번호에 뜻이 없는 열거
    #: (센서 종류 등)에만 쓴다 — PGA 배율처럼 숫자가 곧 뜻이면 필요 없다.
    choice_labels: tuple = ()
    #: 참이면 값 변경 시도를 INTERLOCK 으로 거부한다 (현재값과 같으면 통과)
    interlocked: bool = False

    #: 참이면 이 항목이 **실제 출력을 움직인다** (전원 레일·밸브·LED).
    #:
    #: 🔴 TEST 제어 모드에서는 저장되지 않고, 모드를 벗어날 때 기본값으로
    #:    돌아간다 (규격 §6.4). 항목 이름으로 짐작하지 않도록 보드가
    #:    카탈로그에 실어 보낸다.
    out: bool = False


class ConfigStore:
    """설정 항목 보관, 검증, 영속."""

    def __init__(self, items: list[SimConfigItem], path: Path | None = None):
        self.items: dict[str, SimConfigItem] = {i.key: i for i in items}
        self.path = path
        self.dirty = False
        self.load_failed = False
        #: load() 가 거부하고 기본값을 유지한 키들
        self.rejected_keys: list[str] = []
        #: 마지막 save() 가 남긴 값들 — "플래시에 무엇이 들어 있나".
        #: 🔴 TEST 에서 출력을 건너뛰었는지 확인할 방법이 이것뿐이다.
        self._saved: dict[str, object] = {}

    # ------------------------------------------------------------------ 조회
    def get(self, key: str) -> object:
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)
        return item.current

    def field_mask(self, kind: str) -> int:
        """`kind`(`ain`·`i2c`·`din`) 레코드가 쓸 마스크 값.

        🔴 [개정, 2026-08-19] `tx.fields` 하나였던 것을 셋으로 나눴다 —
           `ain` 의 마스크를 바꿔도 `i2c`·`din` 은 그대로여야 한다."""
        return int(self.items[FIELD_MASK_KEYS[kind]].current)

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

    def revert_outputs(self) -> list[str]:
        """출력 항목만 기본값으로. 되돌린 키들을 돌려준다 (규격 §6.4).

        🔴 "안전 상태" 는 전부 꺼짐이 아니라 **각 항목의 기본값**이다.
           `pwr.5v` 의 기본값은 켜짐이고, 거기에 쿨링 팬과 ADS1256 의
           아날로그 전원이 걸려 있다 — 테스트를 끝냈다고 팬을 세우면 안 된다.
        """
        changed = []
        for key, item in self.items.items():
            if item.out and item.current != item.default:
                item.current = item.default
                changed.append(key)
        if changed:
            self.dirty = True
        return changed

    # ---------------------------------------------------------------- 영속화
    def saved_value(self, key: str) -> object:
        """마지막 `save()` 가 남긴 값. 없으면 `None`.

        플래시에 무엇이 들어 있는지를 시험과 화면이 물어볼 수 있어야 한다 —
        TEST 에서 출력을 건너뛰었는지 확인할 방법이 그것뿐이다.
        """
        return self._saved.get(key)

    def save(self, *, skip_outputs: bool = False) -> None:
        """🔴 `skip_outputs` 는 TEST 제어 모드에서 참이다 (규격 §6.4).

        벤치에서 배선을 보려고 밸브를 한 번 열어 본 것이 플래시에 남아
        다음 부팅에 되살아나면 안 된다.
        """
        # 🔴 출력 항목은 **기본값으로** 남긴다. 이전 저장값을 지키는 편이
        #    친절해 보이지만, 그러려면 "플래시에 뭐가 있었나" 를 따로 들고
        #    있어야 하고 펌웨어에는 그런 자리가 없다. 두 구현이 갈리느니
        #    한 규칙으로 간다 — 테스트에서 만진 출력은 저장되지 않는다.
        self._saved.update({
            k: (i.default if (skip_outputs and i.out) else i.current)
            for k, i in self.items.items()
        })
        if self.path is not None:
            self.path.write_text(
                json.dumps(self._saved, ensure_ascii=False, indent=2),
                encoding="utf-8",
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
            # 🔴 출력 항목만 표시한다. 대부분의 항목에 붙지 않는 필드를
            #    전부에 실으면 카탈로그가 그만큼 길어지고, 카탈로그는
            #    115200 에서 몇 초씩 걸린다.
            if item.out:
                rec["out"] = True
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
                if item.choice_labels:
                    rec["choice_labels"] = list(item.choice_labels)
            yield json.dumps(rec, ensure_ascii=False, separators=_COMPACT)

        for bit, name, default, label, records in FIELD_BITS:
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
                    # 🔴 규격 §7.3 개정 — 이 비트가 어느 레코드에 해당하는지
                    #    호스트가 이것으로 마스크 카드를 셋으로 나눠 그린다.
                    "records": list(records),
                },
                ensure_ascii=False,
                separators=_COMPACT,
            )

        yield json.dumps(
            {
                "schema_ver": SCHEMA_VER,
                "seq": 0,
                "t": 0,
                "type": "cfg_end",
                "count": len(self.items) + len(FIELD_BITS),
            },
            ensure_ascii=False,
            separators=_COMPACT,
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


def _default_field_mask(kind: str) -> int:
    """`kind` 에 해당하는 비트만으로 기본 마스크를 만든다.

    🔴 [개정, 2026-08-19] `tx.fields` 를 셋으로 나누며 `kind` 를 받는다 —
       손으로 상수를 적지 않고 FIELD_BITS 표에서 끌어낸다(펌웨어
       mk_cfgtable.c 의 field_mask_bounds() 와 같은 계산)."""
    mask = 0
    for bit, _name, default, _label, records in FIELD_BITS:
        if kind in records and default:
            mask |= 1 << bit
    return mask


def _all_field_mask(kind: str) -> int:
    """`kind` 에 해당하는 **모든** 비트. `tx.fields_<kind>` 의 상한이다.

    🔴 0xFFFFFFFF 를 상한으로 두면 호스트가 뜻 없는 비트를 켤 수 있고,
       보드는 그것을 수락한 뒤 무시한다 — 화면에는 켜진 것으로 남는다.
       그렇다고 손으로 상수를 적으면 비트를 하나 더 붙이는 순간 새 비트를
       켤 수 없게 되는데, 그 사실이 코드 어디에도 드러나지 않는다.
    """
    mask = 0
    for bit, _name, _default, _label, records in FIELD_BITS:
        if kind in records:
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
    # 🔴 [개정, 2026-08-19] `tx.fields` 하나였던 것을 ain·i2c·din 셋으로
    #    나눴다(규격 §7.2·§7.5·§7.6) — `ain` 의 `raw` 를 꺼도 `i2c` 와는
    #    무관해야 하는데, 마스크가 하나면 그럴 수 없었다.
    mask_ain = _default_field_mask("ain")
    mask_i2c = _default_field_mask("i2c")
    mask_din = _default_field_mask("din")
    items: list[SimConfigItem] = [
        SimConfigItem("dev.id", "dev", "str", "1", "1", maximum=15,
                      label="장치 ID"),
        SimConfigItem("tx.fields_ain", "tx", "u32", mask_ain, mask_ain,
                      minimum=0, maximum=_all_field_mask("ain"),
                      label="NDJSON 필드 마스크 (아날로그)"),
        SimConfigItem("tx.fields_i2c", "tx", "u32", mask_i2c, mask_i2c,
                      minimum=0, maximum=_all_field_mask("i2c"),
                      label="NDJSON 필드 마스크 (I2C)"),
        SimConfigItem("tx.fields_din", "tx", "u32", mask_din, mask_din,
                      minimum=0, maximum=_all_field_mask("din"),
                      label="NDJSON 필드 마스크 (디지털 입력)"),
        SimConfigItem("tx.period_ms", "tx", "u16", 100, 100,
                      minimum=10, maximum=10000, unit="ms", label="전송 주기"),
        SimConfigItem("tx.float_digits", "tx", "u8", 4, 4,
                      minimum=2, maximum=6, label="실수 자릿수"),
        SimConfigItem("adc.pga", "adc", "enum", 1, 1,
                      choices=(1, 2, 4, 8, 16, 32, 64), label="PGA"),
        SimConfigItem("adc.drate", "adc", "enum", 60, 60,
                      choices=DRATE_CHOICES, unit="SPS", label="데이터율"),
        # 🔴 라벨에 핀 번호를 쓰지 않는다 (설계 원칙 1, CLAUDE.md §3).
        #    화면과 프로토콜은 `PD8` 이 아니라 `24V 전원` 을 쓴다. PCB 배선은
        #    고정이므로 사용자가 핀을 알아야 할 이유가 없고, 알려 주면 임의로
        #    바꿀 수 있다는 인상을 준다.
        SimConfigItem("pwr.24v", "pwr", "bool", False, False,
                      label="24V 전원", out=True),
        SimConfigItem("pwr.14v9", "pwr", "bool", False, False,
                      label="14.9V 전원", out=True),
        SimConfigItem(
            # 🔴 5V 도 끌 수 있다 (사용자 확정 2026-08-14). 막지 않는 대신
            #    무엇이 함께 멈추는지 note 가 말하고, 화면이 그 문구를
            #    그대로 띄운다(규격 §7.3).
            "pwr.5v", "pwr", "bool", True, True, label="5V 전원", out=True,
            note="끄면 쿨링 팬·아날로그 수집·WS2812 가 함께 멈춘다",
        ),
        SimConfigItem("pwr.seq_delay_ms", "pwr", "u16", 500, 500,
                      minimum=0, maximum=5000, unit="ms", label="레일 기동 간격"),

        # ── 디지털 입력 (데이터시트 §5.7, 사용자 확정 2026-08-18) ───────
        #
        # 🔴 J18~J20 은 출력이 아니라 **입력**이다. 넷리스트 확인 결과
        #    커넥터 pin1 이 MCU 핀에 직결이고 사이에 부품이 없다 — 옵토
        #    커플러가 커넥터 반대편(외부)에 붙고, 보드는 그 신호를 읽는다.
        #    "켜라/꺼라" 가 성립하지 않으므로 `sol.j18`~`sol.j20` 을 아예
        #    카탈로그에서 없앤다. 지금 상태는 규격 §7.6 의 `din` 텔레메트리로
        #    온다(`tools/simulator/telemetry.build_din_record`).
        #
        # 🔴 남는 것은 디바운스 시간뿐이다. 옵토 접점이 흔들리는 정도는
        #    커넥터마다 물리는 외부 장치에 따라 달라질 수 있어, 펌웨어
        #    상수로 고정하지 않고 현장에서 조정할 수 있게 카탈로그에
        #    남긴다. `out` 이 아니다 — 켜고 끄는 출력이 아니라 필터 값이라
        #    TEST 이탈 때 되돌릴 "출력" 이 없다.
        SimConfigItem("sol.debounce_ms", "sol", "u16", 5, 5,
                      minimum=0, maximum=1000, unit="ms", label="디바운스",
                      note="이보다 짧게 흔들리는 신호는 상태 변화로 보지 않는다"),

        # ── GNSS/PPS 시간축 (J16, 데이터시트 §5.5, Phase 3) ─────────────
        #
        # 🔴 값·라벨·note·범위가 펌웨어(firmware/stage1/app/mk_cfgtable.c
        #    의 add_gnss())와 한 글자도 다르면 안 된다 — crosscheck_
        #    cfgtable.py 가 대조한다.
        SimConfigItem(
            "gnss.enabled", "gnss", "bool", False, False,
            label="GNSS 사용",
            note="J16 에 GNSS 모듈이 꽂혀 있을 때만 켠다 — 없으면 NMEA 가 "
                 "안 와서 시간 등급이 device_clock 에 머문다",
        ),
        # 🔴 note 를 짧게 둔다 — 펌웨어 쪽(mk_cfgtable.c 의 add_gnss())이
        #    mk_cfgwire_list 의 320바이트 줄 상한을 넘기지 않으려 note 를
        #    줄였다. 여기서 길게 두면 "있는지 없는지"는 같아도 실제로
        #    읽을 문구가 갈린다.
        SimConfigItem(
            "gnss.baud", "gnss", "enum", 115200, 115200,
            choices=GNSS_BAUD_CHOICES, unit="bps", label="GNSS 통신속도",
            note="UM981 기본값 확인 필요",
        ),
        # 🔴 기본이 꺼짐이다(규격 §7.7). RTK 모듈은 초당 수십 줄을 낼 수
        #    있고 그것을 전부 텔레메트리에 실으면 ain·i2c 가 쓸 대역을
        #    잠식한다 — 진단할 때만 켠다. 값·라벨·note 가 펌웨어
        #    (firmware/stage1/app/mk_cfgtable.c 의 add_gnss())와 한 글자도
        #    다르면 안 된다 — crosscheck_cfgtable.py 가 대조한다.
        SimConfigItem(
            "gnss.echo", "gnss", "bool", False, False,
            label="GNSS 원시 문장 에코",
            note="받은 줄을 그대로 텔레메트리로 올린다 — 진단용, 대역을 먹는다",
        ),

        # ── LCD 화면 (J25, 데이터시트 §5.9) ────────────────────────────
        #
        # 🔴 기본이 꺼짐이다. 화면 한 장이 320 x 480 x 3 = 460,800 바이트라
        #    SPI2 16 MHz 에서 약 230 ms 를 쓴다 — 패널이 안 물린 보드에서
        #    그것을 밀면 수집이 그만큼 뒤로 밀린다.
        #
        # 🔴 값·라벨·note 가 펌웨어(firmware/stage1/app/mk_cfgtable.c 의
        #    add_lcd())와 한 글자도 다르면 안 된다 — crosscheck_cfgtable.py
        #    가 대조한다.
        SimConfigItem(
            "lcd.enabled", "lcd", "bool", False, False,
            label="화면 사용",
            note="J25 에 LCD 가 꽂혀 있을 때만 켠다 — 한 장 그리는 데 "
                 "230 ms 를 쓴다",
        ),
        # 🔴 갱신 주기. 사람이 읽는 화면이라 초당 2~4번이면 넘친다 —
        #    텔레메트리 주기(tx.period_ms)를 따라갈 이유가 없다. 값이 바뀐
        #    칸만 다시 그리므로, 화면이 조용하면 이 주기가 짧아도 전송이
        #    아예 없다.
        SimConfigItem(
            "lcd.period_ms", "lcd", "u16", 250, 250,
            minimum=50, maximum=10000, unit="ms", label="화면 갱신 주기",
            note="값이 바뀐 칸만 다시 그린다 — 짧게 잡아도 화면이 조용하면 "
                 "전송이 없다",
        ),

        # ── WS2812 LED 체인 (데이터시트 §5.8) ──────────────────────
        SimConfigItem(
            "led.count", "led", "u8", 0, 0, minimum=0, maximum=4,
            label="체인 LED 수", out=True,
            note="J21 부터 순서대로 채운다 — 중간이 비면 뒤쪽이 동작하지 않는다",
        ),
        SimConfigItem(
            "led.brightness", "led", "u8", 64, 64, minimum=0, maximum=255,
            label="밝기", out=True,
            note="5V 레일이 꺼져 있으면 LED 가 켜지지 않는다",
        ),
        # 🔴 out 이 아니다. 켜고 끄는 출력이 아니라 물린 스트립의 성질이다 —
        #    out 으로 두면 TEST 를 빠져나올 때 색이 저 혼자 뒤집힌다.
        SimConfigItem(
            "led.grb", "led", "bool", False, False,
            label="색 순서 GRB",
            note="빨강과 초록이 바뀌어 보이면 이 값을 뒤집는다 — 칩마다 다르다",
        ),
    ]

    # 🔴 색을 0xRRGGBB 한 덩이로 두지 않는다. 그러면 화면에 생짜 숫자가
    #    떠서 손으로 계산해야 한다 — 필드 마스크가 `698` 로 보이던 것과
    #    같은 문제다. R·G·B 세 항목으로 두면 각각 0~255 이고, 반복 구조라
    #    화면이 표로 접는다.
    for lamp in range(21, 25):                   # J21 ~ J24
        items += [
            SimConfigItem(f"led{lamp}.r", "led", "u8", 0, 0,
                          minimum=0, maximum=255, label=f"J{lamp} 빨강", out=True),
            SimConfigItem(f"led{lamp}.g", "led", "u8", 0, 0,
                          minimum=0, maximum=255, label=f"J{lamp} 초록", out=True),
            SimConfigItem(f"led{lamp}.b", "led", "u8", 0, 0,
                          minimum=0, maximum=255, label=f"J{lamp} 파랑", out=True),
        ]

    # ── I2C 센서 포트 (데이터시트 §5.4) ────────────────────────────
    #
    # 🔴 짝 커넥터는 **같은 버스**다. J10·J11 은 물리적으로 같은 I2C3 에
    #    병렬로 붙어 있어 같은 주소를 함께 쓰면 충돌한다. 그 사실을 포트마다
    #    실어 보낸다 — 화면이 경고할 유일한 근거다.
    _I2C_BUS = {10: "I2C3", 11: "I2C3", 12: "I2C5",
                13: "I2C5", 14: "I2C1", 15: "I2C1"}
    for port, bus in _I2C_BUS.items():
        items += [
            # 🔴 버스를 **열로** 준다. 포트마다 "J10·J11 은 같은 버스" 라고
            #    풀어 쓰면 표에 같은 문장이 여섯 번 반복돼 정작 값이 안
            #    읽힌다. 어느 포트가 한 버스인지는 열을 훑으면 보인다.
            SimConfigItem(f"i2c{port}.bus", "i2c", "str", bus, bus,
                          maximum=7, readonly=True, label=f"J{port} 버스",
                          note="같은 버스에 물린 두 포트는 주소가 겹치면 안 된다"),
            SimConfigItem(f"i2c{port}.enabled", "i2c", "bool", False, False,
                          label=f"J{port} 사용"),
            # 🔴 0 은 "미지정" 이다. 7비트 주소에서 실제로 쓸 수 있는 구간은
            #    0x08~0x77 이지만, 하한을 0x08 로 두면 기본값 0 이 범위 밖이
            #    되어 $CFG,RESET 이 스스로 어긋난 값으로 되돌린다.
            SimConfigItem(f"i2c{port}.addr", "i2c", "u8", 0, 0,
                          minimum=0, maximum=0x77, label=f"J{port} 주소",
                          note="0 = 미지정. 쓸 수 있는 7비트 주소는 0x08~0x77"),
            SimConfigItem(
                f"i2c{port}.kind", "i2c", "enum",
                I2C_KIND_NONE, I2C_KIND_NONE,
                choices=tuple(k for k, _l, _q in I2C_KINDS),
                choice_labels=tuple(l for _k, l, _q in I2C_KINDS),
                label=f"J{port} 종류",
                note="꽂은 센서 종류",
            ),
            SimConfigItem(f"i2c{port}.period_ms", "i2c", "u16", 200, 200,
                          minimum=10, maximum=60000, unit="ms",
                          label=f"J{port} 주기"),
        ]

    for ch in range(7):
        connector = ch + 3                       # AIN0 → J3
        items += [
            SimConfigItem(f"ain{ch}.enabled", "ain", "bool",
                          ch == 0, ch == 0, label=f"J{connector} 사용"),
            SimConfigItem(f"ain{ch}.period_ms", "ain", "u16", 100, 100,
                          minimum=10, maximum=60000, unit="ms",
                          label=f"J{connector} 수집 주기"),
            # 🔴 영점은 mA 값이므로 0~25 mA 밖은 물리적으로 뜻이 없다.
            #    이 앞단은 120 Ω 션트에 4~20 mA 루프다 — 25 mA 는 과전류
            #    여유까지 본 상한이고, 음수는 전류 방향이 반대라는 뜻이라
            #    이 회로에서는 나올 수 없다. 범위가 없으면 화면의 입력칸이
            #    아무 수나 받고, 잘못 넣으면 모든 측정이 조용히 어긋난다.
            SimConfigItem(f"ain{ch}.zero", "ain", "f32", 4.0, 4.0,
                          minimum=0.0, maximum=25.0,
                          unit="mA", label=f"J{connector} 영점"),
            SimConfigItem(f"ain{ch}.scale", "ain", "f32", 1.0, 1.0,
                          label=f"J{connector} 스케일"),
            SimConfigItem(f"ain{ch}.unit", "ain", "str", "", "",
                          maximum=7, label=f"J{connector} 단위"),
        ]

    return ConfigStore(items, path)
