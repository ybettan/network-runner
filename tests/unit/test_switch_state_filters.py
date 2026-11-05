# Copyright (c) 2026 Yoni Bettan.
# Licensed under the Apache License, Version 2.0.

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "switch_state", ROOT / "plugins/filter/switch_state.py"
)
switch_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(switch_state)


def bridge(interfaces):
    return json.dumps(interfaces)


def reserved(internal=None, l3_begin=4000, l3_end=4064):
    value = {
        "internal": {"range": internal} if internal is not None else {},
        "l3-vni-vlan": {"begin": l3_begin, "end": l3_end},
    }
    return json.dumps(value)


def test_cumulus_vlans_expands_ranges_and_reports_used_and_reserved():
    result = switch_state.cumulus_vlans(
        bridge(
            [
                {
                    "ifname": "br_default",
                    "vlans": [{"vlan": 1, "flags": ["PVID", "Egress Untagged"]}],
                },
                {
                    "ifname": "swp1",
                    "vlans": [{"vlan": 100, "vlanEnd": 102}, {"vlan": 220}],
                },
            ]
        ),
        reserved(internal="3700-3702"),
    )

    assert result == {
        "interfaces": [
            {
                "name": "br_default",
                "vlan_ids": [1],
                "tagged_vlan_ids": [],
                "untagged_vlan_ids": [1],
                "pvid": 1,
            },
            {
                "name": "swp1",
                "vlan_ids": [100, 101, 102, 220],
                "tagged_vlan_ids": [100, 101, 102, 220],
                "untagged_vlan_ids": [],
                "pvid": None,
            },
        ],
        "used_vlan_ids": [1, 100, 101, 102, 220],
        "reserved_vlan_ids": list(range(3700, 3703)) + list(range(4000, 4065)),
    }


def test_cumulus_vlans_accepts_nvue_variants_and_a_trailing_newline():
    result = switch_state.cumulus_vlans(
        bridge([{"ifname": "swp1", "vlans": []}]),
        json.dumps(
            {
                "internal": {"range": "3725-3999\n"},
                "l3-vni-vlan": {
                    "begin": {"operational": 4000, "applied": 4001},
                    "end": {"operational": 4005, "applied": 4006},
                },
            }
        ),
    )

    assert result["reserved_vlan_ids"] == sorted(
        set(range(3725, 4000)) | {4000, 4001, 4002, 4003, 4004, 4005, 4006}
    )


def test_cumulus_vlans_uses_the_switchd_range_when_nvue_omits_it():
    result = switch_state.cumulus_vlans(
        bridge([{"ifname": "br_default", "vlans": [{"vlan": 1}]}]),
        reserved(),
        "resv_vlan_range = 3725-3999\n",
    )

    assert result["used_vlan_ids"] == [1]
    assert result["reserved_vlan_ids"] == list(range(3725, 4000)) + list(range(4000, 4065))


@pytest.mark.parametrize(
    ("bridge_output", "reserved_output", "switchd_output"),
    (
        ("not-json", reserved(internal="10-20"), None),
        (bridge([{"ifname": "swp1", "vlans": [{"vlan": "10"}]}]), reserved(internal="10-20"), None),
        (bridge([{"ifname": "swp1", "vlans": [{"vlan": 0}]}]), reserved(internal="10-20"), None),
        (bridge([{"ifname": "swp1", "vlans": []}]), reserved(internal="bad"), None),
        (bridge([{"ifname": "swp1", "vlans": []}]), reserved(), None),
        (bridge([]), reserved(internal="10-20"), None),
        (bridge([{"ifname": "", "vlans": []}]), reserved(internal="10-20"), None),
        (
            bridge([{"ifname": "swp1", "vlans": [{"vlan": 20, "vlanEnd": 10}]}]),
            reserved(internal="10-20"),
            None,
        ),
    ),
)
def test_cumulus_vlans_rejects_unverifiable_output(bridge_output, reserved_output, switchd_output):
    with pytest.raises(switch_state.SwitchStateError):
        switch_state.cumulus_vlans(bridge_output, reserved_output, switchd_output)


def test_cumulus_switchd_range_must_match_the_expected_key():
    with pytest.raises(switch_state.SwitchStateError, match="malformed"):
        switch_state.cumulus_vlans(
            bridge([{"ifname": "br_default", "vlans": []}]),
            reserved(),
            "reserved_vlan_range = 3725-3999",
        )


def test_cumulus_port_reports_an_access_port():
    result = switch_state.cumulus_port(
        bridge(
            [
                {
                    "ifname": "swp1",
                    "vlans": [{"vlan": 100, "flags": ["PVID", "Egress Untagged"]}],
                }
            ]
        ),
        "swp1",
    )

    assert result == {
        "name": "swp1",
        "present": True,
        "vlan_ids": [100],
        "tagged_vlan_ids": [],
        "untagged_vlan_ids": [100],
        "pvid": 100,
    }


def test_cumulus_port_reports_a_trunk_port():
    result = switch_state.cumulus_port(
        bridge([{"ifname": "swp1", "vlans": [{"vlan": 100}, {"vlan": 200}]}]),
        "swp1",
    )

    assert result["vlan_ids"] == [100, 200]
    assert result["tagged_vlan_ids"] == [100, 200]
    assert result["untagged_vlan_ids"] == []
    assert result["pvid"] is None


def test_cumulus_port_reports_an_unconfigured_port():
    result = switch_state.cumulus_port("[]", "swp1")

    assert result == {
        "name": "swp1",
        "present": False,
        "vlan_ids": [],
        "tagged_vlan_ids": [],
        "untagged_vlan_ids": [],
        "pvid": None,
    }


def test_cumulus_port_rejects_a_duplicate_port():
    duplicated = bridge(
        [
            {"ifname": "swp1", "vlans": []},
            {"ifname": "swp1", "vlans": []},
        ]
    )

    with pytest.raises(switch_state.SwitchStateError, match="malformed"):
        switch_state.cumulus_port(duplicated, "swp1")


def test_cumulus_port_rejects_ambiguous_pvids():
    ambiguous = bridge(
        [
            {
                "ifname": "swp1",
                "vlans": [
                    {"vlan": 100, "flags": ["PVID", "Egress Untagged"]},
                    {"vlan": 200, "flags": ["PVID", "Egress Untagged"]},
                ],
            }
        ]
    )

    with pytest.raises(switch_state.SwitchStateError, match="more than one PVID"):
        switch_state.cumulus_port(ambiguous, "swp1")
