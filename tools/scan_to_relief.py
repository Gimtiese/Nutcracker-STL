#!/usr/bin/env python3
"""Wandelt einen Walnuss-3D-Scan (OBJ) in eine Reliefkarte fuer nutcracker.py um.

Der Scan wird ausgerichtet (Naht = Aequatorebene, Spitze = +y), vom Schwerpunkt
aus radial abgetastet und durch ein angepasstes Ellipsoid geteilt.  Uebrig
bleibt das, was die Nuss von einem glatten Ei unterscheidet: Naht, Rippen,
Furchen, die Spitze und der abgeflachte Stielansatz.

Aufruf:  python3 tools/scan_to_relief.py walnut.obj walnut_relief.npz

Verwendeter Scan: "Walnut 3D scan" von DocMike,
https://www.printables.com/model/214774-walnut-3d-scan  (CC BY-NC-SA 4.0)
"""
import sys

import numpy as np
from manifold3d import Manifold, Mesh

N_LAT, N_LON = 720, 1440          # Raster der Karte (0,25 Grad)


def load_obj(path):
    verts, faces = [], []
    with open(path, "r", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = [int(q.split("/")[0]) for q in line.split()[1:]]
                for k in range(1, len(idx) - 1):
                    faces.append((idx[0], idx[k], idx[k + 1]))
    return np.array(verts), np.array(faces) - 1


def volume_centroid(v, f):
    t = v[f]
    w = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2]))
    return (w[:, None] * t.sum(1) / 4).sum(0) / w.sum()


def find_seam_and_tip(x):
    """Naht = Grosskreis, der am staerksten ueber seine Umgebung hinausragt;
    Spitze = Stelle auf der Naht mit dem groessten Radius."""
    r = np.linalg.norm(x, axis=1)
    u = x / r[:, None]
    rng = np.random.default_rng(0)

    def score(n):
        t = np.abs(u @ n)
        return r[t < 0.035].mean() - r[(t > 0.08) & (t < 0.2)].mean()

    cand = rng.normal(size=(6000, 3))
    cand /= np.linalg.norm(cand, axis=1)[:, None]
    best = cand[np.argmax([score(n) for n in cand])]
    for scale in (0.03, 0.03, 0.01, 0.01, 0.004):
        loc = best + rng.normal(scale=scale, size=(1500, 3))
        loc /= np.linalg.norm(loc, axis=1)[:, None]
        s = np.array([score(n) for n in loc])
        if s.max() > score(best):
            best = loc[np.argmax(s)]
    a1 = np.cross(best, [0.0, 0.0, 1.0])
    a1 /= np.linalg.norm(a1)
    a2 = np.cross(best, a1)
    on = np.abs(u @ best) < 0.035
    ang = np.arctan2(u[on] @ a2, u[on] @ a1)
    bins = np.linspace(-np.pi, np.pi, 73)
    prof = np.array([r[on][(ang >= bins[i]) & (ang < bins[i + 1])].mean() for i in range(72)])
    prof = (prof + np.roll(prof, 1) + np.roll(prof, -1)) / 3
    k = int(np.argmax(prof))
    a = 0.5 * (bins[k] + bins[k + 1])
    tip = np.cos(a) * a1 + np.sin(a) * a2
    return best, tip, score(best)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    v, f = load_obj(src)
    x = v - volume_centroid(v, f)
    x *= 19.2 / np.linalg.norm(x, axis=1).mean()        # auf etwa echte Groesse (mm)
    seam, tip, sc = find_seam_and_tip(x)
    ey = tip - (tip @ seam) * seam
    ey /= np.linalg.norm(ey)
    ez = seam / np.linalg.norm(seam)
    ex = np.cross(ey, ez)
    xa = x @ np.stack([ex, ey, ez], 1)                   # ausgerichtet: y = Spitze, z = Nahtnormale
    print(f"Naht gefunden (Wulst {sc:.2f} mm), Ausdehnung x/y/z: "
          f"{np.ptp(xa[:, 0]):.1f} / {np.ptp(xa[:, 1]):.1f} / {np.ptp(xa[:, 2]):.1f} mm")

    m = Manifold(Mesh(vert_properties=xa.astype(np.float32), tri_verts=f.astype(np.uint32)))
    if m.status().name != "NoError":
        raise SystemExit(f"Scan ist kein geschlossenes Netz: {m.status()}")

    # Strahlen nur durch die Schale schicken (kurze Strecke = wenige Kandidaten-Dreiecke)
    rr = np.linalg.norm(xa, axis=1)
    r0, r1 = float(rr.min()) - 0.5, float(rr.max()) + 0.5
    lat = (np.arange(N_LAT) + 0.5) / N_LAT * np.pi - np.pi / 2
    lon = np.arange(N_LON) / N_LON * 2 * np.pi
    cx, cy = np.cos(lon).tolist(), np.sin(lon).tolist()
    radius = np.zeros((N_LAT, N_LON), np.float32)
    multi = 0
    for i, la in enumerate(lat):
        cl, sl = float(np.cos(la)), float(np.sin(la))
        for j in range(N_LON):
            dx, dy = cl * cx[j], cl * cy[j]
            hits = m.ray_cast((r0 * dx, r0 * dy, r0 * sl), (r1 * dx, r1 * dy, r1 * sl))
            if hits:                                     # distance = Anteil der Strecke
                radius[i, j] = r0 + (r1 - r0) * max(h.distance for h in hits)
                multi += len(hits) > 1
    if (radius <= 0).any():
        raise SystemExit("Strahlen ohne Treffer -- Schwerpunkt liegt nicht im Scan?")
    print(f"Strahlen mit mehreren Treffern (Hinterschnitte): {100 * multi / radius.size:.2f} %")

    # Ellipsoid anpassen: 1/R^2 = (dx/a)^2 + (dy/b)^2 + (dz/c)^2
    LAT, LON = np.meshgrid(lat, lon, indexing="ij")
    d2 = np.stack([(np.cos(LAT) * np.cos(LON)) ** 2, (np.cos(LAT) * np.sin(LON)) ** 2, np.sin(LAT) ** 2], -1)
    w = np.cos(LAT).reshape(-1)                          # flaechengewichtet
    coef, *_ = np.linalg.lstsq(d2.reshape(-1, 3) * w[:, None], (1.0 / radius.reshape(-1) ** 2) * w, rcond=None)
    semi = 1.0 / np.sqrt(coef)
    relief = radius / (1.0 / np.sqrt(d2 @ coef))
    print(f"Ellipsoid-Halbachsen x/y/z: {semi.round(2)} mm, Relief {relief.min():.3f} .. {relief.max():.3f}")
    np.savez_compressed(dst, relief=relief.astype(np.float16), semi_axes=semi.astype(np.float32),
                        source="Walnut 3D scan by DocMike, printables.com/model/214774, CC BY-NC-SA 4.0")
    print("geschrieben:", dst)


if __name__ == "__main__":
    main()
