# Copyright (c) 2026 Yoni Bettan.
# Licensed under the Apache License, Version 2.0.

import os
from pathlib import Path
import subprocess
import tempfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
TASKS = (
    "add_trunk_vlan.yaml",
    "conf_access_port.yaml",
    "conf_trunk_port.yaml",
    "create_vlan.yaml",
    "delete_port.yaml",
    "delete_trunk_vlan.yaml",
    "delete_vlan.yaml",
)
FLOCK_PREFIX = "flock /tmp/nvue.lock -c '\n"
NV_SCRIPT = """#!/bin/sh
printf '%s\n' "$*" >> "$NV_CALL_LOG"
if [ "$1" = config ] && [ "$2" = diff ]; then
    printf '%s' "$NV_DIFF_OUTPUT"
    exit "$NV_DIFF_STATUS"
fi
if [ "$1" = config ] && [ "$2" = apply ]; then
    exit "$NV_APPLY_STATUS"
fi
if [ "$1" = set ] || [ "$1" = unset ]; then
    exit "$NV_MUTATE_STATUS"
fi
exit 0
"""


def task_body(task_name):
    path = ROOT / "roles/switch/providers/cumulus" / task_name
    task = yaml.safe_load(path.read_text())[0]
    raw = task["raw"]
    assert raw.startswith(FLOCK_PREFIX)
    body = raw[len(FLOCK_PREFIX):].rstrip()
    assert body.endswith("'")
    return body[:-1].replace("{{ port_name }}", "swp4").replace(
        "{{ _vlan_id }}", "120"
    )


def run_task(task_name, diff_status=0, diff_output="", apply_status=0,
             mutate_status=0):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        nv = temp_path / "nv"
        nv.write_text(NV_SCRIPT)
        nv.chmod(0o755)
        call_log = temp_path / "calls"
        env = os.environ.copy()
        env.update({
            "PATH": temp_dir + os.pathsep + env.get("PATH", ""),
            "NV_CALL_LOG": str(call_log),
            "NV_DIFF_STATUS": str(diff_status),
            "NV_DIFF_OUTPUT": diff_output,
            "NV_APPLY_STATUS": str(apply_status),
            "NV_MUTATE_STATUS": str(mutate_status),
        })
        result = subprocess.run(
            ["/bin/sh", "-c", task_body(task_name)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        calls = call_log.read_text().splitlines() if call_log.exists() else []
        return result, calls


@pytest.mark.parametrize("task_name", TASKS)
def test_empty_diff_skips_apply(task_name):
    result, calls = run_task(task_name)

    assert result.returncode == 0
    assert "config diff --output commands" in calls
    assert "config apply -y" not in calls


@pytest.mark.parametrize("task_name", TASKS)
def test_nonempty_diff_with_nvue_status_one_is_applied(task_name):
    result, calls = run_task(
        task_name,
        diff_status=1,
        diff_output="nv set interface swp4 type swp\n",
    )

    assert result.returncode == 0
    assert "config apply -y" in calls


@pytest.mark.parametrize(
    "diff_status,diff_output",
    (
        (1, ""),
        (1, "Error: invalid NVUE option\n"),
        (2, "Error: NVUE failed\n"),
    ),
)
@pytest.mark.parametrize("task_name", TASKS)
def test_diff_errors_propagate_without_apply(task_name, diff_status,
                                            diff_output):
    result, calls = run_task(
        task_name,
        diff_status=diff_status,
        diff_output=diff_output,
    )

    assert result.returncode == diff_status
    assert "config apply -y" not in calls


@pytest.mark.parametrize("task_name", TASKS)
def test_apply_failure_propagates(task_name):
    result, calls = run_task(
        task_name,
        diff_status=1,
        diff_output="nv set interface swp4 type swp\n",
        apply_status=7,
    )

    assert result.returncode == 7
    assert "config apply -y" in calls


@pytest.mark.parametrize("task_name", TASKS)
def test_nvue_mutation_failure_stops_before_diff(task_name):
    result, calls = run_task(task_name, mutate_status=6)

    assert result.returncode == 6
    assert not any(call.startswith("config diff ") for call in calls)
