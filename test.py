from datetime import datetime, timezone
from bot import parse_lineup

sample = """Oct 28 @ 5pm (meet @ nhans 3:50pm leave by 4)
L1: <@111> <@222>
L2: <@333> <@444>
D: <@555>
C: <@666>
lead: <@777>"""

print(parse_lineup(sample, datetime(2026, 10, 20, tzinfo=timezone.utc)))
print(parse_lineup("see you guys there <@111>", datetime.now(timezone.utc)))  # None