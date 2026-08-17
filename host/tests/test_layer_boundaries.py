"""계층 경계를 한 곳에서 강제한다.

🔴 **왜 이 파일이 생겼나**

   `CLAUDE.md §0` 은 `screen` · `theme` · `command_queue` · `widgets/*` 가
   Qt 를 import 하지 않는다고 선언한다. 그런데 그것을 지키는 시험은 네
   모듈에만, 그것도 파일마다 손으로 복사한 형태로 있었다 — 정작 이 층이
   존재하는 이유인 `screen.py` 와, CLAUDE.md 가 이름까지 댄 `theme.py` 에는
   **없었다.**

   펌웨어 쪽은 이미 제대로 하고 있다. `test_firmware_safety.py` 의
   `test_app_layer_does_not_include_hal` 은 `app/*.[ch]` 를 전부 훑는다.
   여기서도 같은 방식으로 간다 — 목록을 손으로 적지 않고 **디렉터리를
   훑고**, 예외만 이름으로 둔다. 새 모듈을 만들면 자동으로 걸린다.

🔴 **문자열이 아니라 AST 로 본다**

   예전 시험은 `assert "PyQt" not in inspect.getsource(mod)` 였다. 그러면
   *"이 파일은 PyQt6 를 import 하지 않는다"* 라는 **주석 한 줄이 시험을
   깨뜨린다.** 규칙을 설명하는 문장을 지워야 규칙 검사가 통과하는 꼴이다.
   (펌웨어 쪽은 `_strip_comments` 로 이 함정을 이미 피해 갔다.)

   import 문은 문법이므로 AST 로 보면 주석·문서화 문자열이 섞일 여지가 없다.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Qt 를 써도 되는 곳. 그 밖은 전부 Qt 를 모른다.
#:
#: 🔴 `qt/` 는 그리는 층이고, `app.py` 는 배치와 배선이다. 둘 다 화면을
#:    띄우지 않으면 시험할 수 없는 코드라 경계 안쪽에 둔다.
QT_ALLOWED = ("host/gui/qt", "host/gui/app.py")

#: Qt 가 없어야 하는 층. 디렉터리를 훑으므로 새 파일이 저절로 포함된다.
QT_FREE_ROOTS = ("host/core", "host/service", "host/gui")


def _qt_free_modules() -> list[Path]:
    out: list[Path] = []
    for root in QT_FREE_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel.endswith("__init__.py"):
                continue
            if any(rel.startswith(a) for a in QT_ALLOWED):
                continue
            out.append(path)
    return out


def _imported_roots(path: Path) -> set[str]:
    """이 파일이 import 하는 최상위 패키지 이름들."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` 는 module 이 None 이다.
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_there_is_something_to_check():
    """🔴 훑어서 아무것도 안 나오면 아래 시험들이 조용히 통과한다.

    경로가 바뀌거나 오타가 나면 정확히 그렇게 된다 — 시험은 초록인데
    아무것도 안 보고 있는 상태다.
    """
    found = _qt_free_modules()
    assert len(found) >= 12, f"훑은 것이 너무 적다: {[p.name for p in found]}"
    names = {p.name for p in found}
    # CLAUDE.md §0 이 이름을 댄 것들이 실제로 들어왔는지 본다.
    for must in ("screen.py", "theme.py", "command_queue.py",
                 "loop_gauge.py", "status_chip.py"):
        assert must in names, f"{must} 가 검사 대상에서 빠졌다"


@pytest.mark.parametrize("path", _qt_free_modules(),
                         ids=lambda p: p.relative_to(REPO).as_posix())
def test_layer_does_not_import_qt(path: Path):
    """🔴 이 경계가 있어야 화면 로직을 디스플레이 없이 시험할 수 있다.

    한 번 깨지면 되돌리기 어렵다 — Qt 타입이 함수 서명에 새어 나오기
    시작하고, 그때부터는 그 모듈을 시험하려면 QApplication 이 필요해진다.
    """
    assert "PyQt6" not in _imported_roots(path), (
        f"{path.name} 이 PyQt6 를 import 한다 — 이 층은 Qt 를 모른다"
    )


@pytest.mark.parametrize("path", _qt_free_modules(),
                         ids=lambda p: p.relative_to(REPO).as_posix())
def test_layer_does_not_import_the_qt_package(path: Path):
    """`host.gui.qt` 를 우회로 끌어와도 같은 문제다.

    Qt 위젯을 직접 import 하지 않아도 `host.gui.qt.*` 를 부르면 그 모듈이
    PyQt6 를 끌고 들어온다. 결과는 같다 — 디스플레이 없이 못 돈다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("host.gui.qt"), (
                f"{path.name} 이 {node.module} 를 import 한다"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("host.gui.qt"), (
                    f"{path.name} 이 {alias.name} 를 import 한다"
                )
