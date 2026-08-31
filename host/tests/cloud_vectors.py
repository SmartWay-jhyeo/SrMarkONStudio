"""젯슨이 실제로 받은 Cloud 형식 NDJSON — 파서·어댑터의 고정 시험 벡터.

🔴 지어낸 표본이 아니다. 2026-08-29 사용자가 실장비 젯슨 수신분을 캡쳐해
   전달한 8종 그대로다(temp_air 원문의 "device_cloㅉck" 오타 하나만 —
   붙여넣기 사고가 명백해서 — device_clock 으로 바로잡았다). 이 줄들이
   통과하지 못하면 파서가 틀린 것이지 벡터가 틀린 것이 아니다.

🔴 seq 가 없다 — 캡쳐 시점의 펌웨어에는 seq 가 없었고, 지금은 tx.seq
   체크박스(기본 켜짐)를 끈 보드가 정확히 이 모양을 낸다. seq 달린
   변형은 WITH_SEQ 로 따로 둔다.
"""

FLOW1 = ('{"schema_ver":1,"device_id":"1","t":968563,"type":"flow1",'
         '"time_source":"device_clock","lpm":1.6,"ma":4.260,"raw":857683}')
FLOW2 = ('{"schema_ver":1,"device_id":"1","t":968568,"type":"flow2",'
         '"time_source":"device_clock","lpm":1.1,"ma":4.284,"raw":862478}')
PRESSURE = ('{"schema_ver":1,"device_id":"1","t":968566,"type":"pressure_paint",'
            '"time_source":"device_clock","bar":0.1,"ma":3.996,"raw":804565}')
TEMP_AIR = ('{"schema_ver":1,"device_id":"1","t":969188,"type":"temp_air",'
            '"time_source":"device_clock","degc":27.0}')
HUMIDITY = ('{"schema_ver":1,"device_id":"1","t":969188,"type":"humidity",'
            '"time_source":"device_clock","pct":34.0}')
GNSS = ('{"schema_ver":1,"device_id":"1","t":1787737031800,"type":"gnss",'
        '"time_source":"gnss","lat_e8":3740668094,"lon_e8":12672278794,'
        '"fix":1,"sat":21,"hdop_x100":120,"cs":"44","alt":32.238,"valve":1}')
IMU = ('{"schema_ver":1,"device_id":"1","t":1787737031664,"type":"imu",'
       '"time_source":"gnss","ax":0.034,"ay":0.992,"az":-0.019,'
       '"gx":-0.015,"gy":0.107,"gz":0.031}')
VALVE = ('{"schema_ver":1,"device_id":"1","t":533008,"type":"valve",'
         '"time_source":"device_clock","state":1}')

ALL = (FLOW1, FLOW2, PRESSURE, TEMP_AIR, HUMIDITY, GNSS, IMU, VALVE)

#: tx.seq 켜짐(기본) 상태의 새 펌웨어가 내는 모양 — seq 가 schema_ver
#: 바로 뒤에 붙는다(펌웨어 begin_record 순서).
WITH_SEQ = tuple(
    line.replace('"schema_ver":1,', '"schema_ver":1,"seq":%d,' % i, 1)
    for i, line in enumerate(ALL)
)
