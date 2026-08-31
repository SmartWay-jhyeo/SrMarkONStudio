"""보드 파라미터의 PC 사본 (HANDOFF_0831 결정 3).

🔴 지금까지 이 사본은 손 관리였다 — tools/restore_board_config.py 의 PLAN
   리스트에 "실물 카탈로그 덤프에서 옮겼다"고 적으며 하드코딩했고, 현장에서
   설정이 바뀌면(2026-08-31 온습도 J13→J12) 스크립트가 낡아 같은 사고를
   재생산할 뻔했다. 이제 GUI 가 접속할 때와 SET 이 수락될 때마다 파일로
   남긴다. 쓰임 셋:

     1. 굽기 후 복원 — restore_board_config.py 가 PLAN 대신 이 파일을 읽는다
     2. 역매핑·중복검사의 소스 — 보드가 안 붙어 있어도 로그를 해석할 수 있다
     3. 백업 — 플래시가 날아가도 마지막 상태가 PC 에 남는다

   기본 경로는 data/ 아래다 — 수집 데이터와 같은 자리, git 추적 밖.
"""

import json
import time
from pathlib import Path

from host.core.config_schema import ConfigSchema

#: 기본 저장 위치 — 저장소 루트의 data/ (수집 데이터와 같은 자리,
#: .gitignore 대상). 🔴 상대경로로 두면 GUI 를 어디서 띄웠는지에 따라
#: 사본이 흩어진다 — 이 파일 위치에서 루트를 계산해 고정한다.
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "board_config.json"


def wire_value(current: object) -> str:
    """현재값을 $CFG,SET 이 받는 문자열로. bool 은 펌웨어 parse_bool 의
    어휘(true/false)로 — str(True)="True" 는 보드가 거절한다."""
    if isinstance(current, bool):
        return "true" if current else "false"
    return "" if current is None else str(current)


def items_from_schema(schema: ConfigSchema) -> dict[str, str]:
    """복원 가능한 항목만 — 읽기 전용은 SET 이 거절하므로 뺀다.
    `link.baud` 도 뺀다: 값 하나로 끝나지 않고 §4.2 의 확인 절차를 타야
    해서, 복원 스크립트가 무심코 보내면 링크가 끊긴다."""
    return {
        key: wire_value(item.current)
        for key, item in schema.items.items()
        if not item.readonly and key != "link.baud"
    }


def save_snapshot(items: dict[str, str], path: Path = DEFAULT_PATH,
                  *, port: str = "") -> None:
    """실패해도 조용히 넘어가지 않는다 — 호출측이 잡아서 알린다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "port": port,
        "items": items,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def load_snapshot(path: Path = DEFAULT_PATH) -> dict[str, str]:
    """없으면 빈 dict — 호출측(restore)이 PLAN 폴백을 탄다."""
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", {})
    return {str(k): str(v) for k, v in items.items()}
