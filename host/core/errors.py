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
    """규격 §5 의 거부 사유. 이 7개가 전부다."""

    RANGE = "RANGE"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    READONLY = "READONLY"
    INTERLOCK = "INTERLOCK"
    MODE = "MODE"
    CHECKSUM = "CHECKSUM"
    BUSY = "BUSY"
    #: 값은 범위 안이지만 조합이 물리적으로 달성 불가 (설계 §6.4)
    CAPACITY = "CAPACITY"

    ALL = (RANGE, UNKNOWN_KEY, READONLY, INTERLOCK, MODE, CHECKSUM, BUSY,
           CAPACITY)
