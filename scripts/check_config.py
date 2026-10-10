"""Vérifie les paramètres locaux sans afficher de secret ni appeler les API."""
import sys

from pydantic import ValidationError

from bot_wumtaaba.config import Settings


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        print("Configuration invalide pour : " + ", ".join(fields), file=sys.stderr)
        return 1
    try:
        settings.validate_live()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Configuration locale valide. Aucun appel Meta ou Google Gemini effectué.")
    print("Prochaine étape : vérifier le webhook HTTPS et effectuer la recette WhatsApp.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
