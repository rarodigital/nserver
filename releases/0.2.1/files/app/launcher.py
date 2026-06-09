from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from updater import Updater

APP_VERSION = "0.2.1"
ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("NSERVER_PORT", "8791"))
URL = f"http://127.0.0.1:{PORT}"


class ConsoleLauncher:
    def log(self, text: str):
        print(text, flush=True)

    def set_status(self, text: str):
        self.log(text)

    def run(self):
        run_flow(self)


def run_flow(ui):
    updater = Updater(ROOT, APP_VERSION)
    ui.set_status("Verificando atualizações...")
    try:
        check = updater.check()
        if check.ok and check.update_available:
            ui.log(f"Nova versão disponível: {check.current_version} -> {check.latest_version}")
            ui.log("Criando backup e aplicando atualização...")
            result = updater.apply(check.manifest)
            ui.log(result.get("message", "Atualização aplicada."))
            ui.log("Reabrindo launcher atualizado...")
            time.sleep(1.2)
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return
        if check.ok:
            ui.log("Nserver já está atualizado.")
        else:
            ui.log("Não consegui verificar updates agora: " + check.message)
    except Exception as exc:
        ui.log("Update ignorado por falha segura: " + str(exc))

    ui.set_status("Iniciando servidor local...")
    env = os.environ.copy()
    env.setdefault("NSERVER_HOST", "0.0.0.0")
    env.setdefault("NSERVER_PORT", str(PORT))
    proc = subprocess.Popen([sys.executable, str(ROOT / "app" / "server.py")], cwd=str(ROOT), env=env)
    time.sleep(1.5)
    ui.log(f"Abrindo navegador: {URL}")
    webbrowser.open(URL)
    ui.set_status("Nserver online. Pode manter esta janela aberta.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


def run_tk():
    import tkinter as tk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("Nserver")
    root.geometry("680x430")
    root.configure(bg="#090b10")

    title = tk.Label(root, text="Nserver", font=("Segoe UI", 24, "bold"), fg="#f8fafc", bg="#090b10")
    title.pack(pady=(22, 4))
    subtitle = tk.Label(root, text="Launcher • atualização automática • servidor local", font=("Segoe UI", 11), fg="#94a3b8", bg="#090b10")
    subtitle.pack(pady=(0, 14))
    status = tk.Label(root, text="Preparando...", font=("Segoe UI", 12, "bold"), fg="#bbf7d0", bg="#111827", padx=14, pady=10)
    status.pack(fill="x", padx=22)
    logbox = ScrolledText(root, height=12, bg="#05060a", fg="#dbeafe", insertbackground="#dbeafe", relief="flat", font=("Consolas", 10))
    logbox.pack(fill="both", expand=True, padx=22, pady=16)

    buttons = tk.Frame(root, bg="#090b10")
    buttons.pack(fill="x", padx=22, pady=(0, 16))

    def open_browser():
        webbrowser.open(URL)

    def close_app():
        root.destroy()
        os._exit(0)

    tk.Button(buttons, text="Abrir painel", command=open_browser, bg="#5b7cfa", fg="white", relief="flat", padx=14, pady=8).pack(side="left")
    tk.Button(buttons, text="Fechar", command=close_app, bg="#1f2937", fg="white", relief="flat", padx=14, pady=8).pack(side="right")

    class TkUi:
        def log(self, text: str):
            def write():
                logbox.insert("end", time.strftime("%H:%M:%S") + "  " + text + "\n")
                logbox.see("end")
            root.after(0, write)

        def set_status(self, text: str):
            root.after(0, lambda: status.config(text=text))
            self.log(text)

    threading.Thread(target=run_flow, args=(TkUi(),), daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    try:
        run_tk()
    except Exception as exc:
        print("Launcher visual indisponível, usando modo console:", exc)
        ConsoleLauncher().run()
