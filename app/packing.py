from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class PackingSku:
    code: str
    name: str
    box_rate: float
    labor_rate: float


class PackingCatalog:
    _BOX_CODE_MAP = {
        "1.5": "1.5",
        "3.0": "3.0",
        "4.5": "4.5",
        "6.0": "6.0",
        "wardrobe": "wardrobe",
        "flat screen tv": "tv",
        "tv": "tv",
        "mirror": "mirror",
    }

    def __init__(self, *, tsv_path: str | Path, json_config: dict):
        self._path = Path(tsv_path)
        self._skus: Dict[str, PackingSku] = {}
        if self._path.exists():
            self._parse_tsv(self._path)
        self._load_from_json(json_config)

    def _parse_tsv(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line and "..." not in line]
        if not lines:
            return
        header = lines[0]
        for line in lines[1:]:
            parts = re.split(r"(?<=\d)(?=[A-Za-z])", line)
            if not parts:
                continue
            name_part = parts[0].strip()
            numeric = re.findall(r"[0-9]+(?:\.[0-9]+)?", line)
            if len(numeric) < 3:
                continue
            box_rate = float(numeric[-2])
            labor_rate = float(numeric[-1])
            name = name_part.strip()
            code = self._derive_code(name)
            if not code:
                continue
            self._skus[code] = PackingSku(code=code, name=name, box_rate=box_rate, labor_rate=labor_rate)

    def _load_from_json(self, config: dict) -> None:
        purchase = config.get("boxAndPackingCosts", {}).get("purchase", [])
        for entry in purchase:
            name = entry["boxType"]
            code = self._derive_code(name)
            if not code:
                continue
            self._skus[code] = PackingSku(
                code=code,
                name=name,
                box_rate=float(entry.get("boxRate", 0.0)),
                labor_rate=float(entry.get("laborRate", 0.0)),
            )
        rental = config.get("boxAndPackingCosts", {}).get("rental", [])
        for entry in rental:
            name = entry["boxType"]
            code = self._derive_code(name)
            if not code:
                continue
            self._skus.setdefault(
                code,
                PackingSku(
                    code=code,
                    name=name,
                    box_rate=float(entry.get("rentalRate", 0.0)),
                    labor_rate=float(entry.get("laborRate", 0.0)),
                ),
            )

    def _derive_code(self, name: str) -> Optional[str]:
        lowered = name.lower()
        for key, code in self._BOX_CODE_MAP.items():
            if key in lowered:
                return code
        digits = re.findall(r"[0-9]+(?:\.[0-9]+)?", lowered)
        if digits:
            return digits[0]
        return None

    def get(self, code: str) -> Optional[PackingSku]:
        return self._skus.get(code)

    @property
    def skus(self) -> Dict[str, PackingSku]:
        return dict(self._skus)
