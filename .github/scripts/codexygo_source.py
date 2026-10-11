"""French text from CodexYGO's public API, after printed-code identity verification."""
from __future__ import annotations
import json
import re
import unicodedata
import urllib.parse

API = "https://codexygo.fr/api/card/v1/search/"


def identity(value: str, rush: bool) -> str:
    value = unicodedata.normalize("NFKC", urllib.parse.unquote(value)).replace("_", " ").strip()
    if rush:
        value = re.sub(r"\s+\(Rush Duel\)$", "", value, flags=re.I)
    return value.casefold()


def format_french(card: dict, title: str, params: dict, rush: bool):
    from card_text_autofill import CardText, format_yugipedia_card, normalize_rush_layout
    translations = card.get("translations", {})
    if not isinstance(translations, dict):
        return None
    fr, en = translations.get("fr", {}), translations.get("en", {})
    if not isinstance(fr, dict) or not isinstance(en, dict) or not isinstance(fr.get("name"), str) or not fr["name"].strip():
        return None
    for translation in (fr, en):
        for field in ("name", "description", "limitationText", "pendulumEffect", "rushOtherEffects", "rushCondition", "rushEffect", "fullText"):
            if field in translation and not isinstance(translation[field], str):
                return None
        choices = translation.get("rushChoiceEffects", [])
        if not isinstance(choices, list) or any(not isinstance(item, str) or not item.strip() for item in choices):
            return None
    # Reject partial translations, including material lines, costs and choices.
    fields = ("description", "limitationText", "rushOtherEffects", "rushCondition", "rushEffect", "rushChoiceEffects") if rush else ("description", "limitationText", "pendulumEffect")
    for field in fields:
        if en.get(field) and not fr.get(field):
            return None
    if rush:
        if params.get("requirement") and not fr.get("rushCondition"):
            return None
        if (params.get("materials") or params.get("condition")) and not fr.get("rushOtherEffects"):
            return None
        if card.get("rushEffectType") == "choice":
            choices = fr.get("rushChoiceEffects", [])
            if not choices or (en.get("rushChoiceEffects") and len(choices) != len(en["rushChoiceEffects"])):
                return None
        desc = fr.get("fullText", "").strip()
        if not desc or not (fr.get("rushEffect") or fr.get("rushChoiceEffects") or fr.get("description")):
            return None
        maximum = card.get("atkMax") or params.get("maximum_atk")
        if maximum and not re.search(r"maximum|max(?:imum)? atk", desc, re.I):
            desc = f"ATK Maximum {maximum}\n" + desc
        text = CardText(fr["name"].strip(), normalize_rush_layout(desc, "fr"), "")
    else:
        translated = dict(params)
        translated.update(fr_name=fr["name"], fr_text=fr.get("description", ""),
                          fr_pendulum_effect=fr.get("pendulumEffect", ""))
        if fr.get("limitationText"):
            translated["fr_text"] = fr["limitationText"].strip() + "\n" + translated["fr_text"]
        text = format_yugipedia_card(title, translated, "fr")
        if text is None:
            return None
    source = API + "?" + urllib.parse.urlencode({"codexygo_ids": card["codexygoId"], "languages": "fr"})
    return CardText(text.name, text.desc, source)


def fetch_french(title: str, params: dict, rush: bool, request, log):
    """Caller has already verified this Yugipedia page contains the printed code."""
    wanted = identity(title, rush)
    query = {"search": re.sub(r"\s+\(Rush Duel\)$", "", title).replace("_", " "),
             "search_type": "name", "format": "rush" if rush else "master", "languages": "en,fr",
             "page_size": 100, "include_images": "false", "include_meta_translations": "false", "include_errata": "false"}
    matches = {}
    try:
        for page in range(1, 11):
            query["page"] = page
            response = json.loads(request(API + "?" + urllib.parse.urlencode(query)))
            if not isinstance(response, dict):
                raise ValueError("Unexpected CodexYGO API response")
            result = response.get("result", response)
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ValueError("Unexpected CodexYGO API response")
            for card in result["data"]:
                if not isinstance(card, dict) or not isinstance(card.get("yugipediaSlug"), str) or not isinstance(card.get("codexygoId"), int):
                    continue
                if bool(card.get("rush", False)) == rush and identity(card.get("yugipediaSlug", ""), rush) == wanted:
                    matches[card.get("codexygoId")] = card
            pages = int(result.get("pagination", {}).get("totalPages", 1))
            if pages > 10:
                raise ValueError("CodexYGO search is too broad to verify safely")
            if page >= pages:
                break
        if len(matches) != 1:
            log(f"[SourceText] CodexYGO: {len(matches)} exact identities for {title}; skipped")
            return None
        card = format_french(next(iter(matches.values())), title, params, rush)
        if card:
            log(f"[SourceText] CodexYGO French text verified: {title}")
        return card
    except (OSError, ValueError, TypeError, KeyError) as error:
        log(f"[SourceText] CodexYGO lookup failed for {title}: {error}")
        return None
