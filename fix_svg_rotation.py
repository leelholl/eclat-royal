#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redresse des SVG en créant toujours un fichier *_fixed.svg séparé."""

from __future__ import annotations

import argparse
import base64
import math
import mimetypes
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow est requis pour corriger les photos incorporées.
    Image = None
    ImageOps = None

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)
ROTATION_TOLERANCE = 0.8
DATA_IMAGE_RE = re.compile(r"^(data:image/[^;,]+(?:;[^,]*)?;base64,)(.*)$", re.DOTALL | re.IGNORECASE)
TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")


def local_name(tag: str) -> str:
    """Retourne le nom local d'une balise XML, sans son espace de noms."""
    return tag.rsplit("}", 1)[-1]


def angle_cible(angle: float) -> Optional[int]:
    """Reconnaît uniquement les rotations parasites demandées."""
    for cible in (90, -90, 180):
        ecart = (angle - cible + 180) % 360 - 180
        if abs(ecart) <= ROTATION_TOLERANCE:
            return cible
    return None


def parse_nombres(texte: str) -> list[float]:
    return [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", texte)]


def decrire_transform(transform: str) -> list[int]:
    """Détecte rotate() et les matrices de rotation pure, sans modifier le SVG."""
    angles: list[int] = []
    for nom, arguments in TRANSFORM_RE.findall(transform):
        valeurs = parse_nombres(arguments)
        if nom.lower() == "rotate" and valeurs:
            cible = angle_cible(valeurs[0])
            if cible is not None:
                angles.append(cible)
        elif nom.lower() == "matrix" and len(valeurs) == 6:
            a, b, c, d, _e, _f = valeurs
            # Une rotation pure (avec échelle uniforme autorisée) a des axes
            # orthogonaux de même longueur et un déterminant positif.
            sx, sy = math.hypot(a, b), math.hypot(c, d)
            if sx > 1e-9 and abs(sx - sy) <= 1e-3 * max(sx, sy):
                if abs(a * c + b * d) <= 1e-3 * sx * sy and a * d - b * c > 0:
                    cible = angle_cible(math.degrees(math.atan2(b, a)))
                    if cible is not None:
                        angles.append(cible)
    return angles


def nettoyer_transform(transform: str) -> tuple[str, int]:
    """Supprime les rotate() reconnus et neutralise les matrices de rotation pure."""
    nb = 0

    def remplace(match: re.Match[str]) -> str:
        nonlocal nb
        nom, arguments = match.group(1), match.group(2)
        valeurs = parse_nombres(arguments)
        if nom.lower() == "rotate" and valeurs and angle_cible(valeurs[0]) is not None:
            nb += 1
            return ""
        if nom.lower() == "matrix" and len(valeurs) == 6:
            a, b, c, d, e, f = valeurs
            sx, sy = math.hypot(a, b), math.hypot(c, d)
            pure = (sx > 1e-9 and abs(sx - sy) <= 1e-3 * max(sx, sy)
                    and abs(a * c + b * d) <= 1e-3 * sx * sy and a * d - b * c > 0)
            if pure and angle_cible(math.degrees(math.atan2(b, a))) is not None:
                nb += 1
                # Garde l'échelle et la translation déjà présentes dans la matrice.
                return f"matrix({sx:.8g} 0 0 {sx:.8g} {e:.8g} {f:.8g})"
        return match.group(0)

    resultat = TRANSFORM_RE.sub(remplace, transform)
    # Nettoie virgules et espaces laissés par la suppression d'un rotate().
    resultat = re.sub(r"\s*,\s*", " ", resultat)
    resultat = re.sub(r"\s+", " ", resultat).strip(" ,")
    return resultat, nb


def attribut_transform(element: ET.Element) -> Optional[str]:
    # Les attributs XML n'ont généralement pas d'espace de noms, mais on tolère
    # aussi un éventuel préfixe inhabituel.
    for cle, valeur in element.attrib.items():
        if local_name(cle) == "transform":
            return cle
    return None


def extraire_longueur(valeur: Optional[str]) -> Optional[tuple[float, str]]:
    if not valeur:
        return None
    match = re.fullmatch(r"\s*([-+]?(?:\d*\.\d+|\d+\.?\d*))\s*([a-zA-Z%]*)\s*", valeur)
    if not match:
        return None
    return float(match.group(1)), match.group(2)


def formater_longueur(valeur: float, unite: str) -> str:
    nombre = f"{valeur:.8g}"
    return nombre + unite


def corriger_image_exif(element: ET.Element, svg_path: Path) -> tuple[bool, str]:
    """Redresse une image EXIF base64 ou liée localement depuis le SVG."""
    href_key = next((k for k in element.attrib if local_name(k) == "href"), None)
    if href_key is None:
        return False, ""
    source = element.attrib[href_key]
    correspondance = DATA_IMAGE_RE.match(source.strip())
    if Image is None or ImageOps is None:
        url = urlparse(source.strip())
        if correspondance:
            raise RuntimeError("Pillow manque : installez-le avec `pip install Pillow` pour vérifier l’orientation EXIF des images.")
        if url.scheme in ("", "file"):
            chemin = unquote(url.path) if url.scheme == "file" else unquote(source.split("#", 1)[0].split("?", 1)[0])
            candidat = Path(chemin)
            if chemin and not candidat.is_absolute():
                candidat = svg_path.parent / candidat
            if candidat.is_file():
                raise RuntimeError("Pillow manque : installez-le avec `pip install Pillow` pour vérifier l’orientation EXIF des images.")
        return False, ""

    try:
        from io import BytesIO

        chemin_image: Optional[Path] = None
        if correspondance:
            prefixe, donnees = correspondance.groups()
            brut = base64.b64decode(re.sub(r"\s+", "", donnees), validate=False)
        else:
            # Les URL distantes ne sont jamais téléchargées. Seuls les fichiers
            # liés localement au SVG (ou les URI file://) sont examinés.
            url = urlparse(source.strip())
            if url.scheme not in ("", "file"):
                return False, ""
            chemin = unquote(url.path) if url.scheme == "file" else unquote(source.split("#", 1)[0].split("?", 1)[0])
            if not chemin:
                return False, ""
            chemin_image = Path(chemin)
            if not chemin_image.is_absolute():
                chemin_image = svg_path.parent / chemin_image
            if not chemin_image.is_file():
                return False, ""
            brut = chemin_image.read_bytes()
            prefixe = ""

        image = Image.open(BytesIO(brut))
        orientation = image.getexif().get(274, 1)
        if orientation in (None, 1):
            return False, ""

        ancien_format = image.format or "PNG"
        ancienne_taille = image.size
        redressee = ImageOps.exif_transpose(image)
        nouvelle_taille = redressee.size
        sortie = BytesIO()
        options = {"quality": 95} if ancien_format.upper() in ("JPEG", "JPG", "WEBP") else {}
        if ancien_format.upper() == "PNG":
            options["optimize"] = True
        redressee.save(sortie, format=ancien_format, **options)
        donnees_corrigees = base64.b64encode(sortie.getvalue()).decode("ascii")
        if correspondance:
            element.set(href_key, prefixe + donnees_corrigees)
        else:
            # Le SVG *_fixed reste autonome; la photo source liée reste intacte.
            mime = Image.MIME.get(ancien_format.upper()) or mimetypes.guess_type(str(chemin_image))[0] or "image/jpeg"
            element.set(href_key, f"data:{mime};base64,{donnees_corrigees}")

        # Si les dimensions SVG étaient précisément les pixels natifs de l'image,
        # elles doivent suivre l'échange largeur/hauteur de l'orientation EXIF.
        if ancienne_taille != nouvelle_taille:
            largeur = extraire_longueur(element.get("width"))
            hauteur = extraire_longueur(element.get("height"))
            if largeur and hauteur and largeur[1] == hauteur[1] == "" and abs(largeur[0] - ancienne_taille[0]) < 0.01 and abs(hauteur[0] - ancienne_taille[1]) < 0.01:
                element.set("width", formater_longueur(nouvelle_taille[0], largeur[1]))
                element.set("height", formater_longueur(nouvelle_taille[1], hauteur[1]))
        origine = "intégrée" if correspondance else f"liée ({chemin_image.name})"
        return True, f"photo {origine} redressée par EXIF ({ancienne_taille[0]}×{ancienne_taille[1]} → {nouvelle_taille[0]}×{nouvelle_taille[1]})"
    except Exception as exc:
        raise RuntimeError(f"image base64 illisible ou non prise en charge : {exc}") from exc


def corriger_dimensions_incoherentes(root: ET.Element) -> Optional[str]:
    """Détecte un viewBox et un canevas portrait/paysage inversés, puis les aligne."""
    largeur, hauteur = extraire_longueur(root.get("width")), extraire_longueur(root.get("height"))
    viewbox = parse_nombres(root.get("viewBox", ""))
    if not (largeur and hauteur and len(viewbox) == 4):
        return None
    l, lu = largeur
    h, hu = hauteur
    _, _, vb_w, vb_h = viewbox
    if l <= 0 or h <= 0 or vb_w <= 0 or vb_h <= 0:
        return None
    if lu != hu:
        return None
    ratio_page, ratio_vb = l / h, vb_w / vb_h
    # On conserve le viewBox et on échange le canevas uniquement lorsque les
    # orientations portrait/paysage sont clairement opposées.
    if ratio_page > 1.15 and ratio_vb < 0.87 or ratio_page < 0.87 and ratio_vb > 1.15:
        root.set("width", formater_longueur(h, hu))
        root.set("height", formater_longueur(l, lu))
        return f"dimensions width/height échangées ({l:g} × {h:g})"
    return None


def appliquer_rotation_forcee(root: ET.Element, angle: int) -> Optional[str]:
    """Ajoute une rotation manuelle centrée et adapte le canevas pour les quarts de tour."""
    cible = next((element for element in root.iter() if local_name(element.tag) == "g"), root)
    vb = parse_nombres(root.get("viewBox", ""))
    if len(vb) == 4:
        min_x, min_y, vb_w, vb_h = vb
        cx, cy = min_x + vb_w / 2, min_y + vb_h / 2
        rotation = f"rotate({angle} {cx:g} {cy:g})"
    else:
        rotation = f"rotate({angle})"
    cle = attribut_transform(cible)
    ancien = cible.get(cle) if cle else None
    cible.set(cle or "transform", f"{ancien} {rotation}".strip())

    if abs(angle) == 90:
        if len(vb) == 4:
            # Après un quart de tour, la boîte visible échange largeur et hauteur.
            nouveau_x, nouveau_y = cx - vb_h / 2, cy - vb_w / 2
            root.set("viewBox", f"{nouveau_x:.8g} {nouveau_y:.8g} {vb_h:.8g} {vb_w:.8g}")
        largeur, hauteur = extraire_longueur(root.get("width")), extraire_longueur(root.get("height"))
        if largeur and hauteur and largeur[1] == hauteur[1]:
            root.set("width", formater_longueur(hauteur[0], hauteur[1]))
            root.set("height", formater_longueur(largeur[0], largeur[1]))
            return "dimensions du canevas échangées pour le quart de tour"
    return None


def sauvegarder_original(source: Path, dossier_backup: Path) -> Path:
    """Copie l'original avant toute écriture; ne remplace jamais un backup existant."""
    dossier_backup.mkdir(parents=True, exist_ok=True)
    destination = dossier_backup / source.name
    if destination.exists():
        horodatage = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = dossier_backup / f"{source.stem}_{horodatage}{source.suffix}"
    shutil.copy2(source, destination)
    return destination


def analyser_et_corriger(chemin: Path, rotation_forcee: Optional[int]) -> tuple[bytes, list[str]]:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    arbre = ET.parse(chemin, parser=parser)
    root = arbre.getroot()
    if local_name(root.tag) != "svg":
        raise ValueError("la racine XML n'est pas <svg>")
    corrections: list[str] = []

    # Corrige les photos intégrées ou liées localement si l'EXIF indique une rotation.
    for element in root.iter():
        if local_name(element.tag) == "image":
            modifie, description = corriger_image_exif(element, chemin)
            if modifie:
                corrections.append(description)

    if rotation_forcee is not None:
        ajustement = appliquer_rotation_forcee(root, rotation_forcee)
        corrections.append(f"rotation manuelle de {rotation_forcee}° ajoutée")
        if ajustement:
            corrections.append(ajustement)
    else:
        premier_groupe = next((element for element in root.iter() if local_name(element.tag) == "g"), None)
        cibles = [root] + ([premier_groupe] if premier_groupe is not None else [])
        cibles.extend(element for element in root.iter() if local_name(element.tag) == "image")
        # Évite de traiter deux fois le même élément si la structure est atypique.
        cibles = list({id(element): element for element in cibles}.values())
        for element in cibles:
            cle = attribut_transform(element)
            if not cle:
                continue
            transform = element.get(cle, "")
            nettoye, nombre = nettoyer_transform(transform)
            if nombre:
                if nettoye:
                    element.set(cle, nettoye)
                else:
                    del element.attrib[cle]
                corrections.append(f"{nombre} rotation(s) parasite(s) retirée(s) sur <{local_name(element.tag)}>")
        ajustement = corriger_dimensions_incoherentes(root)
        if ajustement:
            corrections.append(ajustement)

    resultat = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return resultat, corrections


def traiter_dossier(dossier: Path, rotation_forcee: Optional[int]) -> int:
    if not dossier.is_dir():
        print(f"Dossier introuvable : {dossier}", file=sys.stderr)
        return 2
    fichiers = sorted(p for p in dossier.glob("*.svg") if not p.stem.lower().endswith("_fixed"))
    if not fichiers:
        print(f"Aucun SVG à traiter dans : {dossier}")
        return 0

    erreurs = 0
    print(f"Dossier : {dossier}\nSVG trouvés : {len(fichiers)}\n")
    for source in fichiers:
        destination = source.with_name(f"{source.stem}_fixed.svg")
        if destination.exists():
            print(f"{source.name} : sortie déjà présente, ignoré ({destination.name})")
            continue
        try:
            backup = sauvegarder_original(source, dossier / "backup")
            contenu, corrections = analyser_et_corriger(source, rotation_forcee)
            destination.write_bytes(contenu)
            if corrections:
                print(f"{source.name} : {', '.join(corrections)} → {destination.name} (original sauvegardé dans backup/{backup.name})")
            else:
                print(f"{source.name} : rien à corriger → {destination.name} (original sauvegardé dans backup/{backup.name})")
        except Exception as exc:
            erreurs += 1
            print(f"{source.name} : ERREUR — {exc}")
    return 1 if erreurs else 0


def main() -> int:
    dossier_defaut = Path(__file__).resolve().parent / "output" / "image"
    parser = argparse.ArgumentParser(description="Corrige les rotations de SVG sans écraser les originaux.")
    parser.add_argument("--dossier", type=Path, default=dossier_defaut, help=f"Dossier à traiter (défaut : {dossier_defaut})")
    parser.add_argument("--rotation", type=int, choices=(90, -90, 180), help="Ajoute manuellement cette rotation (positif = sens horaire en SVG) et désactive la détection automatique des transforms.")
    arguments = parser.parse_args()
    return traiter_dossier(arguments.dossier.expanduser().resolve(), arguments.rotation)


if __name__ == "__main__":
    raise SystemExit(main())
