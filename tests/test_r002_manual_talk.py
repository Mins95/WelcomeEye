"""The approved manual R002 profile uses the existing protected TCP talk path."""
from load_integration import cap
import test_connect3_tcp_talk as existing
from test_connect3_talk import pcm


class R002ManualTCPTalkTests(existing.TCPTalkTests):
    """Apply the same negotiation, consent and cleanup checks to manual R002."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hub.variant = cap.DeviceVariant.R002
        self.hub.entry.data['r002_connection_mode'] = 'manual'

    async def test_discovery_entry_cannot_inherit_the_manual_tcp_policy(self):
        self.hub.entry.data['r002_connection_mode'] = 'discovery'
        with self.assertRaisesRegex(existing.t.TalkError, '^talk_start_cancelled_or_media_lost$'):
            await self.talk.start('viewer')
        self.connect.assert_not_called()
        self.assertEqual(self.writer.writes, [])

    async def test_revoking_tcp_approval_stops_only_the_microphone(self):
        await self.start()
        self.hub.entry.data['media_tcp_approved'] = False
        await self.talk.feed('viewer', pcm())
        self.assertFalse(self.talk.active)
        self.assertIsNone(self.talk.owner)
        self.assertTrue(self.live.connected)
        self.assertEqual(self.writer.close_count, 1)
