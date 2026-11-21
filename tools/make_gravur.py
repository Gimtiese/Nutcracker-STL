#!/usr/bin/env python3
"""Erzeugt gravur.json: die Gravur auf der flachen Rueckseite (Druckbett-Seite).

Motiv: ein Baer (aus einer Bilddatei nachgezeichnet), ein Tannenbaum mit Stern
und ein Schriftzug.  Baer und Baum werden als Kontur graviert, die Schrift als
gefuellte Buchstaben -- alle Rillen bleiben damit schmal genug, dass die erste
Schicht darueber sauber druckt.

Leserichtung: von aussen auf die Rueckseite gesehen, Schraube zeigt nach rechts.

Aufruf:
    python3 tools/make_gravur.py baer.jpg "Stine & Tim" /pfad/zur/Schrift.otf gravur.json
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


def bear_shape(path: str, width: float) -> CrossSection:
    img = np.asarray(Image.open(path).convert("RGB")).astype(float)
    mask = Image.fromarray(((img[..., 0] - img[..., 2]) > 60).astype(np.uint8) * 255)
    mask = np.asarray(mask.filter(ImageFilter.GaussianBlur(1.5))).astype(float) / 255.0
    loops = trace(np.pad(mask, 4), 1.0)
    loop = max(loops, key=len)                               # der Baer ist die groesste Kontur
    loop = (loop - loop.min(0)) * (width / np.ptp(loop[:, 0]))
    return CrossSection([loop], FillRule.EvenOdd).simplify(0.02)


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


def tree_shape(height: float, width: float) -> tuple[CrossSection, CrossSection]:
    """Tanne (Umriss) und Stern; Ursprung unten Mitte des Stamms."""
    star_r = 0.105 * height
    h = height - 1.55 * star_r                 # Hoehe bis zur Baumspitze
    trunk = 0.13
    right = [(0.0, 1.0), (0.44, 0.63), (0.21, 0.63), (0.70, 0.36), (0.38, 0.36),
             (1.0, trunk), (0.17, trunk), (0.17, 0.0)]
    poly = np.array([(x * width / 2, y * h) for x, y in right] +
                    [(-x * width / 2, y * h) for x, y in right[:0:-1]])
    tree = CrossSection([poly], FillRule.EvenOdd)
    tree = tree.offset(-0.5, JoinType.Round, 2.0, SEG).offset(0.5, JoinType.Round, 2.0, SEG)   # Ecken runden
    ang = np.pi / 2 + np.arange(10) * np.pi / 5
    rad = np.where(np.arange(10) % 2 == 0, star_r, 0.45 * star_r)
    star = CrossSection([np.c_[rad * np.cos(ang), rad * np.sin(ang) + h + 0.55 * star_r]], FillRule.EvenOdd)
    return tree, star


def outline(shape: CrossSection, w: float = LINE) -> CrossSection:
    return shape - shape.offset(-w, JoinType.Round, 2.0, SEG)


def compose(bear_img: str, text: str, font: str, s: float) -> tuple[CrossSection, CrossSection]:
    """Motiv in Betrachter-Koordinaten (a nach rechts, b nach oben) im Massstab s.
    Gibt (Gravurflaeche, Huellflaeche fuer die Platzpruefung) zurueck."""
    bear = bear_shape(bear_img, 36.0 * s)
    tree, star = tree_shape(25.0 * s, 16.0 * s)
    txt = text_shape(text, font, 6.2 * s)
    ground = 0.0
    bb = bear.bounds()
    tree_x = bb[2] + 2.4 * s + 8.0 * s
    tb = txt.bounds()
    txt = txt.translate((0.5 * (bb[0] + tree_x + 8.0 * s) - 0.5 * (tb[2] - tb[0]) + 1.0 * s,
                         ground - 3.2 * s - (tb[3] - tb[1])))
    move = (tree_x, ground)
    art = outline(bear) + outline(tree.translate(move)) + star.translate(move) + txt
    hull = bear + tree.translate(move) + star.translate(move) + txt
    return art, hull


def main() -> None:
    bear_img, text, font, dst = sys.argv[1:5]
    p = nc.Params(engraving="")
    foot = nc.make_body(p).slice(0.02)                         # Auflageflaeche in Koerper-xy
    # Betrachter: a = Koerper-y (nach rechts), b = Koerper-x (nach oben)
    swap = lambda cs: CrossSection([q[:, ::-1] for q in cs.to_polygons()], FillRule.EvenOdd)  # noqa: E731
    area = swap(foot).offset(-MARGIN, JoinType.Round, 2.0, SEG)

    best = None
    for s in np.arange(1.12, 0.6, -0.02):
        art, hull = compose(bear_img, text, font, s)
        hb = hull.bounds()
        ab = area.bounds()
        for da in np.arange(ab[0] - hb[0], ab[2] - hb[2] + 1e-9, 0.5):
            for db in np.arange(ab[1] - hb[1], ab[3] - hb[3] + 1e-9, 0.5):
                if (hull.translate((da, db)) - area).area() < 1e-3:
                    cand = (abs(db + 0.5 * (hb[1] + hb[3])), da, db)      # moeglichst mittig
                    if best is None or cand < best[0]:
                        best = (cand, s, da, db)
        if best is not None:
            break
    if best is None:
        raise SystemExit("Motiv passt nicht auf die Auflageflaeche")
    _, s, da, db = best
    art, hull = compose(bear_img, text, font, s)
    art = art.translate((da, db)).simplify(0.01)
    # winzige Inseln (z. B. im "&") wuerden als loser Punkt gedruckt: mit ausraeumen
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
