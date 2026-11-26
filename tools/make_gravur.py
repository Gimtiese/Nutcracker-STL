#!/usr/bin/env python3
"""Erzeugt gravur.json: die Gravur auf der flachen Rueckseite (Druckbett-Seite).

Motiv: links zwei Tannenbaeume in zwei Groessen, hintereinander versetzt,
rechts daneben eine Jahreszahl.
Die Baeume werden als Kontur graviert, die Zahl als gefuellte Ziffern -- alle
Rillen bleiben damit schmal genug, dass die erste Schicht darueber sauber druckt.

Leserichtung: von aussen auf die Rueckseite gesehen, Schraube zeigt nach rechts.

Aufruf:
    python3 tools/make_gravur.py "2026" /pfad/zur/Schrift.otf gravur.json
"""
import json
import sys
from pathlib import Path

import numpy as np
from contourpy import contour_generator
from manifold3d import CrossSection, FillRule, JoinType
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nutcracker as nc  # noqa: E402

LINE = 1.3          # Rillenbreite der Konturen (mm)
STROKE_MIN = 1.15   # kleinste Strichstaerke der Schrift (mm)
MARGIN = 2.6        # Abstand der Gravur zum Rand der Auflageflaeche (mm)
SCALE_MAX = 1.20    # groesster Massstab des Motivs (1 = grosse Tanne 25 mm hoch); kleiner, falls es nicht passt
SEG = 48


def trace(mask: np.ndarray, px_per_mm: float) -> list[np.ndarray]:
    """Konturen eines Graustufenbilds (0..1) bei 0.5, in mm, y nach oben."""
    h = mask.shape[0]
    lines = contour_generator(z=mask).lines(0.5)
    out = []
    for ln in lines:
        if len(ln) < 8 or np.linalg.norm(ln[0] - ln[-1]) > 1e-6:
            continue
        out.append(np.c_[ln[:-1, 0], h - ln[:-1, 1]] / px_per_mm)
    return out


def text_shape(text: str, font_path: str, cap: float) -> CrossSection:
    ppm = 40.0
    font = ImageFont.truetype(font_path, 400)
    cap_px = font.getbbox("T")[3] - font.getbbox("T")[1]
    font = ImageFont.truetype(font_path, int(round(400 * cap * ppm / cap_px)))
    bb = font.getbbox(text)
    img = Image.new("L", (bb[2] - bb[0] + 40, bb[3] - bb[1] + 40), 0)
    ImageDraw.Draw(img).text((20 - bb[0], 20 - bb[1]), text, font=font, fill=255)
    arr = np.asarray(img.filter(ImageFilter.GaussianBlur(0.8))).astype(float) / 255.0
    cs = CrossSection(trace(arr, ppm), FillRule.EvenOdd).simplify(0.01)
    # Strichstaerke am Stamm des "i" messen und bei Bedarf aufdicken
    ibb = font.getbbox("l")
    stroke = (ibb[2] - ibb[0]) / ppm
    if stroke < STROKE_MIN:
        cs = cs.offset(0.5 * (STROKE_MIN - stroke), JoinType.Round, 2.0, SEG)
    b = cs.bounds()
    return cs.translate((-b[0], -b[1]))


def tree_shape(h: float, width: float) -> CrossSection:
    """Tanne (Umriss) der Hoehe h; Ursprung unten Mitte des Stamms."""
    trunk = 0.13
    # Einschnuerungen zwischen den Etagen mindestens so breit, dass innen noch
    # Platz neben der Rille bleibt (sonst laeuft die Kontur bei kleinen Baeumen zu)
    neck = max(0.28, (LINE + 0.7) / (0.5 * width))
    right = [(0.0, 1.0), (neck + 0.24, 0.64), (neck, 0.64), (0.76, 0.37), (neck + 0.14, 0.37),
             (1.0, trunk), (0.17, trunk), (0.17, 0.0)]
    poly = np.array([(x * width / 2, y * h) for x, y in right] +
                    [(-x * width / 2, y * h) for x, y in right[:0:-1]])
    tree = CrossSection([poly], FillRule.EvenOdd)
    tree = tree.offset(-0.5, JoinType.Round, 2.0, SEG).offset(0.5, JoinType.Round, 2.0, SEG)   # Ecken runden
    return tree


