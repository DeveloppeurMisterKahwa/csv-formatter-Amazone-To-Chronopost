"""
Convertit un rapport de commandes Amazon (TSV) en fichier d'import
multi-colis Chronopost (CSV séparé par des points-virgules).

Usage :
    python Formater.py commandes_amazon.txt [sortie.csv]
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

# --- Configuration ----------------------------------------------------------

# Codes produit Chronopost (voir l'annexe page 3 de la documentation).
PRODUIT_PARTICULIER_FRANCE = "9C"  # Chrono 18
PRODUIT_PRO_FRANCE = "1S"          # Chrono 13 (Instance agence)
PRODUIT_ETRANGER = "9F"            # Chrono Classic

# Poids unitaire en kg (emballage compris) par SKU Amazon.
# Poids du colis = somme(poids unitaire x quantité).
POIDS_PAR_SKU = {
    # "XQ-5VT4-Q90B": 0.80,
}
POIDS_DEFAUT_KG = 1.0

TYPE_PARTICULIER = "Par"
TYPE_PROFESSIONNEL = "Pro"

# E-mail mis sur tous les colis à la place de l'adresse anonyme Amazon du client.
# Laisser vide ("") pour garder l'adresse Amazon.
EMAIL_DESTINATAIRE = "mister.kahwa@gmail.com"

# Indicatif téléphonique international par code pays (ajouter les pays manquants).
INDICATIFS = {
    "FR": "33", "BE": "32", "LU": "352", "MC": "377", "CH": "41",
    "ES": "34", "PT": "351", "IT": "39", "DE": "49", "AT": "43",
    "NL": "31", "IE": "353", "GB": "44", "PL": "48", "SE": "46", "DK": "45",
}

# Colonnes du fichier Chronopost, dans l'ordre, avec leur longueur maximum.
# Les en-têtes sont recopiés tels quels depuis import_multicolis_exemple.csv.
COLONNES = [
    ("Référence", 15),
    ("Nom", 50),
    ("Prénom", 50),
    ("Adresse", 38),
    ("Suite adresse", 38),
    ("Code porte", 38),
    ("Code pays", 2),
    ("Code Postal", 9),
    ("Ville", 50),
    ("Téléphone", 17),
    ("E-mail", 80),
    ("Type", 3),
    ("Raison sociale", 50),
    ("Date Envoi", 10),
    ("Poids", 5),
    ("Produit", 2),
    ("Point relais", 5),
    ("Livraison le Samedi", 3),
    ("Document / Marchandise", 3),
    ("Longueur", 6),
    ("Largeur", 6),
    ("Hauteur", 6),
    ("Nom Envoi", 35),
    ("Description contenu", 2),
    (" Valeur Douane", 5),
    (" Valeur Assurance", 5),
    ("Incoterm", 3),
]


# --- Lecture du rapport Amazon ----------------------------------------------

def lire_texte(chemin):
    """Lit le fichier en UTF-8, ou en Windows-1252 pour les anciens rapports."""
    try:
        return chemin.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return chemin.read_text(encoding="cp1252")


def lire_commandes(chemin):
    """Regroupe les lignes du TSV par numéro de commande (un colis par commande)."""
    lecteur = csv.DictReader(
        lire_texte(chemin).splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE 
    )
    commandes = {}
    for ligne in lecteur:
        if int(ligne["quantity-to-ship"] or 0) <= 0:
            continue
        commandes.setdefault(ligne["order-id"], []).append(ligne)
    return commandes


# --- Conversion -------------------------------------------------------------

def separer_societe(destinataire: str, acheteur: str) -> tuple[str, str]:
    """Pour une commande pro, sépare le destinataire en (raison sociale, personne).

    Amazon y met la société suivie du nom de l'acheteur, par exemple
    "SUMINISTROS ADARRA Manu Maya" avec l'acheteur "Manu Maya".
    Sinon, le destinataire complet sert de raison sociale et de personne.
    """
    mots = destinataire.split()
    mots_acheteur = acheteur.lower().split()
    n = len(mots_acheteur)
    if 0 < n < len(mots) and [m.lower() for m in mots[-n:]] == mots_acheteur:
        return " ".join(mots[:-n]), " ".join(mots[-n:])
    return destinataire, destinataire


def separer_nom_prenom(nom_complet: str, nom_acheteur: str) -> tuple[str, str]:
    """Découpe le champ 'recipient-name' d'Amazon en (nom, prénom).

    Amazon ne fournit qu'un seul champ, saisi librement par le client,
    donc l'ordre des mots n'est pas fiable. Le 'buyer-name' (titulaire du
    compte) aide quand il ne contient qu'un mot. Exemples réels du rapport :
        "Léa Dibetta"      / acheteur "Dibetta"       -> ("Dibetta", "Léa")
        "Jessica DAVID"    / acheteur "DAVID Jessica" -> ("DAVID", "Jessica")
        "Boyer christophe" / acheteur "SAS DEFABO"    -> ("Boyer", "christophe")
        "DOLVIKA"          / acheteur "dolvika"       -> ("DOLVIKA", "DOLVIKA")

    Nom et Prénom sont obligatoires chez Chronopost : ne jamais renvoyer
    de chaîne vide.
    """
    mots = nom_complet.split()
    if len(mots) < 2:
        # Un seul mot (souvent une société) : Prénom est obligatoire, on le duplique.
        return nom_complet, nom_complet
    minuscules = [m.lower() for m in mots]
    mots_acheteur = nom_acheteur.lower().split()
    if len(mots_acheteur) == 1 and mots_acheteur[0] in minuscules:
        # Acheteur inscrit avec un seul mot : c'est le nom de famille.
        i = minuscules.index(mots_acheteur[0])
        return mots[i], " ".join(mots[:i] + mots[i + 1:])
    majuscules = [m for m in mots if m.isupper()]
    if majuscules and len(majuscules) < len(mots):
        # Les mots en MAJUSCULES forment le nom ("Jessica DAVID", "AYDIN Osman").
        autres = [m for m in mots if not m.isupper()]
        return " ".join(majuscules), " ".join(autres)
    # Sinon, habitude française "Nom Prénom" : le premier mot est le nom.
    return mots[0], " ".join(mots[1:])


def formater_telephone(telephone, pays):
    """Met le numéro au format international selon le pays de livraison.

        "06 84 95 09 62" (FR) -> "+33684950962"
        "618480219"      (ES) -> "+34618480219"
        "0033684950962"  (FR) -> "+33684950962"
    """
    numero = "".join(c for c in telephone if c.isdigit() or c == "+")
    if not numero or numero.startswith("+"):
        return numero
    if numero.startswith("00"):
        return "+" + numero[2:]
    indicatif = INDICATIFS.get(pays)
    if not indicatif:
        return numero  # Pays absent de INDICATIFS : numéro laissé tel quel.
    if numero.startswith(indicatif) and len(numero) > 10:
        return "+" + numero  # Indicatif déjà présent, sans le + ("33684950962").
    if numero.startswith("0") and pays != "IT":
        numero = numero[1:]  # Le 0 national disparaît ; en Italie, il fait partie du numéro.
    return "+" + indicatif + numero


def calculer_poids(lignes, skus_inconnus):
    poids = 0.0
    for ligne in lignes:
        sku = ligne["sku"]
        if sku not in POIDS_PAR_SKU:
            skus_inconnus.add(sku)
        poids += POIDS_PAR_SKU.get(sku, POIDS_DEFAUT_KG) * int(ligne["quantity-to-ship"])
    return f"{poids:.2f}"


def choisir_produit(pays, est_pro):
    """Chrono Classic à l'étranger ; en France, selon le type de destinataire."""
    if pays != "FR":
        return PRODUIT_ETRANGER
    return PRODUIT_PRO_FRANCE if est_pro else PRODUIT_PARTICULIER_FRANCE


