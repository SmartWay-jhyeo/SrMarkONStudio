"""시뮬레이터를 실제 시리얼 포트에 붙여 돌린다.

가상 시리얼 포트 쌍(Windows: com0com, Linux: socat)을 만든 뒤 한쪽을 이
서버에, 다른 쪽을 GUI 나 CLI 에 연결하면 보드 없이 전 구간을 시험할 수 있다.
"""

import time
from pathlib import Path

from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim


def run_simulator_server(port: str, baud: int = 115200,
                         state: Path | None = None) -> None:
    """시뮬레이터를 시리얼 포트에 붙이고 무한 루프로 돌린다."""
    import serial

    store = default_store(state)
    store.load()
    sim = DeviceSim(store)

    ser = serial.Serial(port, baud, timeout=0.05)
    buf = ""
    t0 = time.monotonic()
    print(f"시뮬레이터 시작: {port} @ {baud}")

    try:
        while True:
            now_ms = int((time.monotonic() - t0) * 1000)

            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf += chunk.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    for out in sim.feed(line + "\n"):
                        ser.write((out + "\r\n").encode("utf-8"))

            for out in sim.tick(now_ms):
                ser.write((out + "\r\n").encode("utf-8"))

            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\n시뮬레이터 종료")
    finally:
        ser.close()
