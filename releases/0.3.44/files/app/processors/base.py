from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProcessorResult:
    ok: bool
    message: str
    data: dict[str, Any] | None = None


class Processor:
    id = "base"
    name = "Processador base"
    description = "Contrato base para processadores do Nserver."

    def __init__(self, root: Path):
        self.root = root
        self.media_root = root / "midias"
        self.media_root.mkdir(parents=True, exist_ok=True)

    def run(self, payload: dict[str, Any]) -> ProcessorResult:
        raise NotImplementedError
