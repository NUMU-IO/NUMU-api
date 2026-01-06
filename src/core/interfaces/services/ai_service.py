"""AI service interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProductDescription:
    """AI-generated product description."""

    short_description: str
    long_description: str
    seo_title: str
    seo_description: str
    tags: list[str]


@dataclass
class ChatMessage:
    """Chat message for AI conversation."""

    role: str  # "user", "assistant", "system"
    content: str


@dataclass
class ChatResponse:
    """AI chat response."""

    message: str
    tokens_used: int


class IAIService(ABC):
    """AI service interface."""

    @abstractmethod
    async def generate_product_description(
        self,
        product_name: str,
        category: str | None = None,
        attributes: dict | None = None,
        tone: str = "professional",
    ) -> ProductDescription:
        """Generate product description using AI."""
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        system_prompt: str | None = None,
        max_tokens: int = 1000,
    ) -> ChatResponse:
        """Have a conversation with AI."""
        ...

    @abstractmethod
    async def analyze_image(
        self,
        image_url: str,
        prompt: str = "Describe this product image",
    ) -> str:
        """Analyze an image and return description."""
        ...

    @abstractmethod
    async def generate_seo_keywords(
        self,
        product_name: str,
        description: str,
        category: str | None = None,
    ) -> list[str]:
        """Generate SEO keywords for a product."""
        ...

    @abstractmethod
    async def translate_text(
        self,
        text: str,
        target_language: str,
        source_language: str = "auto",
    ) -> str:
        """Translate text to target language."""
        ...
