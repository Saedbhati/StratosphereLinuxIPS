from tests.module_factory import ModuleFactory
import pytest
from slips_files.core.evidence_structure.evidence import validate_timestamp
from slips_files.core.evidence_structure.evidence import (
    Attacker,
    Direction,
    Evidence,
    EvidenceType,
    IDEACategory,
    IoCType,
    ProfileID,
    Proto,
    Tag,
    ThreatLevel,
    TimeWindow,
    Anomaly,
    Recon,
    Attempt,
    evidence_to_dict,
)


@pytest.mark.parametrize(
    "evidence_type, description, attacker_value, threat_level, "
    "category, profile_ip, timewindow_number, uid, timestamp, "
    "victim_value, proto_value, port, source_target_tag, id, "
    "conn_count, confidence",
    [  # Testcase1: complete evidence data
        (
            EvidenceType.ARP_SCAN,
            "ARP scan detected",
            "192.168.1.1",
            ThreatLevel.LOW,
            IDEACategory.ANOMALY_TRAFFIC,
            "192.168.1.2",
            1,
            ["flow1", "flow2"],
            "2023/10/26 10:10:10.000000+0000",
            "192.168.1.3",
            "tcp",
            80,
            Tag.RECON,
            "d4afbe1a-1cb9-4db4-9fac-74f2da6f5f34",
            10,
            0.8,
        ),
        # Testcase2: different evidence type and threat level
        (
            EvidenceType.DNS_ARPA_SCAN,
            "DNS ARPA scan detected",
            "10.0.0.1",
            ThreatLevel.MEDIUM,
            IDEACategory.RECON_SCANNING,
            "10.0.0.2",
            2,
            ["flow3", "flow4", "flow5"],
            "2023/10/27 11:11:11.000000+0000",
            "10.0.0.3",
            "udp",
            53,
            Tag.RECON,
            "d243119b-2aae-4d7a-8ea1-edf3c6e72f4a",
            5,
            0.5,
        ),
    ],
)
def test_evidence_post_init(
    evidence_type,
    description,
    attacker_value,
    threat_level,
    category,
    victim_value,
    profile_ip,
    timewindow_number,
    uid,
    timestamp,
    proto_value,
    port,
    source_target_tag,
    id,
    conn_count,
    confidence,
):
    attacker = ModuleFactory().create_attacker_obj(
        value=attacker_value, direction=Direction.SRC, attacker_type=IoCType.IP
    )
    victim = ModuleFactory().create_victim_obj(
        direction=Direction.DST, victim_type=IoCType.IP, value=victim_value
    )
    profile = ModuleFactory().create_profileid_obj(ip=profile_ip)
    timewindow = ModuleFactory().create_timewindow_obj(
        number=timewindow_number
    )
    proto = ModuleFactory().create_proto_obj()[proto_value.upper()]
    evidence = ModuleFactory().create_evidence_obj(
        evidence_type,
        description,
        attacker,
        threat_level,
        category,
        victim,
        profile,
        timewindow,
        uid,
        timestamp,
        proto,
        port,
        source_target_tag,
        id,
        conn_count,
        confidence,
    )
    assert evidence.evidence_type == evidence_type
    assert evidence.description == description
    assert evidence.attacker == attacker
    assert evidence.threat_level == threat_level
    assert evidence.category == category
    assert evidence.victim == victim
    assert evidence.profile == profile
    assert evidence.timewindow == timewindow
    assert set(evidence.uid) == set(uid)
    assert evidence.timestamp == timestamp
    assert evidence.proto == proto
    assert evidence.port == port
    assert evidence.source_target_tag == source_target_tag
    assert evidence.id == id
    assert evidence.conn_count == conn_count
    assert evidence.confidence == confidence


def test_evidence_post_init_invalid_uid():
    with pytest.raises(ValueError, match="uid must be a " "list of strings"):
        ModuleFactory().create_evidence_obj(
            evidence_type=EvidenceType.ARP_SCAN,
            description="ARP scan detected",
            attacker=ModuleFactory().create_attacker_obj(
                direction=Direction.SRC,
                attacker_type=IoCType.IP,
                value="192.168.1.1",
            ),
            threat_level=ThreatLevel.LOW,
            category=IDEACategory.ANOMALY_TRAFFIC,
            profile=ModuleFactory().create_profileid_obj(ip="192.168.1.2"),
            timewindow=ModuleFactory().create_timewindow_obj(number=1),
            uid=[1, 2, 3],
            timestamp="2023/10/26 10:10:10.000000+0000",
            victim=ModuleFactory().create_victim_obj(
                direction=Direction.DST,
                victim_type=IoCType.IP,
                value="192.168.1.3",
            ),
            proto=Proto.TCP,
            port=80,
            id=232,
            source_target_tag=Tag.RECON,
            conn_count=10,
            confidence=0.8,
        )


