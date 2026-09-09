"""Small CLI editor for universal_autoregister scenario JSON files."""

import argparse
import json
import os
from pathlib import Path

from universal_autoregister import SCENARIOS, load_scenarios


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def choose_scenario() -> Path:
    scenarios = load_scenarios()
    for index, scenario in enumerate(scenarios, 1):
        print(f"{index}. {scenario.get('name', scenario['_path'].stem)}")
    selected = int(input("Scenario: ")) - 1
    if selected not in range(len(scenarios)):
        raise ValueError("Unknown scenario")
    return Path(scenarios[selected]["_path"])


def interactive(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    while True:
        print("\n1. Show steps\n2. Add step\n3. Remove step\n4. Add DOM HTML step\n5. Change URL\n6. Save and exit")
        choice = input("Action: ").strip()
        steps = data.setdefault("steps", [])
        if choice == "1":
            for index, step in enumerate(steps, 1):
                print(f"{index}: {json.dumps(step, ensure_ascii=False)}")
        elif choice == "2":
            raw = input("Step JSON: ")
            step = json.loads(raw)
            if not isinstance(step, dict) or not step.get("action"):
                raise ValueError("Step must be an object with action")
            steps.append(step)
        elif choice == "3":
            steps.pop(int(input("Step number: ")) - 1)
        elif choice == "4":
            selector = input("Target CSS selector: ").strip()
            operation = input("Operation (remove/set_html/insert_html/append_html/prepend_html): ").strip()
            step = {"action": "dom", "selector": selector, "operation": operation}
            if operation in {"set_html", "insert_html", "append_html", "prepend_html"}:
                source = input("HTML text or file path prefixed with @: ")
                step["html"] = Path(source[1:]).read_text(encoding="utf-8") if source.startswith("@") else source
            steps.append(step)
        elif choice == "5":
            data["url"] = input("URL: ").strip()
        elif choice == "6":
            save(path, data)
            print(f"Saved {path}")
            return
        else:
            print("Choose 1-6.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Edit universal registrar scenarios")
    parser.add_argument("--scenario", type=Path, help="Scenario JSON to edit")
    parser.add_argument("--remove-step", type=int, help="Remove a 1-based step")
    parser.add_argument("--add-dom", nargs=2, metavar=("SELECTOR", "OPERATION"),
                        help="Append a DOM step")
    parser.add_argument("--html-file", type=Path, help="HTML file for an add/set DOM operation")
    args = parser.parse_args()
    path = args.scenario or choose_scenario()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not (args.remove_step or args.add_dom):
        interactive(path)
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    steps = data.setdefault("steps", [])
    if args.remove_step:
        steps.pop(args.remove_step - 1)
    if args.add_dom:
        selector, operation = args.add_dom
        step = {"action": "dom", "selector": selector, "operation": operation}
        if args.html_file:
            step["html"] = args.html_file.read_text(encoding="utf-8")
        steps.append(step)
    save(path, data)
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
