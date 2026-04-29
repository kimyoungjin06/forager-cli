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


def test_deprecated_surface_inventory_renders_operator_summary() -> None:
    text = deprecation.render_deprecated_surface_inventory()

    assert "deprecated surface inventory" in text
    assert "deprecated_surface.mother_orch" in text
    assert "slash_replacement: /map" in text
    assert "cli_replacement: aoe gc" in text


def test_deprecated_surface_inventory_slash_surfaces_match_runtime_envelopes() -> None:
    for row in deprecation.list_deprecated_surfaces():
        for surface in row["slash_surfaces"]:
            body = str(surface).lstrip("/")
            cmd, _, rest = body.partition(" ")
            match = deprecation.match_deprecated_slash_surface(cmd, rest)

            assert match is not None, surface
            assert match.code == row["code"]
            rendered = deprecation.render_deprecated_surface_message(match)
            assert "deprecated surface" in rendered
            assert f"- code: {row['code']}" in rendered
            assert "- replacement:" in rendered


def test_deprecated_surface_inventory_cli_surfaces_match_runtime_envelopes() -> None:
    for row in deprecation.list_deprecated_surfaces():
        for surface in row["cli_surfaces"]:
            match = deprecation.match_deprecated_cli_surface(surface)

            assert match is not None, surface
            assert match.code == row["code"]
            rendered = deprecation.render_deprecated_surface_message(match)
            assert "deprecated surface" in rendered
            assert f"- code: {row['code']}" in rendered
            assert "- replacement:" in rendered


def test_deprecation_inventory_main_outputs_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = deprecation.main(["--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    codes = {row["code"] for row in payload["deprecated_surfaces"]}
    assert rc == 0
    assert payload["count"] == len(payload["deprecated_surfaces"])
    assert "deprecated_surface.gc_alias" in codes
