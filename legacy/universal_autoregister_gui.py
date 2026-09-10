"""Устаревший Tkinter-интерфейс. Актуальный интерфейс — WebUI (webui/).

Оставлен для запуска без FastAPI; новые возможности (инспектор, почтовые
действия, создание сценариев) есть только в WebUI.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from universal.scenarios import ROOT, SCENARIOS_DIR as SCENARIOS, load_scenarios


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Universal Autoregister")
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.scenarios: list[dict] = []
        self.current: dict | None = None
        self.step_index: int | None = None
        self._build()
        self._reload_scenarios()
        self.after(100, self._poll_output)

    def _build(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Сценарий:").pack(side="left")
        self.scenario_var = tk.StringVar()
        self.scenario_box = ttk.Combobox(top, textvariable=self.scenario_var, state="readonly", width=30)
        self.scenario_box.pack(side="left", padx=6)
        self.scenario_box.bind("<<ComboboxSelected>>", lambda _: self._select_scenario())
        ttk.Button(top, text="Обновить", command=self._reload_scenarios).pack(side="left")
        ttk.Button(top, text="Сохранить сценарий", command=self._save_scenario).pack(side="left", padx=6)
        ttk.Button(top, text="Проверить JSON", command=self._validate_scenario).pack(side="left")

        settings = ttk.LabelFrame(self, text="Запуск", padding=8)
        settings.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(settings, text="Аккаунтов:").grid(row=0, column=0, sticky="w")
        self.count_var = tk.StringVar(value="1")
        ttk.Spinbox(settings, from_=1, to=10000, textvariable=self.count_var, width=8).grid(row=0, column=1, padx=5)
        ttk.Label(settings, text="Почта:").grid(row=0, column=2, sticky="w", padx=(18, 0))
        self.mail_var = tk.StringVar(value="tmail")
        ttk.Combobox(settings, textvariable=self.mail_var, values=("none", "tmail", "mail.tm"),
                     state="readonly", width=12).grid(row=0, column=3, padx=5)
        self.proxy_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text="Использовать proxies.txt", variable=self.proxy_var).grid(row=0, column=4, padx=(18, 5))
        self.run_button = ttk.Button(settings, text="Запустить", command=self._run)
        self.run_button.grid(row=0, column=5, padx=8)
        self.stop_button = ttk.Button(settings, text="Остановить", command=self._stop, state="disabled")
        self.stop_button.grid(row=0, column=6)

        url_frame = ttk.Frame(self, padding=(8, 0, 8, 8))
        url_frame.pack(fill="x")
        ttk.Label(url_frame, text="URL сайта:").pack(side="left")
        self.url_var = tk.StringVar()
        ttk.Entry(url_frame, textvariable=self.url_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(url_frame, text="Секция:").pack(side="left")
        self.section_var = tk.StringVar(value="steps")
        self.section_box = ttk.Combobox(url_frame, textvariable=self.section_var,
                                        values=("steps", "submit", "success"), state="readonly", width=10)
        self.section_box.pack(side="left", padx=6)
        self.section_box.bind("<<ComboboxSelected>>", lambda _: self._refresh_steps())

        editor = ttk.Panedwindow(self, orient="horizontal")
        editor.pack(fill="both", expand=True, padx=8)
        left = ttk.Frame(editor, padding=5)
        right = ttk.Frame(editor, padding=5)
        editor.add(left, weight=1)
        editor.add(right, weight=2)
        ttk.Label(left, text="Шаги сценария").pack(anchor="w")
        self.steps = tk.Listbox(left, exportselection=False)
        self.steps.pack(fill="both", expand=True, pady=5)
        self.steps.bind("<<ListboxSelect>>", lambda _: self._load_step())
        buttons = ttk.Frame(left)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Добавить", command=self._add_step).pack(side="left")
        ttk.Button(buttons, text="Удалить", command=self._remove_step).pack(side="left", padx=4)
        ttk.Button(buttons, text="Вверх", command=lambda: self._move_step(-1)).pack(side="left")
        ttk.Button(buttons, text="Вниз", command=lambda: self._move_step(1)).pack(side="left", padx=4)

        form = ttk.LabelFrame(right, text="Редактор шага", padding=8)
        form.pack(fill="x")
        ttk.Label(form, text="Действие:").grid(row=0, column=0, sticky="w")
        self.action_var = tk.StringVar(value="fill")
        ttk.Combobox(form, textvariable=self.action_var,
                     values=("fill", "type", "check", "uncheck", "click", "select", "press", "wait_visible", "wait_url", "wait", "dom"),
                     width=18).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Label(form, text="Селекторы (по одному на строку):").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.selector_text = tk.Text(form, height=5, width=65)
        self.selector_text.grid(row=1, column=1, sticky="ew", padx=5, pady=(8, 0))
        ttk.Label(form, text="Значение / шаблон:").grid(row=2, column=0, sticky="w", pady=5)
        self.value_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.value_var, width=68).grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        ttk.Label(form, text="DOM operation:").grid(row=3, column=0, sticky="w")
        self.operation_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.operation_var, width=30).grid(row=3, column=1, sticky="w", padx=5)
        ttk.Button(form, text="Применить к шагу", command=self._apply_step).grid(row=4, column=1, sticky="e", pady=8)
        form.columnconfigure(1, weight=1)

        ttk.Label(right, text="Лог процесса").pack(anchor="w", pady=(12, 2))
        self.log = tk.Text(right, height=14, state="disabled", background="#111820", foreground="#d7e2ea")
        self.log.pack(fill="both", expand=True)

    def _reload_scenarios(self) -> None:
        try:
            self.scenarios = load_scenarios()
        except Exception as error:
            messagebox.showerror("Сценарии", str(error))
            return
        names = [str(item.get("name", item["_path"].stem)) for item in self.scenarios]
        self.scenario_box["values"] = names
        if names:
            self.scenario_box.current(0)
            self._select_scenario()

    def _select_scenario(self) -> None:
        index = self.scenario_box.current()
        if index < 0:
            return
        path = Path(self.scenarios[index]["_path"])
        self.current = json.loads(path.read_text(encoding="utf-8"))
        self.current["_path"] = path
        self.url_var.set(self.current.get("url", ""))
        self._refresh_steps()

    def _refresh_steps(self) -> None:
        self.steps.delete(0, "end")
        for index, step in enumerate((self.current or {}).get(self.section_var.get(), []), 1):
            selectors = step.get("selector") or step.get("selectors") or ""
            if isinstance(selectors, list):
                selectors = selectors[0] if selectors else ""
            self.steps.insert("end", f"{index}. {step.get('action', '?')}  {selectors}")
        self.step_index = None

    def _load_step(self) -> None:
        selected = self.steps.curselection()
        if not selected or not self.current:
            return
        self.step_index = selected[0]
        step = self.current[self.section_var.get()][self.step_index]
        self.action_var.set(step.get("action", "fill"))
        selectors = step.get("selectors", step.get("selector", []))
        if isinstance(selectors, str):
            selectors = [selectors]
        self.selector_text.delete("1.0", "end")
        self.selector_text.insert("1.0", "\n".join(selectors or []))
        self.value_var.set(str(step.get("value", step.get("text", step.get("url", "")))))
        self.operation_var.set(str(step.get("operation", "")))

    def _apply_step(self) -> None:
        if self.step_index is None or not self.current:
            return
        step = self.current[self.section_var.get()][self.step_index]
        step["action"] = self.action_var.get().strip()
        selectors = [line.strip() for line in self.selector_text.get("1.0", "end").splitlines() if line.strip()]
        step["selectors"] = selectors
        step.pop("selector", None)
        value = self.value_var.get()
        if step["action"] == "wait_url":
            step["url"] = value
            step.pop("value", None)
        elif step["action"] not in {"check", "uncheck", "click", "wait_visible"}:
            step["value"] = value
        if self.operation_var.get().strip():
            step["operation"] = self.operation_var.get().strip()
        self._refresh_steps()

    def _add_step(self) -> None:
        if self.current is None:
            return
        self.current.setdefault(self.section_var.get(), []).append({"action": "fill", "selectors": [], "value": ""})
        self._refresh_steps()
        self.steps.selection_set(self.steps.size() - 1)
        self._load_step()

    def _remove_step(self) -> None:
        if self.current and self.step_index is not None:
            self.current[self.section_var.get()].pop(self.step_index)
            self._refresh_steps()

    def _move_step(self, direction: int) -> None:
        if not self.current or self.step_index is None:
            return
        target = self.step_index + direction
        steps = self.current[self.section_var.get()]
        if 0 <= target < len(steps):
            steps[self.step_index], steps[target] = steps[target], steps[self.step_index]
            self._refresh_steps()
            self.steps.selection_set(target)
            self._load_step()

    def _save_scenario(self) -> None:
        if not self.current:
            return
        path = Path(self.current["_path"])
        data = {key: value for key, value in self.current.items() if key != "_path"}
        data["url"] = self.url_var.get().strip()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._write_log(f"Saved scenario: {path.name}")

    def _validate_scenario(self) -> None:
        if not self.current:
            return
        if not self.url_var.get().strip():
            messagebox.showerror("Сценарий", "URL сайта не заполнен")
            return
        for section in ("steps", "submit", "success"):
            if not isinstance(self.current.get(section, []), list):
                messagebox.showerror("Сценарий", f"Секция {section} должна быть списком")
                return
        messagebox.showinfo("Сценарий", "Сценарий корректен")

    def _run(self) -> None:
        if self.process and self.process.poll() is None:
            return
        if not self.current:
            messagebox.showwarning("Запуск", "Выберите сценарий")
            return
        try:
            count = int(self.count_var.get())
            if count < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Запуск", "Количество должно быть положительным числом")
            return
        command = [sys.executable, "-m", "universal",
                   "--scenario", str(Path(self.current["_path"])), "--count", str(count),
                   "--mail-service", self.mail_var.get()]
        if self.proxy_var.get():
            command += ["--proxies-file", str(ROOT / "proxies.txt"), "--use-proxies"]
        else:
            command.append("--direct")
        self._write_log("$ " + " ".join(command))
        self.process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1)
        self.run_button["state"] = "disabled"
        self.stop_button["state"] = "normal"
        threading.Thread(target=self._read_process, daemon=True).start()

    def _read_process(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.output_queue.put(line.rstrip())
        self.output_queue.put(f"[GUI] Process exited with code {self.process.wait()}")

    def _poll_output(self) -> None:
        try:
            while True:
                self._write_log(self.output_queue.get_nowait())
        except queue.Empty:
            pass
        if self.process and self.process.poll() is not None:
            self.run_button["state"] = "normal"
            self.stop_button["state"] = "disabled"
        self.after(100, self._poll_output)

    def _stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._write_log("[GUI] Stop requested")

    def _write_log(self, text: str) -> None:
        self.log["state"] = "normal"
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log["state"] = "disabled"


if __name__ == "__main__":
    App().mainloop()
