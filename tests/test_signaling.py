import pytest
from toollighthouse.api.signaling import CameraInviteStore


@pytest.mark.asyncio
async def test_invite_creation_and_single_use_consumption():
    store = CameraInviteStore()
    invite = await store.create_invite("session-1", 1, 1, expires_in_seconds=60)
    assert invite.invite_id.startswith("inv_")
    assert invite.used is False
    assert invite.revoked is False

    # First consumption succeeds
    cred = await store.consume_invite(invite.invite_token)
    assert cred.transmitter_id.startswith("trans_")
    assert cred.owner_session_id == "session-1"
    assert invite.used is True

    # Re-using the same invite token fails immediately
    with pytest.raises(PermissionError, match="ya fue consumida"):
        await store.consume_invite(invite.invite_token)


@pytest.mark.asyncio
async def test_invite_revocation():
    store = CameraInviteStore()
    invite = await store.create_invite("session-1", 1, 1, expires_in_seconds=60)

    cancelled = await store.cancel_invite(invite.invite_id, "session-1")
    assert cancelled is True
    assert invite.revoked is True

    with pytest.raises(PermissionError, match="revocada"):
        await store.consume_invite(invite.invite_token)


@pytest.mark.asyncio
async def test_reconnect_transmitter_within_window():
    store = CameraInviteStore()
    invite = await store.create_invite("session-1", 1, 1, expires_in_seconds=60)
    cred = await store.consume_invite(invite.invite_token)

    # Reconnect with correct ticket succeeds
    reconnected = await store.reconnect_transmitter(cred.transmitter_id, cred.ticket)
    assert reconnected.transmitter_id == cred.transmitter_id

    # Reconnect with wrong ticket fails
    with pytest.raises(PermissionError, match="inválida"):
        await store.reconnect_transmitter(cred.transmitter_id, "wrong-ticket")


@pytest.mark.asyncio
async def test_validate_transmitter_for_upload_fencing():
    store = CameraInviteStore()
    invite = await store.create_invite("session-1", owner_lease_generation=2, source_version=3)
    cred = await store.consume_invite(invite.invite_token)

    # Matching generation and source version succeeds
    valid = await store.validate_transmitter_for_upload(cred.transmitter_id, cred.ticket, 2, 3)
    assert valid.transmitter_id == cred.transmitter_id

    # Mismatched generation rejected
    with pytest.raises(PermissionError, match="obsoleta"):
        await store.validate_transmitter_for_upload(cred.transmitter_id, cred.ticket, 3, 3)

    # Mismatched source version rejected
    with pytest.raises(PermissionError, match="obsoleta"):
        await store.validate_transmitter_for_upload(cred.transmitter_id, cred.ticket, 2, 4)
