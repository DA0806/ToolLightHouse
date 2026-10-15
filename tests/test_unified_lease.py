import asyncio
import pytest
import time
from toollighthouse.config import Settings
from toollighthouse.api.sessions import UnifiedSessionStore, PhysicalControlLease, Session


def test_session_roles_and_lease():
    settings = Settings(auth_required=False, lease_ttl_seconds=1.0)
    store = UnifiedSessionStore(settings)

    # Controller automatically gets lease
    ctrl = store.create("dev-1", "controller")
    assert store.controller_id == ctrl.session_id
    assert store.lease is not None
    assert store.lease.role == "controller"
    assert store.lease_generation == 1

    # Reader does not get lease
    reader = store.create("dev-2", "reader")
    assert reader.role == "reader"
    assert store.controller_id == ctrl.session_id

    # Trying to create second controller while first is active fails
    with pytest.raises(PermissionError, match="another controller session is active"):
        store.create("dev-3", "controller")


def test_acquire_vacant_lease_and_role_restrictions():
    settings = Settings(auth_required=False, lease_ttl_seconds=1.0)
    store = UnifiedSessionStore(settings)

    reader = store.create("dev-reader", "reader")
    with pytest.raises(PermissionError, match="camera_transmitter"):
        store.create("dev-trans", "camera_transmitter")

    # Manually insert transmitter session to verify role restriction on lease
    transmitter = Session(
        session_id="trans-123",
        device_id="dev-trans",
        role="camera_transmitter",
        ticket="trans-ticket",
        expires_at=time.time() + 60,
        lease_expires_at=0,
    )
    store.sessions[transmitter.session_id] = transmitter
    admin = store.create("dev-admin", "admin")

    # Readers and transmitters are explicitly denied physical control
    with pytest.raises(PermissionError, match="no tiene permiso"):
        store.acquire_vacant_lease(reader.session_id)

    with pytest.raises(PermissionError, match="no tiene permiso"):
        store.acquire_vacant_lease(transmitter.session_id)

    # Admin can acquire vacant lease
    lease = store.acquire_vacant_lease(admin.session_id)
    assert lease.holder_session_id == admin.session_id
    assert lease.role == "admin"
    assert store.lease_generation == 1

    # Same admin refreshing lease succeeds
    lease2 = store.acquire_vacant_lease(admin.session_id)
    assert lease2.holder_session_id == admin.session_id
    assert lease2.lease_generation == 1


@pytest.mark.asyncio
async def test_admin_takeover_control_lease_success():
    settings = Settings(auth_required=False, lease_ttl_seconds=5.0)
    store = UnifiedSessionStore(settings)

    ctrl = store.create("dev-ctrl", "controller")
    assert store.controller_id == ctrl.session_id
    gen_before = store.lease_generation

    admin = store.create("dev-admin", "admin")
    teardown_called = []

    async def mock_teardown(new_gen: int):
        teardown_called.append(new_gen)

    new_lease = await store.takeover_control_lease(admin.session_id, mock_teardown)
    assert new_lease.holder_session_id == admin.session_id
    assert new_lease.role == "admin"
    assert new_lease.lease_generation == gen_before + 1
    assert teardown_called == [new_lease.lease_generation]
    assert store.controller_id == admin.session_id


@pytest.mark.asyncio
async def test_admin_takeover_failure_leaves_safe_disabled_state():
    settings = Settings(auth_required=False, lease_ttl_seconds=5.0)
    store = UnifiedSessionStore(settings)

    ctrl = store.create("dev-ctrl", "controller")
    admin = store.create("dev-admin", "admin")

    async def failing_teardown(new_gen: int):
        raise ConnectionError("Arduino hardware communication failed")

    with pytest.raises(RuntimeError, match="Fallo durante la expropiación"):
        await store.takeover_control_lease(admin.session_id, failing_teardown)

    # After failure, lease must be safely None and transition_state must be 'failed'
    assert store.lease is None
    assert store.controller_id is None
    assert store.transition_state == "failed"


def test_heartbeat_renewal():
    settings = Settings(auth_required=False, lease_ttl_seconds=2.0)
    store = UnifiedSessionStore(settings)

    ctrl = store.create("dev-ctrl", "controller")
    old_lease_expiry = store.lease.lease_expires_at

    time.sleep(0.05)
    success = store.heartbeat(ctrl.session_id, ctrl.ticket)
    assert success is True
    assert store.lease.lease_expires_at > old_lease_expiry

    # Invalid ticket rejected
    assert store.heartbeat(ctrl.session_id, "wrong-ticket") is False

    # Reader cannot heartbeat for physical lease
    reader = store.create("dev-reader", "reader")
    assert store.heartbeat(reader.session_id, reader.ticket) is False
