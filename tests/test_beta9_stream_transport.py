"""Regression checks for the beta 9 Home Assistant Stream transport policy."""
import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
CAMERA = ROOT / "custom_components/welcomeeye_local/camera.py"
MANIFEST = ROOT / "custom_components/welcomeeye_local/manifest.json"
DIAGNOSTICS = ROOT / "custom_components/welcomeeye_local/diagnostics.py"
HUB = ROOT / "custom_components/welcomeeye_local/hub.py"


def _camera_class():
    tree = ast.parse(CAMERA.read_text(encoding="utf-8"))
    return next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WelcomeEyeCamera"
    )


def test_beta9_disables_native_webrtc_advertisement_but_keeps_stream_source():
    camera = _camera_class()
    init = next(
        node for node in camera.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assignments = [
        node for node in ast.walk(init)
        if isinstance(node, ast.Assign)
    ]
    disabled = False
    for assignment in assignments:
        if len(assignment.targets) != 1:
            continue
        target = assignment.targets[0]
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_supports_native_async_webrtc"
            and isinstance(assignment.value, ast.Constant)
            and assignment.value.value is False
        ):
            disabled = True
            break
    assert disabled
    assert any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "stream_source"
        for node in camera.body
    )


def test_beta9_keeps_existing_http_mpegts_stream_lifecycle():
    source = HUB.read_text(encoding="utf-8")
    assert "Content-Type: video/mp2t" in source
    assert "http_stream_release" in source
    assert "127.0.0.1" in source


def test_beta9_version_and_diagnostics_policy_are_aligned():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    diagnostics = DIAGNOSTICS.read_text(encoding="utf-8")
    assert manifest["version"] == "0.3.1-beta.9"
    assert 'VERSION = "0.3.1-beta.9"' in diagnostics
    assert '"frontend_transport": "home_assistant_stream"' in diagnostics
    assert '"native_webrtc_advertised": False' in diagnostics
