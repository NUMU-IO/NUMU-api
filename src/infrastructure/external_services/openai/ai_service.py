"""OpenAI service implementation."""

import json

from openai import AsyncOpenAI

from src.config import settings
from src.core.exceptions import ExternalServiceError
from src.core.interfaces.services.ai_service import (
    BilingualProductDescription,
    ChatMessage,
    ChatResponse,
    IAIService,
    ProductDescription,
)


class OpenAIService(IAIService):
    """AI service implementation using OpenAI."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.openai_api_key
        self.model = settings.openai_model
        self.client = AsyncOpenAI(api_key=self.api_key) if self.api_key else None

    async def generate_product_description(
        self,
        product_name: str,
        category: str | None = None,
        attributes: dict | None = None,
        tone: str = "professional",
    ) -> ProductDescription:
        """Generate product description using OpenAI."""
        if not self.client:
            raise ExternalServiceError("OpenAI", "API key not configured")

        attrs_text = ""
        if attributes:
            attrs_text = ", ".join(f"{k}: {v}" for k, v in attributes.items())

        prompt = f"""Generate a product description for an e-commerce product.

Product Name: {product_name}
Category: {category or "General"}
Attributes: {attrs_text or "None"}
Tone: {tone}

Please provide:
1. A short description (1-2 sentences, max 160 characters)
2. A detailed long description (2-3 paragraphs)
3. An SEO title (max 60 characters)
4. An SEO meta description (max 160 characters)
5. 5-10 relevant tags

Format your response as JSON with keys: short_description, long_description, seo_title, seo_description, tags (array)"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )

            data = json.loads(response.choices[0].message.content)

            return ProductDescription(
                short_description=data.get("short_description", ""),
                long_description=data.get("long_description", ""),
                seo_title=data.get("seo_title", product_name),
                seo_description=data.get("seo_description", ""),
                tags=data.get("tags", []),
            )
        except Exception as e:
            raise ExternalServiceError("OpenAI", str(e))

    async def chat(
        self,
        messages: list[ChatMessage],
        system_prompt: str | None = None,
        max_tokens: int = 1000,
    ) -> ChatResponse:
        """Have a conversation with OpenAI."""
        if not self.client:
            raise ExternalServiceError("OpenAI", "API key not configured")

        chat_messages = []
        if system_prompt:
            chat_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            chat_messages.append({"role": msg.role, "content": msg.content})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=chat_messages,
                max_tokens=max_tokens,
            )

            return ChatResponse(
                message=response.choices[0].message.content,
                tokens_used=response.usage.total_tokens,
            )
        except Exception as e:
            raise ExternalServiceError("OpenAI", str(e))

    async def analyze_image(
        self,
        image_url: str,
        prompt: str = "Describe this product image",
    ) -> str:
        """Analyze an image using OpenAI Vision."""
        if not self.client:
            raise ExternalServiceError("OpenAI", "API key not configured")

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                max_tokens=500,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise ExternalServiceError("OpenAI", str(e))

    async def generate_seo_keywords(
        self,
        product_name: str,
        description: str,
        category: str | None = None,
    ) -> list[str]:
        """Generate SEO keywords for a product."""
        if not self.client:
            raise ExternalServiceError("OpenAI", "API key not configured")

        prompt = f"""Generate SEO keywords for this product:

Name: {product_name}
Category: {category or "General"}
Description: {description}

Provide 10-15 relevant SEO keywords as a JSON array of strings."""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )

            data = json.loads(response.choices[0].message.content)
            return data.get("keywords", [])
        except Exception as e:
            raise ExternalServiceError("OpenAI", str(e))

    async def generate_bilingual_description(
        self,
        product_name: str,
        product_name_ar: str | None = None,
        category: str | None = None,
        image_url: str | None = None,
        attributes: dict | None = None,
        tone: str = "professional",
    ) -> BilingualProductDescription:
        """Generate bilingual (AR/EN) SEO-optimized product descriptions."""
        if not self.client:
            raise ExternalServiceError("OpenAI", "API key not configured")

        attrs_text = ""
        if attributes:
            attrs_text = ", ".join(f"{k}: {v}" for k, v in attributes.items())

        prompt = f"""You are an expert e-commerce copywriter fluent in both English and Arabic.

Generate bilingual product descriptions for:

Product Name (EN): {product_name}
Product Name (AR): {product_name_ar or "N/A"}
Category: {category or "General"}
Attributes: {attrs_text or "None"}
Tone: {tone}

Return a JSON object with these exact keys:
- short_description_en: 1-2 sentences, max 160 chars (English)
- long_description_en: 2-3 paragraphs, engaging copy (English)
- short_description_ar: 1-2 sentences, max 160 chars (Arabic)
- long_description_ar: 2-3 paragraphs, engaging copy (Arabic)
- seo_title_en: max 60 chars (English)
- seo_title_ar: max 60 chars (Arabic)
- seo_description_en: max 160 chars (English)
- seo_description_ar: max 160 chars (Arabic)
- tags: array of 5-10 bilingual tags (mix of English and Arabic)

Make the Arabic content natural and culturally appropriate for the Egyptian market, not a literal translation."""

        try:
            messages: list[dict] = []

            if image_url:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                })
                model = "gpt-4o"
            else:
                messages.append({"role": "user", "content": prompt})
                model = self.model

            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
            )

            data = json.loads(response.choices[0].message.content)

            return BilingualProductDescription(
                short_description_en=data.get("short_description_en", ""),
                long_description_en=data.get("long_description_en", ""),
                short_description_ar=data.get("short_description_ar", ""),
                long_description_ar=data.get("long_description_ar", ""),
                seo_title_en=data.get("seo_title_en", product_name),
                seo_title_ar=data.get("seo_title_ar", product_name_ar or product_name),
                seo_description_en=data.get("seo_description_en", ""),
                seo_description_ar=data.get("seo_description_ar", ""),
                tags=data.get("tags", []),
            )
        except ExternalServiceError:
            raise
        except Exception as e:
            raise ExternalServiceError("OpenAI", str(e))

    async def translate_text(
        self,
        text: str,
        target_language: str,
        source_language: str = "auto",
    ) -> str:
        """Translate text to target language."""
        if not self.client:
            raise ExternalServiceError("OpenAI", "API key not configured")

        source_info = f"from {source_language}" if source_language != "auto" else ""
        prompt = f"Translate the following text {source_info} to {target_language}. Only return the translated text, nothing else:\n\n{text}"

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
        except Exception as e:
            raise ExternalServiceError("OpenAI", str(e))