def commande_vers_colis(lignes, date_envoi, skus_inconnus):
    """Construit une ligne Chronopost. Les colonnes absentes resteront vides."""
    ligne = lignes[0]
    pays = ligne["ship-country"].strip().upper() or "FR"
    est_pro = ligne["is-business-order"].strip().lower() == "true"
    destinataire = ligne["recipient-name"].strip()
    acheteur = ligne["buyer-name"].strip()
    # Raison sociale obligatoire pour un pro : Amazon la place dans recipient-name.
    raison_sociale = ""
    if est_pro:
        raison_sociale, destinataire = separer_societe(destinataire, acheteur)
    nom, prenom = separer_nom_prenom(destinataire, acheteur)
    suite_adresse = " ".join(
        a.strip() for a in (ligne["ship-address-2"], ligne["ship-address-3"]) if a.strip()
    )

    colis = {
        "Nom": nom,
        "Prénom": prenom,
        "Adresse": ligne["ship-address-1"].strip(),
        "Suite adresse": suite_adresse,
        "Code pays": pays,
        "Code Postal": ligne["ship-postal-code"].strip(),
        "Ville": ligne["ship-city"].strip(),
        "Téléphone": formater_telephone(ligne["buyer-phone-number"], pays),
        "E-mail": EMAIL_DESTINATAIRE or ligne["buyer-email"].strip(),
        "Type": TYPE_PROFESSIONNEL if est_pro else TYPE_PARTICULIER,
        "Raison sociale": raison_sociale,
        "Date Envoi": date_envoi,
        "Poids": calculer_poids(lignes, skus_inconnus),
        "Produit": choisir_produit(pays, est_pro),
        "Nom Envoi": ligne["order-id"],
    }
    return colis


