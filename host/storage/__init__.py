"""레코드를 파일에 쌓고 다시 꺼내 쓰는 층.

🔴 `host/gui/`를 import하지 않는다 — Jetson 무인 수집은 GUI 없이 돌아야
한다(계획서 §1). 이 층은 `host/service/collector.py`와 `tools/cli/`에서만
쓰인다.
"""