@pytest.mark.parametrize(
    "evidence_type, description, attacker_value, "
    "threat_level, category, profile_ip, timewindow_number, "
    "uid, timestamp, victim_value, proto_value, port, "
    "source_target_tag, id, conn_count, confidence",
    [
        (
            # Testcase1 :basic_arp_scan_evidence
            EvidenceType.ARP_SCAN,
            "ARP scan detected",
            "192.168.1.1",
            ThreatLevel.LOW,
            IDEACategory.ANOMALY_TRAFFIC,
            "192.168.1.2",
            1,
            ["flow1", "flow2"],
            "2023/10/26 10:10:10.000000+0000",
            "192.168.1.3",
            "tcp",
            80,
            Tag.RECON,
            "d243119b-2aae-4d7a-8ea1-edf3c6e72f4a",
            10,
            0.8,
        ),
        (
            # Testcase2 :dns_arpa_scan_evidence
            EvidenceType.DNS_ARPA_SCAN,
            "DNS ARPA scan detected",
            "10.0.0.1",
            ThreatLevel.MEDIUM,
            IDEACategory.RECON_SCANNING,
            "10.0.0.2",
            2,
            ["flow3", "flow4", "flow5"],
            "2023/10/27 11:11:11.000000+0000",
            "10.0.0.3",
            "udp",
            53,
            Tag.RECON,
            "d243119b-2aae-4d7a-8ea1-e4f3c6e72f4a",
            5,
            0.5,
        ),
        (
            # Testcase3 :evidence_with_max_values
            EvidenceType.MALICIOUS_JA3,
            "Malicious JA3 fingerprint detected",
            "172.16.0.1",
            ThreatLevel.CRITICAL,
            IDEACategory.INTRUSION_BOTNET,
            "172.16.0.2",
            100,
            ["flow6", "flow7", "flow8", "flow9", "flow10"],
            "2023/10/28 12:12:12.000000+0000",
            "172.16.0.3",
            "icmp",
            0,
            Tag.MALWARE,
            "d243119b-2aae-4d7a-8ea1-eef3c6e72f4a",
            1000,
            1.0,
        ),
    ],
)
def test_evidence_to_dict(
    evidence_type,
    description,
    attacker_value,
    threat_level,
    category,
    profile_ip,
    timewindow_number,
    uid,
    timestamp,
    victim_value,
    proto_value,
    port,
    source_target_tag,
    id,
    conn_count,
    confidence,
):
    attacker = ModuleFactory().create_attacker_obj(
        value=attacker_value, direction=Direction.SRC, attacker_type=IoCType.IP
    )
    victim = ModuleFactory().create_victim_obj(
        direction=Direction.DST, victim_type=IoCType.IP, value=victim_value
    )
    profile = ModuleFactory().create_profileid_obj(ip=profile_ip)
    timewindow = ModuleFactory().create_timewindow_obj(
        number=timewindow_number
    )
    proto = (ModuleFactory().create_proto_obj())[proto_value.upper()]

    evidence = Evidence(
        evidence_type=evidence_type,
        description=description,
        attacker=attacker,
        threat_level=threat_level,
        category=category,
        victim=victim,
        profile=profile,
        timewindow=timewindow,
        uid=uid,
        timestamp=timestamp,
        proto=proto,
        port=port,
        source_target_tag=source_target_tag,
        id=id,
        conn_count=conn_count,
        confidence=confidence,
    )

    evidence_dict = evidence_to_dict(evidence)

    assert isinstance(evidence_dict, dict)
    assert evidence_dict["evidence_type"] == evidence_type.name
    assert evidence_dict["description"] == description
    assert evidence_dict["attacker"]["direction"] == Direction.SRC.name
    assert evidence_dict["attacker"]["attacker_type"] == IoCType.IP.name
    assert evidence_dict["attacker"]["value"] == attacker_value
    assert evidence_dict["threat_level"] == threat_level.name
    assert evidence_dict["category"] == category.name
    assert evidence_dict["victim"]["direction"] == Direction.DST.name
    assert evidence_dict["victim"]["victim_type"] == IoCType.IP.name
    assert evidence_dict["victim"]["value"] == victim_value
    assert evidence_dict["profile"]["ip"] == profile_ip
    assert evidence_dict["timewindow"]["number"] == timewindow_number
    assert set(evidence_dict["uid"]) == set(uid)
    assert evidence_dict["timestamp"] == timestamp
    assert evidence_dict["proto"] == proto.name
    assert evidence_dict["port"] == port
    assert evidence_dict["source_target_tag"] == source_target_tag.name
    assert evidence_dict["id"] == id
    assert evidence_dict["conn_count"] == conn_count
    assert evidence_dict["confidence"] == confidence


