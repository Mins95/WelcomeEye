"""Per-peer aioice cleanup adapter; no global HA transport patches.

aioice 0.10.x has no public transaction cancellation API on protocol shutdown.
The two private Transaction fields used here are covered by dependency tests.
"""
import asyncio


def _cancel_transactions(protocol, report):
    count = 0
    for transaction in tuple(protocol.transactions.values()):
        handle = getattr(transaction, '_Transaction__timeout_handle', None)
        future = getattr(transaction, '_Transaction__future', None)
        if handle is not None:
            handle.cancel()
        if isinstance(future, asyncio.Future) and not future.done():
            future.cancel()
            count += 1
    if count:
        report(count)


def _guard_protocol(protocol, report):
    if getattr(protocol, '_welcomeeye_guarded', False):
        return
    protocol._welcomeeye_guarded = True
    original_close = protocol.close
    original_lost = protocol.connection_lost
    original_send = protocol.send_stun

    async def close():
        _cancel_transactions(protocol, report)
        await original_close()

    def connection_lost(exc):
        _cancel_transactions(protocol, report)
        original_lost(exc)

    def send_stun(message, addr):
        transport = protocol.transport
        if transport is None or getattr(transport, 'is_closing', lambda: False)():
            _cancel_transactions(protocol, report)
            return
        original_send(message, addr)

    protocol.close = close
    protocol.connection_lost = connection_lost
    protocol.send_stun = send_stun


class _OwnedProtocols(list):
    def __init__(self, protocols, report):
        super().__init__()
        self.report = report
        self.extend(protocols)

    def append(self, protocol):
        _guard_protocol(protocol, self.report)
        super().append(protocol)

    def extend(self, protocols):
        for protocol in protocols:
            self.append(protocol)

    def __iadd__(self, protocols):
        self.extend(protocols)
        return self


def protect_peer_ice(pc, report):
    """Install before gathering, on this WelcomeEye peer's connections only."""
    dtls = [getattr(t.sender, 'transport', None) for t in pc.getTransceivers()
            if hasattr(t, 'sender')]
    sctp = getattr(pc, 'sctp', None)
    if sctp is not None:
        dtls.append(sctp.transport)
    for transport in dtls:
        ice = getattr(transport, 'transport', None)
        connection = getattr(ice, '_connection', None)
        if connection is not None and not isinstance(connection._protocols, _OwnedProtocols):
            connection._protocols = _OwnedProtocols(connection._protocols, report)
