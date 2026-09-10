"""Консольный редактор сценариев: `py -3 -m tools.scenario_editor`.

Основной редактор — WebUI (`scripts\\webui.bat`), этот нужен, когда браузер
поднимать не хочется: показать шаги, добавить/удалить шаг, вставить HTML-правку
или почтовое действие, поменять адрес.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from universal.actions import ACTIONS, MAIL_ACTIONS
from universal.scenarios import load_scenarios, read, save, title_of, validate


def choose_scenario() -> Path:
    scenarios = load_scenarios()
    for index, scenario in enumerate(scenarios, 1):
        print(f"{index}. {title_of(scenario['_path'], scenario)}")
    selected = int(input("Сценарий: ")) - 1
    if selected not in range(len(scenarios)):
        raise ValueError("Такого сценария нет")
    return Path(scenarios[selected]["_path"])


def show(steps: list[dict]) -> None:
    for index, step in enumerate(steps, 1):
        print(f"{index}: {json.dumps(step, ensure_ascii=False)}")


def add_mail_step(steps: list[dict]) -> None:
    visible = [name for name in MAIL_ACTIONS if not ACTIONS[name].get("hidden")]
    for index, name in enumerate(visible, 1):
        print(f"{index}. {ACTIONS[name]['label']}")
    action = visible[int(input("Действие: ")) - 1]
    step: dict = {"action": action}
    for field in ACTIONS[action]["fields"]:
        raw = input(f"{field['label']} (Enter — пропустить): ").strip()
        if not raw:
            continue
        step[field["name"]] = float(raw) if field["type"] == "number" else raw
    position = input(f"Вставить перед шагом № (Enter — в конец, всего {len(steps)}): ").strip()
    steps.insert(int(position) - 1 if position else len(steps), step)


def interactive(path: Path) -> None:
    data = read(path)
    while True:
        print("\n1. Показать шаги\n2. Добавить шаг (JSON)\n3. Удалить шаг"
              "\n4. Шаг правки DOM\n5. Почтовое действие\n6. Изменить адрес"
              "\n7. Проверить\n8. Сохранить и выйти")
        choice = input("Действие: ").strip()
        steps = data.setdefault("steps", [])
        if choice == "1":
            show(steps)
        elif choice == "2":
            step = json.loads(input("JSON шага: "))
            if not isinstance(step, dict) or not step.get("action"):
                raise ValueError("Шаг — объект с полем action")
            steps.append(step)
        elif choice == "3":
            steps.pop(int(input("Номер шага: ")) - 1)
        elif choice == "4":
            selector = input("CSS-селектор: ").strip()
            operation = input("Операция (remove/set_html/insert_html/append_html/prepend_html/set_text): ").strip()
            step = {"action": "dom", "selectors": [selector], "operation": operation}
            if operation != "remove":
                source = input("HTML/текст или путь к файлу с префиксом @: ")
                value = Path(source[1:]).read_text(encoding="utf-8") if source.startswith("@") else source
                step["text" if operation == "set_text" else "html"] = value
            steps.append(step)
        elif choice == "5":
            add_mail_step(steps)
        elif choice == "6":
            data["url"] = input("URL: ").strip()
        elif choice == "7":
            errors = validate(data)
            print("Ошибок нет" if not errors else "\n".join(errors))
        elif choice == "8":
            errors = validate(data)
            if errors and input(f"Есть ошибки:\n{chr(10).join(errors)}\nСохранить всё равно? (y/N) ").lower() != "y":
                continue
            save(path, data)
            print(f"Сохранено: {path}")
            return
        else:
            print("Выберите 1-8.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Редактор сценариев регистратора")
    parser.add_argument("--scenario", type=Path, help="JSON сценария")
    parser.add_argument("--remove-step", type=int, help="Удалить шаг по номеру (с 1)")
    parser.add_argument("--add-dom", nargs=2, metavar=("SELECTOR", "OPERATION"),
                        help="Добавить шаг правки DOM")
    parser.add_argument("--html-file", type=Path, help="Файл с HTML для DOM-операции")
    args = parser.parse_args()
    path = args.scenario or choose_scenario()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not (args.remove_step or args.add_dom):
        interactive(path)
        return 0
    data = read(path)
    steps = data.setdefault("steps", [])
    if args.remove_step:
        steps.pop(args.remove_step - 1)
    if args.add_dom:
        selector, operation = args.add_dom
        step = {"action": "dom", "selectors": [selector], "operation": operation}
        if args.html_file:
            step["html"] = args.html_file.read_text(encoding="utf-8")
        steps.append(step)
    save(path, data)
    print(f"Сохранено: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