def test_validate_timestamp():
    valid_timestamp = "2023/10/26 10:10:10.000000+0000"
    assert validate_timestamp(valid_timestamp) == valid_timestamp


@pytest.mark.parametrize(
    "timestamp",
    [  # Testcase1: Wrong format
        "2023-10-26 10:10:10",
        # Testcase2: Invalid hour
        "2023/10/26 25:10:10.000000+0000",
        # Testcase3: Invalid month
        "2023/13/26 10:10:10.000000+0000",
        # Testcase4: Invalid day
        "2023/10/32 10:10:10.000000+0000",
        # Testcase5: Completely invalid
        "not a timestamp",
    ],
)
def test_validate_timestamp_invalid(timestamp):
    with pytest.raises(ValueError, match="Invalid timestamp format"):
        validate_timestamp(timestamp)


def test_profile_id_setattr():
    profile = ProfileID(ip="192.168.1.1")
    assert profile.ip == "192.168.1.1"


def test_profile_id_repr():
    profile = ProfileID(ip="192.168.1.1")
    assert repr(profile) == "profile_192.168.1.1"


def test_attacker_post_init():
    attacker = Attacker(Direction.SRC, IoCType.IP, "192.168.1.1")
    assert attacker.profile.ip == "192.168.1.1"


def test_timewindow_post_init():
    timewindow = TimeWindow(number=1)
    assert timewindow.number == 1


def test_timewindow_repr():
    timewindow = TimeWindow(number=5)
    assert repr(timewindow) == "timewindow5"


@pytest.mark.parametrize(
    "threat_level, expected_value, expected_str",
    [
        (ThreatLevel.INFO, 0, "info"),
        (ThreatLevel.LOW, 0.2, "low"),
        (ThreatLevel.MEDIUM, 0.5, "medium"),
        (ThreatLevel.HIGH, 0.8, "high"),
        (ThreatLevel.CRITICAL, 1, "critical"),
    ],
)
def test_threat_level(threat_level, expected_value, expected_str):
    assert threat_level.value == expected_value
    assert str(threat_level) == expected_str


@pytest.mark.parametrize(
    "anomaly_type, expected_value",
    [
        (Anomaly.TRAFFIC, "Anomaly.Traffic"),
        (Anomaly.FILE, "Anomaly.File"),
        (Anomaly.CONNECTION, "Anomaly.Connection"),
        (Anomaly.BEHAVIOUR, "Anomaly.Behaviour"),
    ],
)
def test_anomaly(anomaly_type, expected_value):
    assert anomaly_type.value == expected_value


@pytest.mark.parametrize(
    "recon_type, expected_value",
    [
        (Recon.RECON, "Recon"),
        (Recon.SCANNING, "Recon.Scanning"),
    ],
)
def test_recon(recon_type, expected_value):
    assert recon_type.value == expected_value


def test_attempt():
    assert Attempt.LOGIN.value == "Attempt.Login"


@pytest.mark.parametrize(
    "tag_enum, expected_value",
    [
        (Tag.SUSPICIOUS_USER_AGENT, "SuspiciousUserAgent"),
        (Tag.BLACKLISTED_IP, "BlacklistedIP"),
        (Tag.CC, "CC"),
    ],
)
def test_tag(tag_enum, expected_value):
    assert tag_enum.value == expected_value


@pytest.mark.parametrize(
    "proto_member, expected_value",
    [
        (Proto.TCP, "tcp"),
        (Proto.UDP, "udp"),
        (Proto.ICMP, "icmp"),
    ],
)
def test_proto(proto_member, expected_value):
    assert proto_member.value == expected_value


@pytest.mark.parametrize(
    "idea_category, expected_value",
    [
        (IDEACategory.ANOMALY_TRAFFIC, "Anomaly.Traffic"),
        (IDEACategory.RECON_SCANNING, "Recon.Scanning"),
        (IDEACategory.INTRUSION_BOTNET, "Intrusion.Botnet"),
    ],
)
def test_idea_category(idea_category, expected_value):
    assert idea_category.value == expected_value
    assert len(IDEACategory) > 0
