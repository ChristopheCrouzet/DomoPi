#!/usr/bin/env python3
"""Génère le jeu d'icônes DomoPi — géométrie calculée, centrée sur (24,24).

Style : filaire détaillé, traits principaux 2.2, détails 1.5,
palette ambre #e8a13c / gris #8b97a5 / blanc cassé #dfe6ee.
"""
import os

A = "#e8a13c"   # ambre (état actif, accents)
G = "#8b97a5"   # gris (détails, état inactif)
W = "#dfe6ee"   # blanc cassé (structures)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "icons")

HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round">')


def w(name, body):
    # newline="\n" : sortie identique sous Windows et Linux (pas de CRLF,
    # sinon chaque régénération sous Windows fait un faux diff git complet)
    with open(os.path.join(OUT, name + ".svg"), "w", newline="\n") as f:
        f.write(HEAD + body + "</svg>\n")


def p(d, stroke, sw=2.2, fill="none", fo=None, extra=""):
    fo = f' fill-opacity="{fo}"' if fo is not None else ""
    return f'<path d="{d}" stroke="{stroke}" stroke-width="{sw}" fill="{fill}"{fo}{extra}/>'


def c(cx, cy, r, stroke, sw=2.2, fill="none", fo=None):
    fo = f' fill-opacity="{fo}"' if fo is not None else ""
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" stroke="{stroke}" stroke-width="{sw}" fill="{fill}"{fo}/>'


def r_(x, y, wd, h, rx, stroke, sw=2.2, fill="none", fo=None):
    fo = f' fill-opacity="{fo}"' if fo is not None else ""
    return (f'<rect x="{x}" y="{y}" width="{wd}" height="{h}" rx="{rx}" '
            f'stroke="{stroke}" stroke-width="{sw}" fill="{fill}"{fo}/>')


# ---------------------------------------------------------------- ampoules
BULB = (p("M20 26.5 h8", W, 2) + p("M19.8 29.5 h8.4 M20.4 32.3 h7.2", G, 1.6)
        + p("M22.4 35 h3.2", G, 1.8))
w("ampoule_on",
  c(24, 17, 9.6, A, 2.2, A, ".18")
  + p("M21 20.5 l1.8-3.4 2.4 3.4 1.8-3.4", A, 1.6)          # filament
  + BULB
  + p("M24 2.6 v3.4 M13.9 6.8 l2.4 2.4 M34.1 6.8 l-2.4 2.4 "
      "M9.6 17 h3.4 M35 17 h3.4", A, 2))                     # rayons
w("ampoule_off",
  c(24, 17, 9.6, G, 2.2)
  + p("M21 20.5 l1.8-3.4 2.4 3.4 1.8-3.4", G, 1.4) + BULB)

# ---------------------------------------------------------------- volets
FRAME = r_(9.5, 5.5, 29, 37, 2, W) + p("M9.5 11 h29", W, 1.8)   # coffre
w("volet_ouvert",
  FRAME
  + p("M12 14.2 h24 M12 17.4 h24", G, 1.8)                    # lames enroulées
  + p("M24 20.5 V42.5", G, 1.5)                               # meneau central
  + p("M14.5 33 l6.5-6.5 M17.5 38 l9-9", A, 1.6)              # reflets
  + p("M9.5 42.5 h29", W, 2.2))
w("volet_ferme",
  FRAME
  + "".join(p(f"M12 {y} h24", G, 1.8) for y in
            (14.2, 17.4, 20.6, 23.8, 27.0, 30.2, 33.4, 36.6))
  + p("M20.5 39.4 h7", A, 2))                                  # poignée de tirage

# ------------------------------------------------- volets battants
LAMES_G = "".join(p(f"M13.6 {y} h7.6", G, 1.5) for y in (13, 17.5, 22, 26.5, 31, 35.5))
LAMES_D = "".join(p(f"M26.8 {y} h7.6", G, 1.5) for y in (13, 17.5, 22, 26.5, 31, 35.5))
w("volet_battant_ferme",
  r_(11, 8.5, 26, 31.5, 1.6, W)                                # dormant
  + p("M24 8.5 V40", W, 2)                                     # jonction vantaux
  + LAMES_G + LAMES_D
  + c(21.4, 24.2, 1, A, 1.4, A) + c(26.6, 24.2, 1, A, 1.4, A)  # poignées
  + p("M8 42.5 h32", W, 2.2))                                  # appui
