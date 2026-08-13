"""Qt 위젯 층.

🔴 이 아래 파일들만 PyQt6 를 import 한다. `host/gui/` 의 나머지(theme,
   command_queue, last_known, field_budget, settings_form, widgets/*)는
   Qt 를 모른다.

   그 경계 덕분에 시각 언어·상태 판정·동시성 규약이 위젯보다 먼저 굳고,
   시험이 디스플레이 없이 돈다. 여기 있는 위젯들은 그 함수들을 **호출만**
   한다 — 판단을 여기서 하기 시작하면 그 순간부터 화면을 띄우지 않고는
   검증할 수 없게 된다.
"""
