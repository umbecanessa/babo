#!/usr/bin/env python3
"""Systematic i18n quality fixes: tone, known MT errors, placeholder validation."""
from __future__ import annotations

import json
import re
from pathlib import Path

I18N = Path(__file__).resolve().parents[1] / "frontend" / "src" / "assets" / "i18n"
PH = re.compile(r"\{\{[^}]+\}\}")

# Exact string replacements per locale (applied after tone rules)
EXACT: dict[str, dict[str, str]] = {
    "fr": {
        "Content de te revoir": "Bon retour",
        "Pensée": "Réflexion",
        "Caractéristiques": "Fonctions",
        "sans sous-marin": "sans abonnement",
        "Auto suit votre appareil": "Auto suit ton appareil",
        "Votre agent": "Ton agent",
        "Vos agents": "Tes agents",
        "votre compte": "ton compte",
        "vous ": "tu ",
        "Vous ": "Tu ",
        "Installez d'abord": "Installe d'abord",
        "Vous configurerez": "Tu configureras",
        "vous connecterez": "tu te connecteras",
        "Vous pouvez": "Tu peux",
        "Vous êtes prêt": "Tu es prêt",
        "Vous terminerez": "Tu finiras",
        "Vous utilisez": "Tu utilises",
        "choisissez": "choisis",
        "Choisissez": "Choisis",
        "Configurez": "Configure",
        "Abonnez-vous": "Abonne-toi",
        "Connectez-vous": "Connecte-toi",
        "Terminez": "Termine",
        "Installez": "Installe",
    },
    "es": {
        "Su agente": "Tu agente",
        "Sus agentes": "Tus agentes",
        "su cuenta": "tu cuenta",
        "Su computadora": "Tu equipo",
        "su computadora": "tu equipo",
        "esta computadora": "este equipo",
        "Esta computadora": "Este equipo",
        "Instale ": "Instala ",
        "instale ": "instala ",
        "Configurará": "Configurarás",
        "iniciará sesión": "iniciarás sesión",
        "ya tengo una cuenta": "Ya tengo una cuenta",
        "Ahorrar": "Guardar",
        "Cerca": "Cerrar",
        "Utilice ": "Usa ",
        "Utilice la": "Usa la",
        "Elija ": "Elige ",
        "elija ": "elige ",
        "Ahorro…": "Guardando…",
        "Su casa actual": "Tu Inicio actual",
        "sucursal": "rama",
        "subscripción": "suscripción",
        "usted escribe": "escribes",
        "Su agente aún": "Tu agente aún",
        "Su clave API": "Tu clave API",
        "Su servidor": "Tu servidor",
    },
    "de": {
        "Ihr Agent": "Dein Agent",
        "Ihre Agenten": "Deine Agenten",
        "Ihr Konto": "Dein Konto",
        "Ihre API": "Deine API",
        "Ihr Computer": "Dein Computer",
        "Ihrem Gerät": "deinem Gerät",
        "Ihren ": "deinen ",
        "Ihrer ": "deiner ",
        "Ihrem ": "deinem ",
        "Ihre ": "Deine ",
        "Ihr ": "Dein ",
        "Installieren Sie": "Installiere",
        "Wählen Sie": "Wähle",
        "Geben Sie": "Gib",
        "Melden Sie sich": "Melde dich",
        "Schließen Sie": "Schließe",
        "Fahren Sie": "Fahre",
        "Erstellen Sie": "Erstelle",
        "Verwenden Sie": "Verwende",
        "Tippen Sie": "Tippe",
        "Überprüfen Sie": "Überprüfe",
        "Warten Sie": "Warte",
        "Befolgen Sie": "Folge",
        "Laden Sie": "Lade",
        "Starten Sie": "Starte",
        "Öffnen Sie": "Öffne",
        "Finden Sie": "Finde",
        "Benennen Sie": "Benenne",
        "Weitermachen": "Weiter",
        "Merkmale": "Funktionen",
        "Erinnerung": "Speicher",
        "Vorschriften": "Regulationen",
        "Wendet sich": "Züge",
        "Gebäude...": "Wird aufgebaut…",
        "Fortschrittlich": "Erweitert",
        "Stornieren": "Abbrechen",
        "Versuchen Sie es erneut": "Erneut versuchen",
        "Sie können": "Du kannst",
        "Sie konfigurieren": "Du konfigurierst",
        "Sie sind bereit": "Du bist bereit",
        "Nun sind Sie bereit": "Alles bereit",
        "damit wir Ihnen": "damit wir dir",
        "wenn Sie abwesend": "wenn du weg bist",
        "in der Sie schreiben": "in der du schreibst",
        "anmelden": "Anmelden",
        "Denken": "Denkweise",
    },
}

# Keys where exact substring replace is unsafe — skip tone blanket rules
SKIP_SUBSTR = ("apiKeys.", "curl ", "from openai", "Authorization:")


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = str(v)
    return out


def unflatten(flat: dict[str, str]) -> dict:
    root: dict = {}
    for path, value in flat.items():
        parts = path.split(".")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return root


def apply_exact(text: str, rules: dict[str, str]) -> str:
    out = text
    # Longer keys first to avoid partial clobber
    for src, dst in sorted(rules.items(), key=lambda x: -len(x[0])):
        out = out.replace(src, dst)
    return out


def validate_placeholders(en_flat: dict[str, str], loc_flat: dict[str, str], lang: str) -> list[str]:
    bad = []
    for key, en_val in en_flat.items():
        if key not in loc_flat:
            continue
        en_ph = sorted(PH.findall(en_val))
        loc_ph = sorted(PH.findall(loc_flat[key]))
        if en_ph != loc_ph:
            bad.append(key)
    if bad:
        print(f"  {lang}: {len(bad)} placeholder mismatches")
    return bad


def process_lang(lang: str, en_flat: dict[str, str]) -> int:
    path = I18N / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    flat = flatten(data)
    rules = EXACT.get(lang, {})
    changed = 0
    for key, value in flat.items():
        if any(s in key for s in SKIP_SUBSTR) or any(s in value for s in ("curl ", "from openai")):
            continue
        new_val = apply_exact(value, rules)
        if new_val != value:
            flat[key] = new_val
            changed += 1
    path.write_text(json.dumps(unflatten(flat), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_placeholders(en_flat, flat, lang)
    print(f"{lang}: {changed} strings updated")
    return changed


def main() -> None:
    en_flat = flatten(json.loads((I18N / "en.json").read_text(encoding="utf-8")))
    total = 0
    for lang in ("fr", "es", "de"):
        total += process_lang(lang, en_flat)
    print(f"total changes: {total}")


if __name__ == "__main__":
    main()