w("volet_battant_ouvert",
  r_(13.5, 8.5, 21, 31.5, 1.6, W)                              # fenêtre dégagée
  + p("M24 8.5 V40 M13.5 24.2 h21", G, 1.5)                    # croisillons
  + p("M16.5 20 l3.5-3.5 M16.5 31 l6-6", A, 1.5)               # reflets
  + r_(3, 8.5, 7.2, 31.5, 1.2, W, 2)                           # vantail rabattu g.
  + r_(37.8, 8.5, 7.2, 31.5, 1.2, W, 2)
  + "".join(p(f"M5 {y} h3.2", G, 1.4) for y in (13.5, 19, 24.5, 30, 35.5))
  + "".join(p(f"M39.8 {y} h3.2", G, 1.4) for y in (13.5, 19, 24.5, 30, 35.5))
  + p("M8 42.5 h32", W, 2.2))                                  # appui

# ---------------------------------------------------------------- prise
w("prise",
  r_(7.5, 7.5, 33, 33, 6.5, W)
  + c(24, 24, 12.2, G, 1.8)
  + c(24, 17.8, 1.9, G, 1.5, G)                                # broche de terre
  + c(18.4, 26.6, 2.2, A, 1.8, A, ".9")
  + c(29.6, 26.6, 2.2, A, 1.8, A, ".9"))

# ---------------------------------------------------------------- véhicule
w("vehicule_electrique",
  p("M5 38.9 h38", G, 1.5)                                     # sol
  # carrosserie symétrique autour de x=24, habitacle allongé (x 13.7 -> 34.3)
  + p("M6 34.3 v-5.4 c0-2 1.3-3.2 3.4-3.6 l4.3-.9 3.3-6.7 c.9-1.8 2.4-2.7 4.4-2.7"
      " h5.2 c2 0 3.5.9 4.4 2.7 l3.3 6.7 4.3.9 c2.1.4 3.4 1.6 3.4 3.6 v5.4", W)
  + p("M6 34.3 h4.2 M18.8 34.3 h10.4 M37.8 34.3 h4.2", W, 2.2)
  # vitres longues, séparées au montant central x=24
  + p("M16.2 24.4 l2.9-5.8 c.5-1 1.3-1.5 2.4-1.5 h1.3 v7.3 z "
      "M25.2 24.4 v-7.3 h1.3 c1.1 0 1.9.5 2.4 1.5 l2.9 5.8 z", G, 1.5)
  # roues centrées symétriquement : 24 ± 9.5
  + c(14.5, 34.3, 4.2, W, 2.2) + c(33.5, 34.3, 4.2, W, 2.2)
  + c(14.5, 34.3, 1.3, G, 1.4, G) + c(33.5, 34.3, 1.3, G, 1.4, G)
  # phare avant + feu arrière
  + p("M40.4 29 h1.8", A, 2.2) + p("M5.8 29 h1.8", G, 2.2)
  # éclair de charge sur la carrosserie, centré sur x=24, sous les vitres
  + p("M25.1 25.9 l-2.7 3.7 h3.2 l-2.7 3.7", A, 1.9))

# ---------------------------------------------------------------- réfrigérateur
w("refrigerateur",
  r_(14.5, 4.5, 19, 39, 2.5, W)
  + p("M14.5 19 h19", W, 1.8)                                  # séparation portes
  + p("M29.8 8.5 v6.5 M29.8 23 v9", A, 2)                      # poignées
  + p("M18 41 v2 M30 41 v2", G, 1.8))                          # pieds

