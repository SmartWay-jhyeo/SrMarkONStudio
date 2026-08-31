"""프로토콜 예외와 거부 사유 상수."""


class ProtocolError(Exception):
    """프로토콜 계층의 모든 예외의 기반."""


class ChecksumError(ProtocolError):
    """XOR 체크섬 불일치."""


class MalformedLineError(ProtocolError):
    """$ ... * 형태를 갖추지 못한 줄."""


class ConfigError(ProtocolError):
    """설정 요청 거부. reason 은 Reason 의 상수 중 하나."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}{': ' + detail if detail else ''}")
        self.reason = reason
        self.detail = detail


class Reason:
    """규격 §5 의 거부 사유. 이 9개가 전부다."""

    RANGE = "RANGE"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    READONLY = "READONLY"
    INTERLOCK = "INTERLOCK"
    MODE = "MODE"
    CHECKSUM = "CHECKSUM"
    BUSY = "BUSY"
    #: 값은 범위 안이지만 조합이 물리적으로 달성 불가 (설계 §6.4)
    CAPACITY = "CAPACITY"
    #: 명령은 알아들었으나 이 펌웨어가 아직 구현하지 않았다.
    #:
    #: 🔴 펌웨어를 단계로 나누어 올리기 때문에 필요하다. 1단계는 $HB·$ID 만
    #:    답하고 설정 저장소는 2단계에 들어온다. 그동안 GUI 는 아직 없는
    #:    명령을 정당하게 보낸다. 조용히 버리면 죽은 링크와 구분되지 않고,
    #:    사용자가 보는 것은 "눌렀는데 아무 일도 안 일어남" 이다.
    UNSUPPORTED = "UNSUPPORTED"

    ALL = (RANGE, UNKNOWN_KEY, READONLY, INTERLOCK, MODE, CHECKSUM, BUSY,
           CAPACITY, UNSUPPORTED)
