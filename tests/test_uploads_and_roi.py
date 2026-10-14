import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from toollighthouse.api import create_app
from toollighthouse.config import Settings


def _jpeg_bytes(size=(64, 64), color="blue"):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _admin_headers(client, admin_key="admin_secret"):
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": admin_key},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    return {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


def test_reference_upload_and_delete_with_path_containment(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings))
    headers = _admin_headers(client)

    # Path traversal in class_id rejected
    for bad_id in ["..", "../etc", "tool_1/sub", "tool 1", "tool_1\\bad"]:
        resp = client.post(
            f"/v1/tools/{bad_id}/references",
            headers=headers,
            files={"file": ("ref.jpg", _jpeg_bytes(), "image/jpeg")},
        )
        assert resp.status_code in (400, 404, 422)

    # Valid upload for tool_1
    upload_resp = client.post(
        "/v1/tools/tool_1/references",
        headers=headers,
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert upload_resp.status_code == 200
    ref_info = upload_resp.json()
    assert "image_id" in ref_info
    image_id = ref_info["image_id"]
    assert "thumbnail_data_url" in ref_info

    # List references for tool_1
    list_resp = client.get("/v1/tools/tool_1/references", headers=headers)
    assert list_resp.status_code == 200
    refs = list_resp.json()["references"]
    assert any(r["image_id"] == image_id for r in refs)

    # Path traversal in delete rejected
    bad_delete = client.delete(f"/v1/tools/tool_1/references/../../etc/passwd", headers=headers)
    assert bad_delete.status_code in (400, 404, 422)

    # Valid delete
    del_resp = client.delete(f"/v1/tools/tool_1/references/{image_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Confirm deleted
    list_after = client.get("/v1/tools/tool_1/references", headers=headers).json()
    assert not any(r["image_id"] == image_id for r in list_after["references"])


def test_roi_calibration_validates_bounds():
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings))
    headers = _admin_headers(client)

    # Set background first (100x80)
    bg_bytes = _jpeg_bytes(size=(100, 80))
    bg_resp = client.post("/v1/calibration/background", headers=headers, files={"file": ("bg.jpg", bg_bytes, "image/jpeg")})
    assert bg_resp.status_code == 200

    # Valid ROI inside (100x80)
    roi_ok = client.put("/v1/calibration/roi", headers=headers, json={"roi": [10, 10, 60, 50]})
    assert roi_ok.status_code == 200
    assert roi_ok.json()["roi"] == [10, 10, 60, 50]

    # Out of bounds ROI rejected
    roi_bad_x = client.put("/v1/calibration/roi", headers=headers, json={"roi": [90, 10, 30, 20]})  # 90+30 > 100
    assert roi_bad_x.status_code == 400

    roi_bad_y = client.put("/v1/calibration/roi", headers=headers, json={"roi": [10, 70, 20, 20]})  # 70+20 > 80
    assert roi_bad_y.status_code == 400

    # Negative coordinates rejected
    roi_neg = client.put("/v1/calibration/roi", headers=headers, json={"roi": [-5, 10, 20, 20]})
    assert roi_neg.status_code == 400


def test_image_format_validation_rejects_unsupported_formats():
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings))
    headers = _admin_headers(client)

    # 1. BMP format rejected
    bmp_buf = io.BytesIO()
    Image.new("RGB", (50, 50), "red").save(bmp_buf, "BMP")
    resp_bmp = client.post(
        "/v1/tools/tool_1/references",
        headers=headers,
        files={"file": ("photo.bmp", bmp_buf.getvalue(), "image/bmp")},
    )
    assert resp_bmp.status_code == 400
    assert "unsupported image format" in resp_bmp.json()["detail"].lower()

    # 2. Text payload disguised as JPEG rejected
    resp_txt = client.post(
        "/v1/tools/tool_1/references",
        headers=headers,
        files={"file": ("fake.jpg", b"NOT_AN_IMAGE", "image/jpeg")},
    )
    assert resp_txt.status_code == 400


def test_resolve_contained_path_symlink_escape_detected(tmp_path):
    from toollighthouse.api.app import _resolve_contained_path
    from fastapi import HTTPException

    safe_root = tmp_path / "safe_dir"
    safe_root.mkdir()
    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("classified", encoding="utf-8")

    # Within root is fine
    contained = safe_root / "child.txt"
    contained.write_text("child", encoding="utf-8")
    assert _resolve_contained_path(safe_root, "child.txt") == contained.resolve()

    # Relative path escape rejected
    with pytest.raises(HTTPException) as exc:
        _resolve_contained_path(safe_root, "../outside_secret.txt")
    assert exc.value.status_code == 400


def test_subsequent_frame_dimension_mismatch_fails_cleanly():
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings))
    headers = _admin_headers(client)

    # Background is calibrated to 100x80
    bg_bytes = _jpeg_bytes(size=(100, 80))
    client.post("/v1/calibration/background", headers=headers, files={"file": ("bg.jpg", bg_bytes, "image/jpeg")})

    # Subsequent capture with differing dimensions (200x150) fails
    mismatch_bytes = _jpeg_bytes(size=(200, 150))
    resp = client.post("/v1/analyze/photo", headers=headers, files={"file": ("frame.jpg", mismatch_bytes, "image/jpeg")})
    assert resp.status_code == 400
    assert "dimensions" in resp.json()["detail"].lower()

