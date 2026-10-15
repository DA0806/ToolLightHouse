import pytest
from toollighthouse.camera_registry import CameraSourceRegistry


def test_registry_initialization_and_five_canonical_sources():
    registry = CameraSourceRegistry(initial_active="remote_phone")
    sources = registry.list_sources()
    assert len(sources) == 5

    source_ids = {s["source_id"] for s in sources}
    assert source_ids == {
        "remote_phone",
        "laptop_builtin",
        "usb_webcam",
        "operator_device",
        "manual_upload",
    }

    assert registry.active_source_id == "remote_phone"
    assert registry.source_version == 1

    active_desc = registry.get_source("remote_phone")
    assert active_desc.is_active is True
    assert active_desc.status == "active"

    standby_desc = registry.get_source("laptop_builtin")
    assert standby_desc.is_active is False
    assert standby_desc.status == "standby"


@pytest.mark.asyncio
async def test_switch_active_source_safely():
    registry = CameraSourceRegistry(initial_active="remote_phone")
    teardowns = []

    async def mock_teardown(old_src, new_src, new_ver):
        teardowns.append((old_src, new_src, new_ver))

    desc = await registry.switch_active_source("usb_webcam", mock_teardown)
    assert desc.source_id == "usb_webcam"
    assert desc.is_active is True
    assert registry.active_source_id == "usb_webcam"
    assert registry.source_version == 2
    assert teardowns == [("remote_phone", "usb_webcam", 2)]

    # Old source should now be standby
    old_desc = registry.get_source("remote_phone")
    assert old_desc.is_active is False
    assert old_desc.status == "standby"


@pytest.mark.asyncio
async def test_switch_to_invalid_source_raises_key_error():
    registry = CameraSourceRegistry()
    with pytest.raises(KeyError, match="does not exist"):
        await registry.switch_active_source("nonexistent_cam")
