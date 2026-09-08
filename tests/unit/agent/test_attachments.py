"""Attachments: the agent never touches bytes, and only a vision model sees them.

The hub uploads through the existing customization-assets route and sends a
URL. Whether that URL becomes an OpenAI image block or stays as text depends on
whether the configured model can actually read images — a text-only model
rejects the block form, and does not need it, because the URL is already in the
message text where it can be handed to create_product.
"""

from __future__ import annotations

import pytest

from src.core.agent.interfaces import ChatMessage
from src.infrastructure.agent.llm import provider as prov


def _wire(msg, *, vision: bool, monkeypatch):
    monkeypatch.setattr(prov.app_settings, "agent_llm_vision", vision, raising=False)
    return prov._message_to_wire(msg)


IMG = "https://cdn.numueg.app/customization/s1/product_image_1.jpg"


class TestVisionOff:
    def test_the_url_stays_as_text(self, monkeypatch):
        wire = _wire(
            ChatMessage(
                role="user",
                content=f"add this\n[attached image: {IMG}]",
                image_urls=[IMG],
            ),
            vision=False,
            monkeypatch=monkeypatch,
        )
        assert isinstance(wire["content"], str)
        # Still reachable: the model can pass this URL to create_product.
        assert IMG in wire["content"]

    def test_a_message_with_no_images_is_unchanged(self, monkeypatch):
        wire = _wire(
            ChatMessage(role="user", content="how many orders today"),
            vision=False,
            monkeypatch=monkeypatch,
        )
        assert wire == {"role": "user", "content": "how many orders today"}


class TestVisionOn:
    def test_images_become_content_blocks(self, monkeypatch):
        wire = _wire(
            ChatMessage(role="user", content="write a description", image_urls=[IMG]),
            vision=True,
            monkeypatch=monkeypatch,
        )
        assert isinstance(wire["content"], list)
        assert wire["content"][0] == {"type": "text", "text": "write a description"}
        assert wire["content"][1] == {
            "type": "image_url",
            "image_url": {"url": IMG},
        }

    def test_a_message_without_images_stays_a_plain_string(self, monkeypatch):
        """Only messages that actually carry an image take the block form."""
        wire = _wire(
            ChatMessage(role="user", content="hello"),
            vision=True,
            monkeypatch=monkeypatch,
        )
        assert wire["content"] == "hello"

    def test_only_user_messages_are_converted(self, monkeypatch):
        wire = _wire(
            ChatMessage(role="assistant", content="ok", image_urls=[IMG]),
            vision=True,
            monkeypatch=monkeypatch,
        )
        assert wire["content"] == "ok"


class TestTheRequestContract:
    def test_attachments_must_be_https(self):
        """An http URL is mixed content once a theme renders it."""
        from pydantic import ValidationError

        from src.api.v1.agent.routes import ChatAttachment

        ChatAttachment(url=IMG)  # fine
        with pytest.raises(ValidationError):
            ChatAttachment(url="http://cdn.numueg.app/x.jpg")

    def test_attachments_are_optional(self):
        """Older hub builds never send the field."""
        from src.api.v1.agent.routes import ChatRequest

        assert ChatRequest(message="hi").attachments == []
