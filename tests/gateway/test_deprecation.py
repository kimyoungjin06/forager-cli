#!/usr/bin/env python3
"""Deprecated surface inventory regressions."""

from __future__ import annotations

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_deprecation as deprecation


def test_deprecated_surface_inventory_includes_current_retired_surfaces() -> None:
    rows = deprecation.list_deprecated_surfaces()
    codes = {row["code"] for row in rows}

    assert "deprecated_surface.mother_orch" in codes
    assert "deprecated_surface.swarm" in codes
    assert "deprecated_surface.orch_map" in codes
    assert "deprecated_surface.monitor_alias" in codes
    assert "deprecated_surface.lifecycle_alias" in codes
    assert "deprecated_surface.followup_alias" in codes
    assert "deprecated_surface.offdesk_alias" in codes
    assert "deprecated_surface.gc_alias" in codes


def test_deprecated_surface_inventory_exposes_canonical_replacements() -> None:
    rows = {row["code"]: row for row in deprecation.list_deprecated_surfaces()}

    assert rows["deprecated_surface.orch_map"]["slash_replacement"] == "/map"
    assert rows["deprecated_surface.orch_map"]["cli_replacement"] == "aoe orch list"
    assert rows["deprecated_surface.lifecycle_alias"]["slash_replacement"] == "/task"
    assert rows["deprecated_surface.followup_alias"]["cli_replacement"] == "aoe followup"
    assert rows["deprecated_surface.gc_alias"]["slash_replacement"] == "/gc"
