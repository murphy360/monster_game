"""Integration test for POST /levels/reprocess-all."""

import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from backend import storage
from backend.main import app

client = TestClient(app)

SCENE_COLOR = (40, 60, 90)
KEY_COLOR = (167, 239, 70)
REAL_WINDOW_BOX = (50, 50, 90, 80)  # x0, y0, x1, y1


def _build_test_image_data_uri() -> str:
    image = Image.new("RGB", (200, 150), SCENE_COLOR)
    pixels = image.load()
    width, height = image.size

    band = 15
    for y in range(height):
        for x in range(width):
            if y < band or y >= height - band or x < band or x >= width - band:
                pixels[x, y] = KEY_COLOR

    x0, y0, x1, y1 = REAL_WINDOW_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = KEY_COLOR

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return "data:image/png;base64," + b64


def test_reprocess_all_dry_run_reports_changes_without_persisting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "LEVELS_DIR", str(tmp_path))

    image_url = _build_test_image_data_uri()
    level_id = storage.save_level(
        {
            "title": "Stale Test Level",
            "original_background_url": image_url,
            "window_key_color": "#A7EF46",
            "boundary_color": "#A7EF46",
            "background_url": image_url,
            "windows": [],  # stale: a fresh reprocess should find the one real window
            "board_width": 200,
            "board_height": 150,
        },
        theme="test",
    )

    response = client.post("/levels/reprocess-all", json={"apply": False})
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1

    result = results[0]
    assert result["id"] == level_id
    assert result["old_window_count"] == 0
    assert result["new_window_count"] == 1
    assert result["changed"] is True
    assert result["applied"] is False

    # Dry run must not have touched the saved record.
    unchanged = storage.get_level(level_id)
    assert unchanged["windows"] == []


def test_reprocess_all_apply_persists_the_new_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "LEVELS_DIR", str(tmp_path))

    image_url = _build_test_image_data_uri()
    level_id = storage.save_level(
        {
            "title": "Stale Test Level 2",
            "original_background_url": image_url,
            "window_key_color": "#A7EF46",
            "boundary_color": "#A7EF46",
            "background_url": image_url,
            "windows": [],
            "board_width": 200,
            "board_height": 150,
        },
        theme="test",
    )

    response = client.post("/levels/reprocess-all", json={"apply": True})
    assert response.status_code == 200
    result = response.json()[0]
    assert result["applied"] is True
    assert result["new_window_count"] == 1

    updated = storage.get_level(level_id)
    assert len(updated["windows"]) == 1
    assert updated["background_url"] != image_url  # now the masked/processed image


def test_reprocess_single_level_dry_run_reports_changes_without_persisting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "LEVELS_DIR", str(tmp_path))

    image_url = _build_test_image_data_uri()
    level_id = storage.save_level(
        {
            "title": "Stale Single Level",
            "original_background_url": image_url,
            "window_key_color": "#A7EF46",
            "boundary_color": "#A7EF46",
            "background_url": image_url,
            "windows": [],
            "board_width": 200,
            "board_height": 150,
        },
        theme="test",
    )

    response = client.post(f"/levels/{level_id}/reprocess", json={"apply": False})
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == level_id
    assert result["old_window_count"] == 0
    assert result["new_window_count"] == 1
    assert result["changed"] is True
    assert result["applied"] is False

    unchanged = storage.get_level(level_id)
    assert unchanged["windows"] == []


def test_reprocess_single_level_apply_persists_the_new_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "LEVELS_DIR", str(tmp_path))

    image_url = _build_test_image_data_uri()
    level_id = storage.save_level(
        {
            "title": "Stale Single Level 2",
            "original_background_url": image_url,
            "window_key_color": "#A7EF46",
            "boundary_color": "#A7EF46",
            "background_url": image_url,
            "windows": [],
            "board_width": 200,
            "board_height": 150,
            # A stale candidate row from generation time, same shape the real
            # pipeline saves - a reprocess must refresh this row too, not just
            # the top-level "windows" field, or the review UI's Mask Color
            # Decision panel (which reads candidate_scores) keeps showing the
            # old count even after the level itself is up to date.
            "color_decision": {
                "selected_key_color": "#A7EF46",
                "candidate_scores": [
                    {"key_color": "#A7EF46", "window_count": 0, "windows": []},
                ],
            },
        },
        theme="test",
    )

    response = client.post(f"/levels/{level_id}/reprocess", json={"apply": True})
    assert response.status_code == 200
    result = response.json()
    assert result["applied"] is True
    assert result["new_window_count"] == 1

    updated = storage.get_level(level_id)
    updated_candidates = updated["color_decision"]["candidate_scores"]
    assert len(updated_candidates) == 1
    assert updated_candidates[0]["key_color"] == "#A7EF46"
    assert updated_candidates[0]["window_count"] == 1
    assert len(updated_candidates[0]["windows"]) == 1
    assert len(updated["windows"]) == 1
    assert updated["background_url"] != image_url


def test_reprocess_single_level_missing_id_returns_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "LEVELS_DIR", str(tmp_path))

    response = client.post("/levels/does-not-exist/reprocess", json={"apply": False})
    assert response.status_code == 404
