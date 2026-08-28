#!/usr/bin/env python3
"""Curated fixes for obvious machine-translation errors in common UI strings."""
import json
from pathlib import Path

I18N = Path(__file__).resolve().parents[1] / "frontend" / "src" / "assets" / "i18n"

FIXES = {
    "fr": {
        "common.back": "Retour",
        "common.on": "Activé",
        "common.off": "Désactivé",
        "common.online": "En ligne",
        "common.offline": "Hors ligne",
        "common.save": "Enregistrer",
        "common.got_it": "Compris",
        "common.close": "Fermer",
        "common.retry": "Réessayer",
        "common.done": "Terminé",
        "common.testing": "Test en cours…",
        "common.saving": "Enregistrement…",
        "common.try_again": "Réessayer",
        "common.continue": "Continuer",
        "common.cancel": "Annuler",
        "common.easier_setup": "Configuration simplifiée",
    },
    "es": {
        "common.save": "Guardar",
        "common.retry": "Reintentar",
        "common.close": "Cerrar",
        "common.online": "En línea",
        "common.offline": "Sin conexión",
        "common.on": "Activado",
        "common.off": "Desactivado",
        "common.saving": "Guardando…",
        "common.testing": "Probando…",
        "common.got_it": "Entendido",
        "common.suggested": "Sugerido",
        "common.try_again": "Intentar de nuevo",
        "common.continue": "Continuar",
        "common.cancel": "Cancelar",
        "common.easier_setup": "Configuración más sencilla",
    },
    "de": {
        "common.got_it": "Verstanden",
        "common.continue": "Weiter",
        "common.cancel": "Abbrechen",
        "common.try_again": "Erneut versuchen",
        "common.saving": "Speichern…",
        "common.advanced": "Erweitert",
        "common.off": "Aus",
        "common.online": "Online",
        "common.offline": "Offline",
    },
}


def set_path(data: dict, path: str, value: str) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def main() -> None:
    for lang, fixes in FIXES.items():
        path = I18N / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in fixes.items():
            set_path(data, key, value)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"fixed {lang}.json ({len(fixes)} strings)")


if __name__ == "__main__":
    main()
