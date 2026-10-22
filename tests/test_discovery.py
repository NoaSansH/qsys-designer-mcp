"""Parsing Q-SYS device announcements."""
from qsys_mcp import discovery

ANNOUNCE = (b"<QDP><device><name>Room-Core</name><type>lcqln</type><part_number>Core 8 Flex</part_number>"
            b"<lan_a_ip>192.168.10.20</lan_a_ip><lan_b_ip></lan_b_ip><is_primary>true</is_primary>"
            b"<design_name>Room v2</design_name></device></QDP>")


def test_parse_an_announcement():
    d = discovery.parse(ANNOUNCE)
    assert d == {"name": "Room-Core", "model": "Core 8 Flex", "ips": ["192.168.10.20"], "type": "lcqln",
                 "is_primary": "true", "design_name": "Room v2"}
    assert discovery.cores([d]) == [d]
    assert discovery.parse(b"<QDP><query/></QDP>") is None
    assert discovery.cores([{"name": "TSC", "model": "TSC-80-G2", "type": "touchpanel", "ips": []}]) == []
