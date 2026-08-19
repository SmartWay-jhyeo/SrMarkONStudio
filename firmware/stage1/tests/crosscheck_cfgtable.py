"""보드에 실리는 실제 설정표와 시뮬레이터의 카탈로그를 대조한다.

🔴 stage 1 에서 가장 값진 대조다. 사용자가 원하는 것은 "USB 를 꽂았을 때만
   GUI 로 설정하고 제어하는 것" 이고, 그 GUI 는 **시뮬레이터를 상대로**
   개발된다. 화면은 카탈로그만 보고 그려지므로, 두 카탈로그가 어긋나면
   GUI 는 시뮬레이터에서 멀쩡하다가 보드에서만 틀어진다.

   그런데 여태 이 대조가 없었다. crosscheck_cfg.py 는 test_cfgwire.c 의
   **6항목짜리 시험용 표**를 확인한다 — parse_catalog 가 C 의 출력을 제대로
   읽는지 보는 것이지, 실제 표를 보는 것이 아니다. 보드에 실리는
   mk_cfgtable.c 는 main.c 에만 엮여 있어서 아무도 보지 않았다.

무엇을 비교하나 — **바이트가 아니라 구조**다 (crosscheck_cfg.py 와 같은 이유).
호스트는 JSON 을 파싱해 ConfigSchema 를 만들고 화면은 그 구조만 본다.

  - 항목 키 집합
  - 항목마다: vtype · ro · min · max · unit · choices · 기본값
  - 필드 비트 집합과 비트마다: 이름 · 기본값

무엇을 비교하지 않나
  - `label` 과 `note` 의 문구. 사람이 읽는 글이고 한쪽을 고칠 이유가 늘 있다.
    다만 **있는지 없는지**는 본다 — 없으면 화면이 key 를 그대로 보여 준다.
  - `current`. 시뮬레이터는 저장된 값을 들고 있을 수 있다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# 🔴 콘솔 코드페이지에 기대지 않는다.
#
#    이 도구들은 한글과 `—` 를 찍는다. CP949 콘솔에서 돌면 글자가 깨지고,
#    환경에 따라서는 UnicodeEncodeError 로 죽는다. 그러면 논리는 멀쩡한데
#    시험이 실패한 것처럼 보인다 — 거짓 실패는 진짜 실패보다 나쁘다.
#    아무도 안 믿게 되기 때문이다.
#
#    부르는 쪽(run_tests.ps1, Makefile)에서 PYTHONUTF8 을 세우는 방법도
#    있지만, 그러면 손으로 직접 돌릴 때 다시 깨진다. 도구가 스스로 책임진다.
#    Codex 감사(2026-08-14 16:15)의 지적.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from host.core.config_schema import parse_catalog  # noqa: E402
from host.core.errors import ConfigError  # noqa: E402
from host.core.framing import build_command  # noqa: E402
from tools.simulator.capacity import check_capacity  # noqa: E402
from tools.simulator.config_store import default_store  # noqa: E402
from tools.simulator.device_sim import DeviceSim  # noqa: E402

#: 구조로서 반드시 같아야 하는 것들. 하나라도 어긋나면 화면이 달라진다.
#:   vtype    위젯 종류를 정한다 (bool → 체크박스, enum → 콤보)
#:   readonly 편집 가능 여부를 정한다
#:   minimum  스핀박스의 한계이자 입력 검증의 기준
#:   maximum  〃
#:   unit     값 뒤에 붙는 글자
#:   choices  콤보의 항목들
#:   default  $CFG,RESET 이 돌아갈 자리
#:   out      TEST 에서 저장 안 됨·모드 이탈 시 복귀 (규격 §6.4). 한쪽만
#:            표시하면 한 구현은 밸브를 되돌리고 다른 구현은 남긴다
COMPARED = ("vtype", "readonly", "out", "minimum", "maximum", "unit",
            "choices", "default")


def _find_binary() -> Path:
    for name in ("test_cfgtable.exe", "test_cfgtable"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def _firmware_catalog():
    lines = subprocess.run(
        [str(_find_binary()), "--catalog"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    return parse_catalog([ln for ln in lines if ln.strip()])


def _simulator_catalog():
    """시뮬레이터에게 실제로 $CFG,LIST 를 시켜 그 응답을 파싱한다.

    카탈로그를 만드는 코드 경로를 그대로 지나가야 의미가 있다. 설정
    저장소를 직접 들여다보면 device_sim 이 카탈로그를 만들며 저지르는
    실수는 이 대조를 통과한다.
    """
    # 🔴 체크섬을 손으로 적지 않는다. 한 번 그랬다가 CHECKSUM 오류가 났다.
    sim = DeviceSim(default_store())
    lines = sim.feed(build_command("CFG", "LIST"))
    body = [ln for ln in lines if not ln.startswith("$")]
    if not body:
        raise SystemExit(f"시뮬레이터가 카탈로그를 내지 않았다: {lines}")
    return parse_catalog(body)


def _same(a, b) -> bool:
    """실수는 값으로 본다 — 표기가 4.0 이든 4.0000 이든 같은 값이다."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= 1e-6
    return a == b


