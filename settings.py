"""Configuración de los modelos remotos mediante valores literales en .env."""

import os
from pathlib import Path
import re


DEFAULT_ENV = Path(__file__).with_name(".env")
MODEL_SETTINGS = frozenset({
    "MODEL_API_KEY",
    "MODEL_CA_FILE",
    "SUPERVISOR_API_URL",
    "SUPERVISOR_API_KEY",
    "SUPERVISOR_MODEL",
    "LAYA_API_URL",
    "LAYA_API_KEY",
    "LAYA_MODEL",
    "LAYA_API_FORMAT",
})


def load_settings(path=None, environ=None):
    """Lee únicamente las opciones del bot. Las variables del proceso tienen prioridad."""
    source = DEFAULT_ENV if path is None else Path(path)
    env = os.environ if environ is None else environ
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    except UnicodeDecodeError:
        raise ValueError("Guarda .env con codificación UTF-8.") from None

    values = {}
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assignment = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)", line)
        if not assignment:
            raise ValueError(f"Revisa el formato de .env en la línea {lineno}.")
        name, value = assignment.groups()
        if name not in MODEL_SETTINGS:
            continue
        if name in values:
            raise ValueError(f"Opción duplicada en .env, línea {lineno}.")
        if value.startswith(('"', "'")):
            quoted = re.fullmatch(r"""(["'])(.*?)\1\s*(?:#.*)?""", value)
            if not quoted:
                raise ValueError(f"Revisa las comillas de .env en la línea {lineno}.")
            value = quoted[2]
        values[name] = value

    values.update({key: env[key] for key in MODEL_SETTINGS if key in env})
    return values