# ---------------------------------------------------------------- PC
w("pc_bureau",
  # écran compact décalé à gauche
  r_(5.5, 8.5, 25, 17.5, 2.2, W)
  + r_(8, 11, 20, 12.5, 1, G, 1.5)
  + c(18, 24.6, .8, A, 1.3, A)                                 # LED écran
  + p("M14 32 h8 M18 26 v6", W, 2)                             # pied écran
  + p("M11 15 l3.5-3.5 M11 19 l7.5-7.5", A, 1.2)               # reflet
  # tour à droite
  + r_(33, 8.5, 9.5, 30, 2, W)
  + p("M35.5 13 h4.5 M35.5 16.5 h4.5", G, 1.6)                 # baies/aérations
  + c(37.7, 22, 1.5, G, 1.5)                                   # bouton power
  + c(37.7, 27, .9, A, 1.3, A)                                 # LED activité
  + p("M35.5 32.5 h4.5", G, 1.5))                              # fente
w("pc_portable",
  r_(11.5, 8.5, 25, 17, 2, W)
  + r_(14, 11, 20, 12, .8, G, 1.5)
  + p("M7 33.2 l4-7.7 h26 l4 7.7 c.4 1-.3 1.8-1.4 1.8 H8.4 c-1.1 0-1.8-.8-1.4-1.8 z", W)
  + p("M20.5 31.6 h7", A, 1.8))                                # pavé tactile

# ---------------------------------------------------------------- piscine
w("piscine",
  p("M18.5 32 V10.5 M29.5 32 V10.5", W, 2.2)                   # montants
  + p("M18.5 14 h11 M18.5 19 h11 M18.5 24 h11", W, 1.8)        # barreaux
  + p("M5.5 31 q3-2.8 6 0 t6 0 t6 0 t6 0 t6 0 t6 0", A, 2.2)
  + p("M5.5 37.5 q3-2.8 6 0 t6 0 t6 0 t6 0 t6 0 t6 0", A, 2, extra=' opacity=".55"'))

# ---------------------------------------------------------------- portail
# Portail fermé et ouvert : mêmes deux vantaux à barreaux entre deux piliers,
# jointifs au centre quand fermé, pivotés vers le spectateur quand ouvert.
w("portail",
  p("M6 42.5 V13 M42 42.5 V13", W, 2.4)                        # piliers
  # vantail gauche (x 6->24) et droit (x 24->42), rectangles jointifs
  + p("M6 16 h18 v22.5 H6 z", W, 2)
  + p("M24 16 h18 v22.5 H24 z", W, 2)
  + p("M11 16 V38.5 M16 16 V38.5", G, 1.6)                     # barreaux g.
  + p("M32 16 V38.5 M37 16 V38.5", G, 1.6)                     # barreaux d.
  + p("M6 24.5 h36", A, 2)                                     # lisse haute continue
  + c(21.6, 30, 1, A, 1.4, A) + c(26.4, 30, 1, A, 1.4, A))     # serrure centrale
w("portail_ouvert",
  p("M6 42.5 V13 M42 42.5 V13", W, 2.4)                        # piliers
  # vantaux pivotés vers le spectateur (trapèzes symétriques)
  + p("M6 15.5 L15.5 19 V38.5 L6 40.5 z", W, 2)
  + p("M42 15.5 L32.5 19 V38.5 L42 40.5 z", W, 2)
  + p("M9.2 16.9 V39.8 M12.4 18 V39.1", G, 1.6)                # barreaux vantail g.
  + p("M38.8 16.9 V39.8 M35.6 18 V39.1", G, 1.6)               # barreaux vantail d.
  + p("M20.5 42.5 h7", A, 2.2))                                # seuil dégagé

# ---------------------------------------------------------------- chauffe-eau
w("chauffe_eau",
  r_(14, 4.5, 20, 29.5, 8.5, W)
  + p("M24 11.5 c3.4 4.3 5.1 6.8 5.1 9.4 a5.1 5.1 0 11-10.2 0 c0-2.6 1.7-5.1 5.1-9.4 z",
      A, 1.9, A, ".2")
  + p("M18 29.5 h12", G, 1.5)                                  # niveau
  + p("M17.5 34 v5 M30.5 34 v5", G, 2)                         # piquages
  + p("M15.2 41.5 h4.6 M28.2 41.5 h4.6", G, 1.8))              # raccords

