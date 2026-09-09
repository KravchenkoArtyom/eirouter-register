"""Execute the form actions used by the standalone eirouter recipe."""

from collections.abc import Callable

from playwright.async_api import Page, Error as PlaywrightError

from core.recipes import CheckAction, Recipe, TypeAction


class StepFailed(RuntimeError):
    pass


class RecipeRunner:
    def __init__(self, page: Page, context_vars: dict[str, str] | None = None,
                 log: Callable[[str], None] = print) -> None:
        self.page = page
        self.ctx = context_vars or {}
        self.log = log

    async def run(self, recipe: Recipe) -> dict[str, str]:
        for index, step in enumerate(recipe.steps, 1):
            try:
                if isinstance(step, TypeAction):
                    field = self.page.locator(step.selector).first
                    await field.wait_for(timeout=step.timeout_ms)
                    if step.clear:
                        await field.fill("")
                    text = step.text.format_map(self.ctx)
                    if step.delay_ms:
                        await field.press_sequentially(text, delay=step.delay_ms)
                    else:
                        await field.fill(text)
                elif isinstance(step, CheckAction):
                    await self.page.locator(step.selector).first.check(timeout=step.timeout_ms)
                else:
                    raise StepFailed(f"Unsupported eirouter form action: {step.action}")
            except PlaywrightError:
                raise StepFailed(f"Form action {index} ({step.action}) failed") from None
            self.log(f"[form] Step {index}/{len(recipe.steps)}: {step.action} complete")
        return dict(self.ctx)