def outline(shape: CrossSection, w: float = LINE) -> CrossSection:
    return shape - shape.offset(-w, JoinType.Round, 2.0, SEG)


def compose(text: str, font: str, s: float) -> tuple[CrossSection, CrossSection]:
    """Motiv in Betrachter-Koordinaten (a nach rechts, b nach oben) im Massstab s.
    Links zwei Tannen hintereinander: die kleine steht etwas hoeher und halb
    verdeckt hinter der grossen.  Rechts die Jahreszahl auf der Grundlinie der
    vorderen Tanne.  Gibt (Gravurflaeche, Huellflaeche fuer die Platzpruefung) zurueck."""
    front = tree_shape(25.0 * s, 18.0 * s)                       # Mitte bei a = 0
    back = tree_shape(19.0 * s, 14.0 * s).translate((-9.0 * s, 3.5 * s))
    # verdeckter Teil faellt weg; ein schmaler Spalt vor der vorderen Kontur haelt
    # die Linien getrennt (sonst entstehen an den Kreuzungen Kleckse)
    back_line = outline(back) - front.offset(0.9, JoinType.Round, 2.0, SEG)
    year = text_shape(text, font, 9.0 * s)
    year = year.translate((9.0 * s + 4.5 * s, 0.0))
    art = back_line + outline(front) + year
    hull = back + front + year
    return art, hull


def main() -> None:
    text, font, dst = sys.argv[1:4]
    p = nc.Params(engraving="")
    foot = nc.make_body(p).slice(0.02)                         # Auflageflaeche in Koerper-xy
    # Betrachter: a = Koerper-y (nach rechts), b = Koerper-x (nach oben)
    swap = lambda cs: CrossSection([q[:, ::-1] for q in cs.to_polygons()], FillRule.EvenOdd)  # noqa: E731
    area = swap(foot).offset(-MARGIN, JoinType.Round, 2.0, SEG)

    best = None
    for s in np.arange(SCALE_MAX, 0.6, -0.02):
        art, hull = compose(text, font, s)
        hb = hull.bounds()
        ab = area.bounds()
        for da in np.arange(ab[0] - hb[0], ab[2] - hb[2] + 1e-9, 0.5):
            for db in np.arange(ab[1] - hb[1], ab[3] - hb[3] + 1e-9, 0.5):
                if (hull.translate((da, db)) - area).area() < 1e-3:
                    # moeglichst mittig auf der Auflageflaeche
                    cand = ((da + 0.5 * (hb[0] + hb[2]) - 0.5 * (ab[0] + ab[2])) ** 2
                            + (db + 0.5 * (hb[1] + hb[3]) - 0.5 * (ab[1] + ab[3])) ** 2)
                    if best is None or cand < best[0]:
                        best = (cand, s, da, db)
        if best is not None and best[0] < 2.0 ** 2:     # erst nehmen, wenn es fast mittig sitzt
            break
        best = None
    if best is None:
        raise SystemExit("Motiv passt nicht auf die Auflageflaeche")
    _, s, da, db = best
    art, hull = compose(text, font, s)
    art = art.translate((da, db)).simplify(0.01)
    # winzige Inseln wuerden als loser Punkt gedruckt: mit ausraeumen; winzige
    # Gravurpunkte (z. B. der Punkt in der Null mancher Schriften) weglassen
    art = CrossSection.compose([c for c in art.decompose() if c.area() > 2.0])
    b = art.bounds()
    frame = CrossSection.square((b[2] - b[0] + 6, b[3] - b[1] + 6)).translate((b[0] - 3, b[1] - 3))
    for piece in (frame - art).decompose():
        if piece.area() < 1.0:
            art = art + piece
    polys_xy = [np.round(q[:, ::-1], 3).tolist() for q in art.to_polygons()]
    b = art.bounds()
    print(f"Massstab {s:.2f}: Motiv {b[2]-b[0]:.1f} x {b[3]-b[1]:.1f} mm, "
          f"{len(polys_xy)} Konturen, Gravurflaeche {art.area():.0f} mm2")
    Path(dst).write_text(json.dumps({
        "hinweis": "Gravur auf der Rueckseite; Koordinaten = Koerper-xy in mm. Erzeugt von tools/make_gravur.py",
        "text": text, "polygons_xy": polys_xy}, separators=(",", ":")))
    print("geschrieben:", dst)


if __name__ == "__main__":
    main()