# ---------------------------------------------------------------- lave-linge
w("lave_linge",
  r_(9.5, 5, 29, 37.5, 3, W)
  + p("M9.5 13.5 h29", W, 1.8)
  + c(14.8, 9.2, 1.7, A, 1.6, A, ".85")                        # sélecteur
  + p("M27 9.2 h6.5", G, 1.8)                                  # afficheur
  + c(24, 28.5, 9.7, W, 2.2) + c(24, 28.5, 6.6, G, 1.6)        # hublot double
  + p("M18.9 28.5 c1.7-1.9 3.4-1.9 5.1 0 s3.4 1.9 5.1 0", A, 1.7))

# ---------------------------------------------------------------- TV
w("tv",
  r_(5.5, 8.5, 37, 23.5, 2.4, W)
  + r_(8.2, 11.2, 31.6, 18, 1, G, 1.4)
  + p("M13 25 l7.5-7.5 M17 27.5 l10-10", A, 1.3)               # reflet
  + p("M24 32 v4 M15.5 38.8 h17", W, 2.2))
w("home_cinema",
  r_(13, 9.5, 22, 14.5, 1.8, W)
  + p("M22 13.5 l5 3.2 -5 3.2 z", A, 1.6, A, ".35")            # lecture
  + r_(4.5, 16.5, 6.6, 22, 1.6, G, 1.9)                        # enceintes
  + r_(36.9, 16.5, 6.6, 22, 1.6, G, 1.9)
  + c(7.8, 21, 1.3, A, 1.4) + c(40.2, 21, 1.3, A, 1.4)
  + c(7.8, 31.5, 2.4, A, 1.5) + c(40.2, 31.5, 2.4, A, 1.5)
  + r_(14.5, 27.5, 19, 4.4, 2.2, G, 1.8)                       # barre de son
  + p("M18.5 29.7 h.1 M22.2 29.7 h.1 M25.8 29.7 h.1 M29.5 29.7 h.1", A, 1.8))

# ---------------------------------------------------------------- box internet
w("box_internet",
  r_(7, 26, 34, 12, 3.5, W)
  + p("M15 26 V17.5 M33 26 V17.5", G, 2)                       # antennes
  + c(15, 16.2, 1.1, G, 1.4, G) + c(33, 16.2, 1.1, G, 1.4, G)
  + c(12.5, 32, 1.1, A, 1.4, A) + c(17.3, 32, 1.1, G, 1.4, G) + c(22.1, 32, 1.1, G, 1.4, G)
  + p("M19 14.6 a7.4 7.4 0 0110 0", A, 2)                      # wifi centré x=24
  + p("M15.2 10.2 a13 13 0 0117.6 0", A, 2)
  + c(24, 19.2, 1.2, A, 1.4, A))

# ---------------------------------------------------------------- radiateur
cols = "".join(r_(x - 2.1, 9, 4.2, 30, 2.1, W, 2) for x in (9, 15, 21, 27, 33, 39))
w("radiateur",
  cols
  + p("M11.1 13.5 h1.8 M17.1 13.5 h1.8 M23.1 13.5 h1.8 M29.1 13.5 h1.8 M35.1 13.5 h1.8",
      G, 1.6)                                                  # raccords hauts
  + p("M11.1 34.5 h1.8 M17.1 34.5 h1.8 M23.1 34.5 h1.8 M29.1 34.5 h1.8 M35.1 34.5 h1.8",
      A, 1.6)                                                  # collecteur bas
  + p("M11 42 v-3 M37 42 v-3", G, 1.9))                        # pieds

# --------------------------------------------- radiateur, modes fil pilote
# Radiateur réduit en hauteur (30->18) pour dégager une zone haute (y<21) où
# vient se poser le symbole de mode (grossi ~70%) ; pieds et collecteur bas
# gardent leur position d'origine (fixée sur le bas de l'icône).
def rad_base(structure, collector):
    cols = "".join(r_(x - 2.1, 21, 4.2, 18, 2.1, structure, 2) for x in (9, 15, 21, 27, 33, 39))
    return (cols
      + p("M11.1 25.5 h1.8 M17.1 25.5 h1.8 M23.1 25.5 h1.8 M29.1 25.5 h1.8 M35.1 25.5 h1.8",
          G, 1.6)                                                # raccords hauts
      + p("M11.1 34.5 h1.8 M17.1 34.5 h1.8 M23.1 34.5 h1.8 M29.1 34.5 h1.8 M35.1 34.5 h1.8",
          collector, 1.6)                                        # collecteur bas
      + p("M11 42 v-3 M37 42 v-3", G, 1.9))                      # pieds