def main() -> int:
    fw = _firmware_catalog()
    sim = _simulator_catalog()
    problems: list[str] = []

    only_fw = sorted(set(fw.items) - set(sim.items))
    only_sim = sorted(set(sim.items) - set(fw.items))
    if only_fw or only_sim:
        problems.append(f"  항목 목록이 다르다\n"
                        f"    펌웨어에만: {only_fw or '없음'}\n"
                        f"    시뮬레이터에만: {only_sim or '없음'}")

    for key in sorted(set(fw.items) & set(sim.items)):
        a, b = fw.items[key], sim.items[key]
        diffs = [attr for attr in COMPARED
                 if not _same(getattr(a, attr), getattr(b, attr))]
        if diffs:
            detail = "\n".join(
                f"      {attr}: 펌웨어={getattr(a, attr)!r} "
                f"시뮬레이터={getattr(b, attr)!r}" for attr in diffs)
            problems.append(f"  {key} 가 다르다\n{detail}")
        # 🔴 문구는 비교하지 않지만 **있는지**는 본다. 없으면 화면이
        #    `ain3.span` 같은 키를 그대로 보여 준다.
        for side, item in (("펌웨어", a), ("시뮬레이터", b)):
            if not item.label:
                problems.append(f"  {key}: {side} 쪽에 라벨이 없다")
        # 인터록·읽기전용은 사유가 따라와야 한다 (규격 §7.3).
        if a.readonly and not (a.note and b.note):
            problems.append(f"  {key}: ro 인데 사유(note)가 한쪽에 없다 — "
                            f"펌웨어={a.note!r} 시뮬레이터={b.note!r}")

    # 🔴 펌웨어의 기본값 자체가 보드의 용량 규칙을 지키는지 본다.
    #
    #    처음 이 대조를 붙였을 때 펌웨어는 7채널을 전부 켠 채로 출발했고,
    #    그 조합은 요구 70 SPS > 가용 45.3 SPS 로 보드 자신의 검사에
    #    걸렸다 — $CFG,RESET 이 스스로 거부하는 설정으로 되돌리는 셈이었다.
    #    용량 계산은 아직 Python 에만 있으므로 그 대조가 여기서 이뤄진다.
    store = default_store()
    for key, item in fw.items.items():
        if key in store.items:
            store.items[key].current = item.default
    try:
        check_capacity(store)
    except ConfigError as exc:
        problems.append(f"  펌웨어 기본값이 용량 검사에 걸린다 — {exc}")

    only_fw_bits = sorted(set(fw.fields) - set(sim.fields))
    only_sim_bits = sorted(set(sim.fields) - set(fw.fields))
    if only_fw_bits or only_sim_bits:
        problems.append(f"  필드 비트가 다르다\n"
                        f"    펌웨어에만: {only_fw_bits or '없음'}\n"
                        f"    시뮬레이터에만: {only_sim_bits or '없음'}")
    for bit in sorted(set(fw.fields) & set(sim.fields)):
        a, b = fw.fields[bit], sim.fields[bit]
        # 🔴 [개정, 2026-08-19] records 도 대조한다 — tx.fields 를 ain·i2c·
        #    din 셋으로 나누며 "이 비트가 어느 레코드에 해당하는가" 가
        #    새로 생긴 계약이다. 여기가 어긋나면 GUI 가 마스크 카드를
        #    엉뚱하게 나눠 그린다(시뮬레이터에서는 멀쩡한 채로).
        a_records = tuple(sorted(a.records))
        b_records = tuple(sorted(b.records))
        if (a.name, a.default, a_records) != (b.name, b.default, b_records):
            problems.append(f"  비트 {bit} 가 다르다\n"
                            f"      펌웨어={a.name!r}/{a.default}/{a_records} "
                            f"시뮬레이터={b.name!r}/{b.default}/{b_records}")

    if problems:
        print("펌웨어 표와 시뮬레이터 카탈로그가 어긋난다:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print(f"\n(항목 펌웨어 {len(fw.items)} · 시뮬레이터 {len(sim.items)})",
              file=sys.stderr)
        return 1

    for key in sorted(fw.items):
        it = fw.items[key]
        print(f"  ok   {key:18} {it.vtype:5} ro={str(it.readonly):5} "
              f"choices={len(it.choices)}")
    print(f"  ok   필드 비트 {len(fw.fields)}개")
    print(f"\nMATCH ({len(fw.items)} items, {len(fw.fields)} fields)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
