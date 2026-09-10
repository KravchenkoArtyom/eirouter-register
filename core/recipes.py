"""Схема и загрузка рецептов автоматизации (JSON/YAML).

Рецепт - декларативный сценарий регистрации: селекторы, задержки и
последовательность шагов живут в файле из папки recipes/, при изменении
вёрстки сайта правится рецепт, а не код приложения.

Форматы строк прокси/селекторов не валидируются здесь - только структура.
Плейсхолдеры вида {email}/{password}/{device_uuid} подставляются движком
во время выполнения (см. core/recipe_runner.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError

try:  # yaml опционален: рецепты могут жить и просто в JSON
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# ---------------- действия ----------------

class NavigateAction(BaseModel):
    action: Literal["navigate"]
    url: str
    timeout_ms: int = 120000


class WaitAction(BaseModel):
    action: Literal["wait"]
    min_ms: int = Field(ge=0)
    max_ms: int | None = None  # рандом min..max (антифрод); None = ровно min_ms


class WaitForSelectorAction(BaseModel):
    action: Literal["wait_for_selector"]
    selector: str
    timeout_ms: int = 60000
    state: Literal["visible", "attached", "hidden", "detached"] = "visible"


class ClickAction(BaseModel):
    action: Literal["click"]
    selector: str
    timeout_ms: int = 60000        # общее время на шаг, включая ретраи
    delay_before_ms: int = 0
    delay_after_ms: int = 0
    retries: int = Field(default=1, ge=1)  # ретраи движка поверх кликов до expect
    # признак следующей страницы: кликаем, пока не выполнится один из них
    expect_url_contains: str | None = None
    expect_selector: str | None = None
    expect_selector_hidden: str | None = None


class TypeAction(BaseModel):
    action: Literal["type"]
    selector: str
    text: str                      # поддерживает плейсхолдеры {email} и т.п.
    timeout_ms: int = 60000
    delay_ms: int = 0              # посимвольная задержка (0 = мгновенный fill)
    clear: bool = True
    click_first: bool = True
    press_enter: bool = False


class CheckAction(BaseModel):
    action: Literal["check"]
    selector: str = "input[type='checkbox']"
    timeout_ms: int = 60000


class WaitEmailAction(BaseModel):
    action: Literal["wait_email_code"]
    pattern: str                   # regex; группа 1 (или всё совпадение) - результат
    timeout_sec: int = 600
    into: str | None = None        # селектор, в который ввести код
    save_as: str = "email_result"  # имя переменной контекста
    delay_ms: int = 0              # посимвольный ввод кода (если into)


class SolveCaptchaAction(BaseModel):
    action: Literal["solve_captcha"]
    mode: Literal["manual", "auto", "service"] = "manual"
    instruction: str = "Решите капчу в открывшемся окне"
    widget_selector: str | None = None   # клик по виджету (открыть попап)
    passed_selector: str | None = None   # элемент со статусом прохождения
    passed_text: str = "passed"
    timeout_sec: int = 300
    handle_selector: str | None = None
    track_selector: str | None = None


class ExtractSessionAction(BaseModel):
    action: Literal["extract_session"]
    cookies: list[str] = []
    localStorage: list[str] = []
    save_prefix: str = "session"   # переменные: session_<имя>


Step = Annotated[
    NavigateAction | WaitAction | WaitForSelectorAction | ClickAction | TypeAction | CheckAction | WaitEmailAction | SolveCaptchaAction | ExtractSessionAction,
    Field(discriminator="action"),
]


# ---------------- рецепт ----------------

class Recipe(BaseModel):
    name: str
    provider: str = "zcode"
    description: str = ""
    vars: dict[str, str] = {}      # значения по умолчанию для плейсхолдеров
    steps: list[Step] = Field(min_length=1)


class RecipeError(Exception):
    """Рецепт не прошёл валидацию структуры."""


def validate_recipe(data: dict, source: str = "<memory>") -> Recipe:
    """Валидация структуры рецепта. Бросает RecipeError со списком проблем."""
    try:
        return Recipe.model_validate(data)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "<корень>"
            problems.append(f"{loc}: {err['msg']}")
        raise RecipeError(f"{source}: " + "; ".join(problems[:8])) from None


def _load_text(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RecipeError(f"{path.name}: YAML не поддерживается (не установлен pyyaml)")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise RecipeError(f"{path.name}: ожидается JSON/YAML-объект с полями рецепта")
    return data


def load_file(path: str | Path) -> Recipe:
    path = Path(path)
    try:
        data = _load_text(path)
    except (json.JSONDecodeError, OSError) as e:
        raise RecipeError(f"{path.name}: файл не читается: {e}") from None
    return validate_recipe(data, path.name)


class RecipeLibrary:
    """Папка рецептов с горячей перезагрузкой: reload() перед каждым
    использованием перечитывает файлы - правки подхватываются без
    перезапуска приложения."""

    def __init__(self, folder: str | Path = "recipes"):
        self.folder = Path(folder)

    def reload(self) -> tuple[dict[str, Recipe], dict[str, str]]:
        """Перечитывает папку. Возвращает (рецепты по имени, ошибки по файлу)."""
        recipes: dict[str, Recipe] = {}
        errors: dict[str, str] = {}
        if not self.folder.is_dir():
            return recipes, {str(self.folder): "папка не существует"}
        paths = sorted(
            list(self.folder.glob("*.json"))
            + list(self.folder.glob("*.yaml"))
            + list(self.folder.glob("*.yml"))
        )
        for path in paths:
            try:
                recipe = load_file(path)
                recipes[recipe.name] = recipe
            except RecipeError as e:
                errors[path.name] = str(e)
        return recipes, errors