RAD_ON = rad_base(W, A)
RAD_OFF = rad_base(G, G)

w("radiateur_confort",                                           # soleil ambre
  RAD_ON
  + c(24, 10.7, 4.8, A, 2.0, A, ".8")
  + p("M24 4.58 v-2.38 M24 16.82 v2.38 M17.88 10.7 h-2.38 M30.12 10.7 h2.38", A, 1.8)
  + p("M19.66 6.36 l-1.68-1.68 M28.34 6.36 l1.68-1.68 "
      "M19.66 15.04 l-1.68 1.68 M28.34 15.04 l1.68 1.68", A, 1.6))
w("radiateur_eco",                                                # croissant gris bleuté
  RAD_ON
  + p("M28.08 14.78 A7.48 7.48 0 1 1 19.92 6.62 A5.78 5.78 0 0 0 28.08 14.78 Z",
      G, 1.9, G, ".85"))
w("radiateur_hors_gel",                                           # cristal de glace gris bleuté
  RAD_ON
  + p("M17.37 10.7 H30.63 M20.685 4.954 L27.315 16.446 M27.315 4.954 L20.685 16.446", G, 1.9)
  + c(17.37, 10.7, .94, G, 0, G) + c(30.63, 10.7, .94, G, 0, G)
  + c(20.685, 4.954, .94, G, 0, G) + c(27.315, 16.446, .94, G, 0, G)
  + c(27.315, 4.954, .94, G, 0, G) + c(20.685, 16.446, .94, G, 0, G))
w("radiateur_off",                                                # radiateur grisé + "OFF" ambre
  RAD_OFF
  + r_(6.15, 3.05, 11.05, 15.3, 5.53, A, 2.4)                      # O
  + p("M20.6 3.05 V18.35 M20.6 3.05 H29.95 M20.6 10.28 H28.25", A, 2.4)  # F
  + p("M32.5 3.05 V18.35 M32.5 3.05 H41.85 M32.5 10.28 H40.15", A, 2.4))  # F

# ---------------------------------------------------------------- sèche-serviettes
w("seche_serviettes",
  p("M14 6 V42 M34 6 V42", W, 2.2)                             # colonnes
  # barreaux : pleins en haut et en bas, interrompus derrière la serviette
  + p("M14 10.5 h20 M14 16 h20", G, 2)
  + p("M14 21.5 h3.5 M30.5 21.5 h3.5 M14 27 h3.5 M30.5 27 h3.5", G, 2)
  + p("M14 32.5 h20 M14 38 h20", G, 2)
  # serviette pliée sur le barreau y=16
  + r_(18, 16, 12, 15.5, 1.5, A, 1.9, A, ".16")
  + p("M18 20 h12", A, 1.4))                                   # pli

# ---------------------------------------------------------------- chaudière
w("chaudiere",
  r_(12, 4.5, 24, 33.5, 3, W)
  + r_(16.5, 8.5, 15, 4.6, 1, G, 1.5) + c(29, 10.8, .8, A, 1.3, A)
  + p("M24 17.5 c-4.2 4.9-5.9 7.7-5.9 10.4 a5.9 5.9 0 0011.8 0 c0-2.7-1.7-5.5-5.9-10.4 z",
      A, 1.9, A, ".16")
  + p("M24 24.5 c-1.7 2-2.4 3.2-2.4 4.4 a2.4 2.4 0 004.8 0 c0-1.2-.7-2.4-2.4-4.4 z",
      A, 0, A, ".55")
  + p("M17 38 v4.5 M31 38 v4.5", G, 2))

# ---------------------------------------------------------------- climatiseur
w("climatiseur",
  r_(6.5, 8.5, 35, 13.5, 3.2, W)
  + p("M10.5 18.4 h27", G, 1.8)                                # volet de soufflage
  + c(36.6, 12.8, .9, A, 1.3, A)                               # témoin
  + p("M14 26.5 q-2.4 2.7 0 5.4 t0 5.4", A, 2)                 # flux d'air ×3
  + p("M24 26.5 q-2.4 2.7 0 5.4 t0 5.4", A, 2)
  + p("M34 26.5 q-2.4 2.7 0 5.4 t0 5.4", A, 2))

