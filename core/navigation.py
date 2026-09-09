"""Navigation helpers shared by registrars.

Centralizes the page-load step so a single slow site (cf. the
`Timeout 120000ms exceeded` you hit on wisdomsatan.club) no longer aborts the
whole run. Callers retry a few times before treating the account as failed and
moving on.
"""

from typing import Callable

from playwright.async_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeout


async def goto_with_retry(page: Page, url: str, *,
                          wait_until: str = "domcontentloaded",
                          attempts: int = 3, log: Callable[[str], None] | None = None) -> None:
    """Navigate to ``url``, retrying on timeouts.

    Raises the last error only after all attempts fail, so the caller can record
    the account as failed and continue with the next one instead of aborting.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until=wait_until)
            return
        except (PlaywrightTimeout, PlaywrightError) as error:
            last_error = error
            if log:
                log(f"[nav] Attempt {attempt}/{attempts} to load {url} failed: {type(error).__name__}: {error}")
            if attempt < attempts:
                # Give the site / network a moment before retrying.
                await page.wait_for_timeout(3000)
    assert last_error is not None
    raise last_error
