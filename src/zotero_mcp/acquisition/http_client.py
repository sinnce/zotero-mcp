from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class HttpResult:
    success: bool
    content: bytes = b""
    content_type: str = ""
    status_code: int = 0
    error_message: str | None = None
    redirect_chain: list[str] = field(default_factory=list)


def validate_pdf_bytes(content: bytes) -> bool:
    return len(content) >= 4 and content[:4] == b"%PDF"


def validate_content_type(actual: str, expected: str) -> bool:
    return actual.split(";")[0].strip().lower() == expected.lower()


async def fetch_with_retry(
    url: str,
    *,
    max_retries: int = 3,
    timeout: float = 30.0,
    retry_delay: float = 1.0,
    expected_content_type: str | None = None,
    follow_redirects: bool = True,
    max_redirects: int = 10,
) -> HttpResult:
    redirect_chain: list[str] = []
    last_error: str | None = None

    for attempt in range(max_retries):
        if attempt > 0 and retry_delay > 0:
            await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))

        try:
            async with httpx.AsyncClient(
                follow_redirects=follow_redirects,
                max_redirects=max_redirects,
                timeout=timeout,
            ) as client:
                response = await client.get(url)

                for r in response.history:
                    redirect_chain.append(str(r.url))

                if response.status_code in _RETRYABLE_STATUS:
                    last_error = f"HTTP {response.status_code}"
                    continue

                if response.status_code >= 400:
                    return HttpResult(
                        success=False,
                        status_code=response.status_code,
                        error_message=f"HTTP {response.status_code}",
                        redirect_chain=redirect_chain,
                    )

                content = response.content
                content_type = response.headers.get("content-type", "")

                return HttpResult(
                    success=True,
                    content=content,
                    content_type=content_type,
                    status_code=response.status_code,
                    redirect_chain=redirect_chain,
                )

        except httpx.TimeoutException as e:
            last_error = f"Timeout: {e}"
            logger.debug("Request timed out (attempt %d/%d): %s", attempt + 1, max_retries, url)
        except Exception as e:
            last_error = str(e)
            logger.debug("Request failed (attempt %d/%d): %s — %s", attempt + 1, max_retries, url, e)

    return HttpResult(
        success=False,
        error_message=last_error or "Max retries exceeded",
        redirect_chain=redirect_chain,
    )