# ---------------------------------------------------------------- solaire
w("panneaux_solaires",
  p("M11 13.5 h26 l4.6 17.5 H6.4 z", W)                        # panneau symétrique
  + p("M17.9 13.5 L15.6 31 M24 13.5 V31 M30.1 13.5 L32.4 31", G, 1.5)
  + p("M9.2 22.2 h29.6", G, 1.5)
  + p("M24 31 v7.3 M16.8 42.6 h14.4", W, 2.2)                  # pied centré
  + c(40, 6.8, 3, A, 1.8, A, ".25")
  + p("M40 1.2 v1.6 M44.8 3 l-1.1 1.1 M46.4 6.8 h-1.6", A, 1.6))

# ---------------------------------------------------------------- capteurs
w("temperature",
  p("M21.4 27.6 V9.5 a2.6 2.6 0 015.2 0 v18.1 a7.7 7.7 0 11-5.2 0 z", W)
  # centre réel du réservoir dessiné par l'arc : (24, 34.85)
  + c(24, 34.85, 4.2, A, 0, A) + p("M24 34.85 V13.5", A, 2.4)
  + p("M29 12.5 h2.8 M29 17.5 h2.8 M29 22.5 h2.8", G, 1.5))    # graduations
w("compteur",
  c(24, 26, 16.4, W)
  + p("M12.4 14.4 l1.7 1.7 M24 9.6 v2.4 M35.6 14.4 l-1.7 1.7 "
      "M9.6 26 h2.4 M36 26 h2.4", G, 1.7)                      # graduations en arc
  + p("M24 26 L31.4 18.6", A, 2.4) + c(24, 26, 2.1, A, 1.5, A) # aiguille
  + r_(19.8, 32.8, 8.4, 4, 1, G, 1.5))                         # totaliseur
w("humidite",
  p("M24 5.6 C31 14.4 34.7 19.7 34.7 25.4 a10.7 10.7 0 11-21.4 0 C13.3 19.7 17 14.4 24 5.6 z",
    A, 2.2, A, ".16")
  + p("M29.3 26.2 a5.4 5.4 0 01-4.1 5.2", W, 1.7))             # reflet
w("precipitation",
  p("M14.6 24.5 a6.6 6.6 0 011-13.1 8.9 8.9 0 0116.9 2.3 5.7 5.7 0 01-1.1 10.8 z", W)
  + p("M16 28.5 l-1.7 4.6 M24 28.5 l-1.7 4.6 M32 28.5 l-1.7 4.6", A, 2.1)
  + p("M20.2 35.5 l-1.4 3.8 M28.2 35.5 l-1.4 3.8", A, 2.1))
w("luminosite",
  c(24, 24, 7.5, A, 2.2, A, ".22")
  + p("M24 6.4 v4.7 M24 36.9 v4.7 M6.4 24 h4.7 M36.9 24 h4.7 "
      "M11.6 11.6 l3.3 3.3 M33.1 33.1 l3.3 3.3 M36.4 11.6 l-3.3 3.3 M14.9 33.1 l-3.3 3.3",
      A, 2.1))
w("vent",
  p("M6 15.5 h17.3 a4.3 4.3 0 10-4.3-4.3", W, 2.2)
  + p("M6 24 h26.6 a4.7 4.7 0 11-4.7 4.7", A, 2.2)
  + p("M6 32.5 h13", G, 2))

# ---------------------------------------------------------------- interrupteurs
w("interrupteur_on",
  r_(6.5, 15, 35, 18, 9, A, 2.2, A, ".16")
  + c(32.5, 24, 6.5, A, 0, A) + p("M32.5 20.8 v6.4", W, 2))
w("interrupteur_off",
  r_(6.5, 15, 35, 18, 9, G, 2.2)
  + c(15.5, 24, 6.5, G, 2.2) + c(15.5, 24, 2.5, G, 1.7))

print(f"{sum(f.endswith('.svg') for f in os.listdir(OUT))} icônes SVG dans {OUT}")
