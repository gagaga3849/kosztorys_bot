"""Tests for the v1 stub adapters (`whatsapp_adapter.py`, `viber_adapter.py`).

These channels are not in the v1 production Definition of Done (Telegram only - see
`docs/DIARY.md`). The stubs only need to prove: they satisfy the `MessengerAdapter` contract
(instantiable, correct `channel`), validate their required credentials at construction time,
and raise a clear `NotImplementedError` (not a confusing crash) from every abstract method.
"""

import pytest

from messengers.viber_adapter import ViberAdapter
from messengers.whatsapp_adapter import WhatsAppAdapter


class TestWhatsAppAdapter:
    def test_constructor_rejects_empty_api_token(self):
        with pytest.raises(ValueError):
            WhatsAppAdapter(api_token="", phone_number_id="123")

    def test_constructor_rejects_empty_phone_number_id(self):
        with pytest.raises(ValueError):
            WhatsAppAdapter(api_token="token", phone_number_id="")

    def test_channel_is_whatsapp(self):
        adapter = WhatsAppAdapter(api_token="token", phone_number_id="123")
        assert adapter.channel == "whatsapp"

    async def test_receive_raises_not_implemented(self):
        adapter = WhatsAppAdapter(api_token="token", phone_number_id="123")
        with pytest.raises(NotImplementedError):
            await adapter.receive({})

    async def test_send_text_raises_not_implemented(self):
        adapter = WhatsAppAdapter(api_token="token", phone_number_id="123")
        with pytest.raises(NotImplementedError):
            await adapter.send_text("user-1", "hello")

    async def test_send_document_raises_not_implemented(self):
        adapter = WhatsAppAdapter(api_token="token", phone_number_id="123")
        with pytest.raises(NotImplementedError):
            await adapter.send_document("user-1", "/tmp/kosztorys.pdf", "caption")


class TestViberAdapter:
    def test_constructor_rejects_empty_bot_token(self):
        with pytest.raises(ValueError):
            ViberAdapter(bot_token="")

    def test_channel_is_viber(self):
        adapter = ViberAdapter(bot_token="token")
        assert adapter.channel == "viber"

    async def test_receive_raises_not_implemented(self):
        adapter = ViberAdapter(bot_token="token")
        with pytest.raises(NotImplementedError):
            await adapter.receive({})

    async def test_send_text_raises_not_implemented(self):
        adapter = ViberAdapter(bot_token="token")
        with pytest.raises(NotImplementedError):
            await adapter.send_text("user-1", "hello")

    async def test_send_document_raises_not_implemented(self):
        adapter = ViberAdapter(bot_token="token")
        with pytest.raises(NotImplementedError):
            await adapter.send_document("user-1", "/tmp/kosztorys.pdf", "caption")
