import sys


def test_python_version():
    # 🔴 젯슨(Ubuntu 22.04 기본 3.10)도 이 기준에 맞춘다 — 3.11 을 따로
    #    설치해서 쓴다 (사용자 결정 2026-08-21: 프로젝트 기준을 낮추지
    #    말고 배포 환경을 기준에 맞출 것).
    assert sys.version_info >= (3, 11)