def vers_ligne_csv(colis, order_id):
    """Ordonne les valeurs selon COLONNES et tronque à la longueur maximum."""
    valeurs = []
    for entete, longueur_max in COLONNES:
        valeur = colis.get(entete, "")
        if len(valeur) > longueur_max:
            print(
                f"Attention {order_id} : '{entete.strip()}' tronqué à {longueur_max} "
                f"caractères ({valeur!r})",
                file=sys.stderr,
            )
            valeur = valeur[:longueur_max]
        valeurs.append(valeur)
    return valeurs


# --- Programme principal ----------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convertit un rapport de commandes Amazon (TSV) "
        "en import multi-colis Chronopost (CSV)."
    )
    parser.add_argument("entree", type=Path, help="fichier TSV exporté depuis Amazon")
    parser.add_argument(
        "sortie", type=Path, nargs="?",
        help="fichier CSV à générer (défaut : <entree>_chronopost.csv)",
    )
    args = parser.parse_args()
    sortie = args.sortie or args.entree.with_name(f"{args.entree.stem}_chronopost.csv")

    commandes = lire_commandes(args.entree)
    date_envoi = date.today().strftime("%d/%m/%Y")
    skus_inconnus = set()

    # Chronopost attend du Windows-1252 (comme le fichier exemple) ;
    # les rares caractères non représentables sont remplacés par '?'.
    with open(sortie, "w", encoding="cp1252", errors="replace", newline="") as f:
        writer = csv.writer(f, delimiter=";", lineterminator="\n")
        writer.writerow(entete for entete, _ in COLONNES)
        for order_id, lignes in commandes.items():
            colis = commande_vers_colis(lignes, date_envoi, skus_inconnus)
            writer.writerow(vers_ligne_csv(colis, order_id))

    if skus_inconnus:
        print(
            f"Poids par défaut ({POIDS_DEFAUT_KG} kg) utilisé pour les SKU : "
            + ", ".join(sorted(skus_inconnus)),
            file=sys.stderr,
        )
    print(f"{len(commandes)} colis écrits dans {sortie}")


if __name__ == "__main__":
    main()
