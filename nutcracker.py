#!/usr/bin/env python3
"""Walnuss-Nussknacker -- parametrischer Generator.

Erzeugt einen walnussfoermigen Schraub-Nussknacker, dessen Koerper *auf dem
Ruecken liegend* gedruckt wird: Die Schraubenachse liegt in der Druckebene,
die Zugkraft im Koerper laeuft dadurch entlang der Lagen statt quer dazu.

Koordinaten des Koerpers (= Drucklage):
    z  nach oben, Druckbett bei z = 0 (flacher Ruecken)
    y  Schraubenachse, +y = Seite, an der die Schraube eingedreht wird
    x  quer

Koordinaten der Schraube (= Drucklage): Griff liegt auf dem Bett (z = 0),
Gewinde zeigt nach oben.

Aufruf:  python3 nutcracker.py [ausgabe-ordner]
"""
from __future__ import annotations

import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, FillRule, JoinType, Manifold, Mesh


# --------------------------------------------------------------------------
# Parameter (alle Masse in mm)
# --------------------------------------------------------------------------
@dataclass
class Params:
    # --- Nusskammer -------------------------------------------------------
    cav_w: float = 47.0          # Breite (x)
    cav_len: float = 47.0        # Laenge (y) = groesste Oeffnung Amboss <-> Schraube
    cav_depth: float = 20.0      # Tiefe unterhalb der Schraubenachse
    cav_exp: float = 2.7         # Superellipsen-Exponent (2 = Ellipse, gross = Kasten)
    floor_t: float = 6.0         # Rueckwand unter der Kammer
    rim_z: float = 40.0          # Hoehe der Kammerkante an der tiefsten Stelle
    rim_scoop_r: float = 70.0    # Radius des Bogens, dem die Kammerkante folgt
    rim_slope_deg: float = 50.0  # Neigung der Randschraege (steil = keine sichtbaren Treppenstufen)

    # --- Walnuss-Huelle ---------------------------------------------------
    ax: float = 35.0             # halbe Breite
    ay_top: float = 47.0         # Halbachse zur Schraubenseite (wird bei y_top gekappt)
    ay_bot: float = 42.0         # Halbachse zum runden Ende hin
    az_front: float = 28.0       # Halbachse oberhalb der Schraubenachse
    az_back: float = 32.0        # Halbachse unterhalb der Schraubenachse
    exp_y_top: float = 2.4       # >2 macht das Ende stumpfer (Platz fuer das Gewinde)
    exp_y_bot: float = 2.2
    y_top: float = 40.0          # plane Flaeche, an der die Schraube eintritt
    stand_flat_y: float | None = None   # z. B. -40.5 kappt die Spitze zu einer Standflaeche
    # --- massiv gedruckte Zonen (Modifier mit 100 % Fuellung) -------------
    sleeve_wall: float = 5.0     # Huelse um das Gewinde: so weit ueber den Gewinde-Aussenradius hinaus
    anvil_r: float = 15.0        # Radius des Blocks hinter der Ambossmulde
    anvil_block: float = 12.0    # ... und seine Tiefe
    shank_into_head: float = 8.0 # so tief reicht der massive Schaft in den Griff
    boss_len: float = 20.0       # Laenge des Gewindeblocks
    boss_wall: float = 5.5       # Mindestwand um das Gewinde
    cav_wall: float = 6.5        # Wand um die Nusskammer, auf die das Relief aufgesetzt wird
    cav_wall_min: float = 4.0    # harte Untergrenze dieser Wand, auch in tiefen Furchen
    max_overhang_deg: float = 43.0   # steilster Ueberhang der Aussenhaut

    # --- Walnuss-Oberflaeche ---------------------------------------------
    relief_map: str = "walnut_relief.npz"   # Karte aus dem 3D-Scan; fehlt sie, wird gerechnet
    relief_gain: float = 1.25    # 1 = Relief wie gescannt (mit der Nuss vergroessert)
    relief_flip: bool = False    # andere Schalenhaelfte nach vorn
    relief_tip_down: bool = True # Spitze der Nuss am runden Ende, Stielansatz an der Schraube
    underside_fade: float = 0.45 # Rest-Relief auf der Unterseite (Druckbarkeit)
    # nur fuer das gerechnete Ersatz-Relief:
    seed: int = 7
    n_cells: int = 84            # Anzahl der Felder zwischen den Rippen
    pit_depth: float = 2.4       # Tiefe der Felder zwischen den Rippen
    ridge_w: float = 0.095       # Rippenbreite (Anteil am Nussradius)
    wrinkle_depth: float = 0.45  # feine Runzeln
    lump_amp: float = 0.9        # grobe Beulen
    seam_h: float = 1.4          # Hoehe der Naht
    seam_w: float = 3.0          # halbe Breite der Naht

    # --- Gravur auf der Rueckseite ---------------------------------------
    engraving: str = "gravur.json"   # Motiv fuer die Druckbett-Seite; "" = keine Gravur
    engrave_depth: float = 0.6   # Tiefe der Gravur (3 Schichten bei 0,2 mm)

    # --- Gewinde ----------------------------------------------------------
    d_major: float = 22.0        # Aussendurchmesser Schraube
    pitch: float = 5.0
    thread_h: float = 2.2        # Gewindetiefe
    flank_deg: float = 35.0      # Flankenwinkel gegen die Radialebene
    crest_w: float = 0.8         # Breite der Gewindespitze
    clearance: float = 0.35      # Spiel (radial bzw. senkrecht zur Flanke)

    # --- Schraube ---------------------------------------------------------
    head_h: float = 16.0
    hub_r: float = 14.0
    wing_reach: float = 44.0     # Griff-Halbspannweite
    neck_h: float = 1.8          # Kegel zwischen Griff und Gewinde
    min_gap: float = 10.0        # kleinste Oeffnung Amboss <-> Schraube (Haselnuss)
    cup_r: float = 14.0          # Kugelradius der Mulden (Schraube + Amboss)
    cup_depth: float = 1.6
    anvil_cup_depth: float = 2.5
    anvil_cup_r: float = 14.0

    # --- Netzaufloesung ---------------------------------------------------
    n_lon: int = 512
    n_lat: int = 256
    thread_seg: int = 96
    thread_div: int = 32         # Unterteilungen pro Gewindegang

    # --- abgeleitete Groessen --------------------------------------------
    @property
    def z_axis(self) -> float:            # Hoehe der Schraubenachse ueber dem Bett
        return self.floor_t + self.cav_depth

    @property
    def y_boss(self) -> float:            # Innenseite des Gewindeblocks
        return self.y_top - self.boss_len

    @property
    def y_anvil(self) -> float:           # Ambossflaeche
        return self.y_boss - self.cav_len

    @property
    def y_cav(self) -> float:             # Kammermitte
        return 0.5 * (self.y_boss + self.y_anvil)

    @property
    def r_major(self) -> float:
        return 0.5 * self.d_major

    @property
    def r_minor(self) -> float:
        return self.r_major - self.thread_h

    @property
    def flank_run(self) -> float:         # axiale Laenge einer Flanke
        return self.thread_h * np.tan(np.radians(self.flank_deg))

    @property
    def thread_len(self) -> float:        # Gewindelaenge der Schraube
        return self.y_top - (self.y_anvil + self.min_gap)

    @property
    def screw_len(self) -> float:         # Gesamthoehe der Schraube im Druck
        return self.head_h + self.neck_h + self.thread_len


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------
def perlin3(p: np.ndarray, seed: int) -> np.ndarray:
    """Klassisches 3D-Gradientenrauschen, Wertebereich etwa [-1, 1]."""
    rng = np.random.default_rng(seed)
    perm = rng.permutation(256)
    perm = np.concatenate([perm, perm, perm[:2]])
    grads = rng.normal(size=(256, 3))
    grads /= np.linalg.norm(grads, axis=1)[:, None]

    pi = np.floor(p).astype(np.int64)
    pf = p - pi
    pi &= 255
    u = pf * pf * pf * (pf * (pf * 6 - 15) + 10)
    x, y, z = pi[..., 0], pi[..., 1], pi[..., 2]
    fx, fy, fz = pf[..., 0], pf[..., 1], pf[..., 2]

    def corner(dx, dy, dz):
        g = grads[perm[perm[perm[x + dx] + y + dy] + z + dz]]
        return g[..., 0] * (fx - dx) + g[..., 1] * (fy - dy) + g[..., 2] * (fz - dz)

    def lerp(a, b, t):
        return a + (b - a) * t

    x0 = lerp(lerp(corner(0, 0, 0), corner(1, 0, 0), u[..., 0]),
              lerp(corner(0, 1, 0), corner(1, 1, 0), u[..., 0]), u[..., 1])
    x1 = lerp(lerp(corner(0, 0, 1), corner(1, 0, 1), u[..., 0]),
              lerp(corner(0, 1, 1), corner(1, 1, 1), u[..., 0]), u[..., 1])
    return 1.6 * lerp(x0, x1, u[..., 2])


def smooth_max(a, b, k):
    return 0.5 * (a + b + np.sqrt((a - b) ** 2 + k * k))


def to_manifold(verts: np.ndarray, tris: np.ndarray) -> Manifold:
    m = Manifold(Mesh(vert_properties=np.asarray(verts, dtype=np.float32),
                      tri_verts=np.asarray(tris, dtype=np.uint32)))
    if m.status().name != "NoError" or m.is_empty():
        raise RuntimeError(f"ungueltiges Netz: {m.status()}")
    return m


def ring_stack(rings: list[np.ndarray], bottom: np.ndarray, top: np.ndarray) -> Manifold:
    """Geschlossener Koerper aus gleich langen Ringen (gegen den Uhrzeigersinn
    von +z gesehen, von unten nach oben) mit je einem Punkt als Deckel."""
    n = len(rings[0])
    verts = [bottom[None, :]] + rings + [top[None, :]]
    verts = np.vstack(verts)
    idx = np.arange(n)
    nxt = (idx + 1) % n
    tris = [np.c_[np.zeros(n, int), 1 + nxt, 1 + idx]]
    for k in range(len(rings) - 1):
        a, b = 1 + k * n, 1 + (k + 1) * n
        tris.append(np.c_[a + idx, a + nxt, b + nxt])
        tris.append(np.c_[a + idx, b + nxt, b + idx])
    last = 1 + (len(rings) - 1) * n
    tris.append(np.c_[np.full(n, len(verts) - 1), last + idx, last + nxt])
    return to_manifold(verts, np.vstack(tris))


def along_y(m: Manifold) -> Manifold:
    """Dreht einen entlang +z aufgebauten Koerper so, dass +z auf +y zeigt."""
    return m.rotate((-90.0, 0.0, 0.0))


# --------------------------------------------------------------------------
# Walnuss-Aussenhaut
# --------------------------------------------------------------------------
def walnut_relief(p: Params, d: np.ndarray) -> np.ndarray:
    """Relief der Schale (mm, <= 0 = Vertiefung) fuer Einheitsrichtungen d.
    Ein verzerrtes Voronoi-Netz liefert erhabene Rippen mit Mulden dazwischen."""
    from scipy.spatial import cKDTree

    rng = np.random.default_rng(p.seed)
    stretch = np.array([1.0, 0.6, 1.0])                  # Felder laengs der Nuss gestreckt
    i = np.arange(p.n_cells) + 0.5                       # Fibonacci-Kugel + Zufall
    phi = np.arccos(1 - 2 * i / p.n_cells)
    th = np.pi * (1 + 5 ** 0.5) * i
    seeds = np.c_[np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)]
    seeds += rng.normal(scale=0.11, size=seeds.shape)
    seeds /= np.linalg.norm(seeds, axis=1)[:, None]

    s = d * stretch
    warp = np.stack([perlin3(s * 2.6 + 11.3, p.seed + 1),
                     perlin3(s * 2.6 + 27.9, p.seed + 2),
                     perlin3(s * 2.6 + 43.1, p.seed + 3)], -1)
    q = s + 0.085 * warp
    dist, _ = cKDTree(seeds * stretch).query(q.reshape(-1, 3), k=2)
    border = (dist[:, 1] - dist[:, 0]).reshape(d.shape[:-1])    # 0 auf der Rippe

    height = 0.75 + 0.25 * perlin3(s * 1.9 + 7.7, p.seed + 4)   # Rippen ungleich hoch
    pits = -p.pit_depth * height * (1 - np.exp(-(border / p.ridge_w) ** 2))
    n2 = perlin3(s * 7.5 + 1.5 * warp + 5.0, p.seed + 6)
    wrinkles = -p.wrinkle_depth * np.exp(-(n2 / 0.18) ** 2)
    lumps = p.lump_amp * perlin3(s * 1.5 + 3.0, p.seed + 7)
    return pits + wrinkles + lumps


def scan_relief(p: Params, lat: np.ndarray, lon: np.ndarray) -> np.ndarray | None:
    """Relief aus dem 3D-Scan als Faktor auf den Radius (1 = glattes Ellipsoid).
    Die Karte liegt im selben Kugelraster wie die Aussenhaut: Pol = Nahtnormale,
    Laenge 90 Grad = Spitze der Nuss.  Gibt None zurueck, wenn die Karte fehlt."""
    from scipy.ndimage import gaussian_filter

    path = Path(__file__).resolve().parent / p.relief_map
    if not p.relief_map or not path.exists():
        return None
    rel = np.load(path)["relief"].astype(np.float64)
    n_la, n_lo = rel.shape
    if p.relief_flip:                                   # halbe Drehung um die Nussachse
        rel = rel[::-1, ::-1]
        rel = np.roll(rel, n_lo // 2 + 1, axis=1)
    if p.relief_tip_down:                               # halbe Drehung um die Nahtnormale
        rel = np.roll(rel, n_lo // 2, axis=1)
    # leicht glaetten, damit das groebere Netz der Aussenhaut nicht flimmert
    sig = 0.4 * (np.pi / p.n_lat) / (np.pi / n_la)
    rel = gaussian_filter(rel, sigma=sig, mode=("nearest", "wrap"))
    fi = np.clip((lat + np.pi / 2) / np.pi * n_la - 0.5, 0, n_la - 1)
    fj = (lon / (2 * np.pi) * n_lo) % n_lo
    i0 = np.floor(fi).astype(int)
    i1 = np.minimum(i0 + 1, n_la - 1)
    j0 = np.floor(fj).astype(int) % n_lo
    j1 = (j0 + 1) % n_lo
    ti, tj = fi - i0, fj - np.floor(fj)
    return ((1 - ti) * (1 - tj) * rel[i0, j0] + (1 - ti) * tj * rel[i0, j1]
            + ti * (1 - tj) * rel[i1, j0] + ti * tj * rel[i1, j1])


def cavity_guard(p: Params, d: np.ndarray, wall: float) -> np.ndarray:
    """Radius (ab Nussmitte) einer Huelle, die die Nusskammer mit der Wandstaerke
    `wall` umschliesst.  Die Aussenhaut darf nirgends darunter liegen."""
    cx, cy, cz, e = 0.5 * p.cav_w + wall, 0.5 * p.cav_len + wall, p.cav_depth + wall, p.cav_exp
    lo = np.zeros(d.shape[:-1])
    hi = np.full_like(lo, 90.0)
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        x, y, z = mid * d[..., 0], mid * d[..., 1], mid * d[..., 2]
        q = np.abs(x / cx) ** e + np.abs((y - p.y_cav) / cy) ** e
        above = (q < 1) & (z + p.z_axis < scoop_z(p, y) - 2.0)    # bis knapp unter die Kammerkante
        below = q + np.abs(z / cz) ** e < 1
        inside = np.where(z >= 0, above, below)
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    return lo


def walnut_outer(p: Params) -> Manifold:
    """Aussenhaut als radial verschobene Kugel um (0, 0, z_axis)."""
    lat = np.linspace(-np.pi / 2, np.pi / 2, p.n_lat + 1)[1:-1]
    lon = np.linspace(0, 2 * np.pi, p.n_lon, endpoint=False)
    LAT, LON = np.meshgrid(lat, lon, indexing="ij")
    d = np.stack([np.cos(LAT) * np.cos(LON), np.cos(LAT) * np.sin(LON), np.sin(LAT)], -1)
    dx, dy, dz = d[..., 0], d[..., 1], d[..., 2]

    # Grundform: |y/ay|^e + (x/ax)^2 + (z/az)^2 = 1; vorne/hinten und
    # Schraubenseite/rundes Ende haben jeweils eigene Halbachsen
    az = np.where(dz >= 0, p.az_front, p.az_back)
    ay = np.where(dy >= 0, p.ay_top, p.ay_bot)
    ey = np.where(dy >= 0, p.exp_y_top, p.exp_y_bot)
    q = (dx / p.ax) ** 2 + (dz / az) ** 2
    lo = np.zeros_like(q)
    hi = np.minimum(1.0 / np.sqrt(np.maximum(q, 1e-12)), ay / np.maximum(np.abs(dy), 1e-9))
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        inside = np.abs(mid * dy / ay) ** ey + mid * mid * q < 1.0
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    R = 0.5 * (lo + hi)
    R = smooth_max(R, cavity_guard(p, d, p.cav_wall), 2.0)   # Platz fuer die Kammerwand

    # Relief; auf der Unterseite abgeschwaecht, damit dort nichts frei haengt
    under = np.clip((-np.degrees(LAT) - 8.0) / 30.0, 0.0, 1.0)
    under = under * under * (3 - 2 * under)
    fade = 1 - (1 - p.underside_fade) * under
    scan = scan_relief(p, LAT, LON)
    if scan is not None:
        # echte Schale: Naht, Rippen und Furchen kommen aus dem 3D-Scan
        R = R * (1 + p.relief_gain * (scan - 1) * fade)
    else:
        # Ersatz ohne Scan: gerechnetes Relief und ein glatter Wulst als Naht
        seam_dist = R * np.arcsin(np.clip(dz, -1, 1))
        seam = np.exp(-(seam_dist / p.seam_w) ** 2)
        calm = np.exp(-(seam_dist / (1.5 * p.seam_w)) ** 2)
        R = R + walnut_relief(p, d) * fade * (1 - calm) + p.seam_h * seam

    R = smooth_max(R, cavity_guard(p, d, p.cav_wall_min), 0.6)

    # Gewindeblock: garantierte Mindestwand um das Gewinde (Zylinder um die y-Achse)
    r_boss = p.r_major + p.clearance + p.boss_wall
    rho = np.sqrt(dx * dx + dz * dz)
    R_boss = np.where(dy > 0.05,
                      np.minimum(r_boss / np.maximum(rho, 1e-9), (p.y_top + 3.0) / np.maximum(dy, 1e-9)),
                      0.0)
    R = np.where(dy > 0.05, smooth_max(R, R_boss, 1.2), R)

    # Druckbarkeit: kein Ueberhang steiler als max_overhang_deg.  Entlang jedes
    # Meridians von oben nach unten das Maximum von R*(cos - k sin) mitfuehren;
    # das fuellt alles, was sonst frei in der Luft haengen wuerde (flacher
    # Kegel zum Bett hin statt Kugelunterseite).
    k = np.tan(np.radians(p.max_overhang_deg))
    w = np.cos(LAT) - k * np.sin(LAT)
    use = w[:, 0] > 0.12
    g = np.where(use[:, None], R * w, -np.inf)
    run = np.maximum.accumulate(g[::-1], axis=0)[::-1]
    R = np.where(use[:, None], np.maximum(R, run / np.where(use[:, None], w, 1.0)), R)

    verts = d * R[..., None]
    south = np.array([0.0, 0.0, -R[0].mean()])
    north = np.array([0.0, 0.0, R[-1].mean()])
    m = ring_stack([verts[i] for i in range(verts.shape[0])], south, north)
    return m.translate((0.0, 0.0, p.z_axis))


# --------------------------------------------------------------------------
# Nusskammer
# --------------------------------------------------------------------------
def cavity_outline(p: Params, n: int = 240, grow: float = 0.0) -> np.ndarray:
    """Superellipse der Kammer in der xy-Ebene (relativ zur Kammermitte)."""
    psi = np.linspace(0, 2 * np.pi, n, endpoint=False)
    cx, cy, e = 0.5 * p.cav_w, 0.5 * p.cav_len, p.cav_exp
    r = (np.abs(np.cos(psi) / cx) ** e + np.abs(np.sin(psi) / cy) ** e) ** (-1.0 / e)
    r = r + grow
    return np.c_[r * np.cos(psi), r * np.sin(psi)]


def scoop_z(p: Params, y: np.ndarray) -> np.ndarray:
    """Hoehe der Kammerkante ueber dem Bett an der Stelle y (flacher Bogen)."""
    dy = np.clip(y - p.y_cav, -0.98 * p.rim_scoop_r, 0.98 * p.rim_scoop_r)
    return p.rim_z + p.rim_scoop_r - np.sqrt(p.rim_scoop_r ** 2 - dy ** 2)


def cavity(p: Params) -> Manifold:
    """Schale unterhalb der Achse, senkrechte Waende darueber (stuetzfrei)."""
    n = 240
    psi = np.linspace(0, 2 * np.pi, n, endpoint=False)
    cx, cy, cz, e = 0.5 * p.cav_w, 0.5 * p.cav_len, p.cav_depth, p.cav_exp
    rings = []
    for beta in np.linspace(0, np.pi / 2, 40)[1:]:     # 0 = Boden, 90 Grad = Aequator
        dx, dy, dz = np.sin(beta) * np.cos(psi), np.sin(beta) * np.sin(psi), -np.cos(beta)
        r = (np.abs(dx / cx) ** e + np.abs(dy / cy) ** e + np.abs(dz / cz) ** e) ** (-1.0 / e)
        rings.append(np.c_[r * dx, r * dy + p.y_cav, r * dz + p.z_axis])
    top = rings[-1].copy()
    top[:, 2] = p.z_axis + 80.0
    rings.append(top)
    bowl = ring_stack(rings, np.array([0.0, p.y_cav, p.z_axis - cz]),
                      np.array([0.0, p.y_cav, p.z_axis + 80.0]))

    # Randschraege: von der Kammerkante steigt ein steiler Trichter nach aussen
    # an, bis er die Nussschale trifft.  So gibt es oben keine flache Flaeche,
    # auf der die Lagen als Treppe sichtbar waeren.
    g = 18.0
    a = cavity_outline(p, n)
    b = cavity_outline(p, n, grow=g)
    ya, yb = a[:, 1] + p.y_cav, b[:, 1] + p.y_cav
    edge = scoop_z(p, ya)
    r0 = np.c_[a[:, 0], ya, np.full(n, p.z_axis)]
    r1 = np.c_[a[:, 0], ya, edge]
    r2 = np.c_[b[:, 0], yb, edge + g * np.tan(np.radians(p.rim_slope_deg))]
    r3 = np.c_[b[:, 0], yb, np.full(n, p.z_axis + 80.0)]
    funnel = ring_stack([r0, r1, r2, r3], np.array([0.0, p.y_cav, p.z_axis]),
                        np.array([0.0, p.y_cav, p.z_axis + 80.0]))
    return bowl + funnel


# --------------------------------------------------------------------------
# Gewinde
# --------------------------------------------------------------------------
def thread_solid(p: Params, length: float, offset: float = 0.0) -> Manifold:
    """Rechtsgewinde entlang +z ab z = 0.  offset > 0 blaest das Profil rundum
    auf -- so entsteht das Werkzeug fuer das Innengewinde mit Spiel."""
    r_root = p.r_minor + offset
    r_crest = p.r_major + offset
    grow = 2.0 * offset * np.tan(np.radians(0.5 * (90.0 - p.flank_deg)))
    crest_w = p.crest_w + grow
    th = np.linspace(0, 2 * np.pi, p.thread_seg, endpoint=False)
    s = th / (2 * np.pi) * p.pitch
    s = np.minimum(s, p.pitch - s)                      # Abstand zur Spitzenmitte
    t = np.clip((s - 0.5 * crest_w) / p.flank_run, 0.0, 1.0)
    r = r_crest - t * (r_crest - r_root)
    cs = CrossSection([np.c_[r * np.cos(th), r * np.sin(th)]])
    div = max(2, int(round(length / p.pitch * p.thread_div)))
    return cs.extrude(length, div, 360.0 * length / p.pitch)


def revolve(profile: list[tuple[float, float]], seg: int = 96) -> Manifold:
    """Rotationskoerper um z aus einem (r, z)-Polygon."""
    return CrossSection([np.array(profile, dtype=float)]).revolve(seg)


# --------------------------------------------------------------------------
# Koerper
# --------------------------------------------------------------------------
def engraving(p: Params) -> Manifold | None:
    """Gravur fuer die flache Rueckseite als Abzugskoerper (None, wenn keine Datei da ist).
    Die Datei enthaelt Konturen in Koerper-xy; innen/aussen ergibt sich aus der
    Gerade-Ungerade-Regel, Loecher in Buchstaben bleiben also stehen."""
    path = Path(__file__).resolve().parent / p.engraving
    if not p.engraving or not path.exists():
        return None
    polys = [np.array(q, dtype=float) for q in json.loads(path.read_text())["polygons_xy"]]
    cs = CrossSection(polys, FillRule.EvenOdd)
    return cs.extrude(p.engrave_depth + 1.0).translate((0.0, 0.0, -1.0))


def make_body(p: Params) -> Manifold:
    body = walnut_outer(p)
    body = body.trim_by_plane((0.0, 0.0, 1.0), 0.0)            # Ruecken = Druckbett
    body = body.trim_by_plane((0.0, -1.0, 0.0), -p.y_top)      # Schraubenseite
    if p.stand_flat_y is not None:                             # sonst bleibt die Spitze der Nuss
        body = body.trim_by_plane((0.0, 1.0, 0.0), p.stand_flat_y)
    body = body - cavity(p)

    c = p.clearance
    R = p.r_major + c                                           # Kernloch + Gewindegrund
    y0 = p.y_boss - 4.0
    L = p.y_top - y0 + 1.0
    hole = along_y(thread_solid(p, L, offset=c)).translate((0.0, y0, p.z_axis))

    # Einlauf-Senkung an der Schraubenseite (passt zum Kegel unter dem Griff)
    sink = p.neck_h + 0.4
    cone = revolve([(0, -1.0), (R + sink + 1.0, -1.0), (R, sink), (0, sink)])
    cone = along_y(cone.mirror((0.0, 0.0, 1.0))).translate((0.0, p.y_top, p.z_axis))

    # "Tropfen"-Dach: im obersten Viertel des liegenden Gewindelochs werden die
    # Gaenge weggenommen und das Dach unter 45 Grad geschlossen -- dort wuerde
    # sonst durchhaengendes Material das Gewinde klemmen lassen.
    s45 = np.sqrt(0.5)
    top_half = (2 * s45 - 1.0) * R - 0.25
    roof = CrossSection([np.array([(-s45 * R, s45 * R), (s45 * R, s45 * R),
                                   (top_half, R + 0.25), (-top_half, R + 0.25)])])
    roof = along_y(roof.extrude(L)).translate((0.0, y0, p.z_axis))
    # along_y dreht das Profil-(y) auf -z, daher spiegeln:
    roof = roof.mirror((0.0, 0.0, 1.0)).translate((0.0, 0.0, 2 * p.z_axis))

    body = body - hole - cone - roof

    gravur = engraving(p)
    if gravur is not None:
        body = body - gravur

    # Mulde im Amboss, haelt die Nuss mittig
    cup = Manifold.sphere(p.anvil_cup_r, 96).translate(
        (0.0, p.y_anvil + p.anvil_cup_r - p.anvil_cup_depth, p.z_axis))
    body = body - cup
    return body


# --------------------------------------------------------------------------
# Schraube
# --------------------------------------------------------------------------
def rounded_prism(cs: CrossSection, h: float, r_top: float, c_bot: float) -> Manifold:
    """Konvexes Prisma mit 45-Grad-Fase unten (Bett) und Rundung oben."""
    layers = [(0.0, -c_bot), (c_bot, 0.0), (h - r_top, 0.0)]
    for a in np.linspace(0, np.pi / 2, 8)[1:]:
        layers.append((h - r_top + r_top * np.sin(a), -r_top * (1 - np.cos(a))))
    pts = []
    for z, off in layers:
        poly = cs.offset(off, JoinType.Round, 2.0, 96).to_polygons()[0]
        pts.append(np.c_[poly, np.full(len(poly), z)])
    return Manifold.hull_points(np.vstack(pts))


def make_screw(p: Params) -> Manifold:
    # Griff: Nabe + zwei Fluegel, die zum Ende hin breiter werden
    hub = rounded_prism(CrossSection.circle(p.hub_r, 128), p.head_h, 1.2, 1.5)
    tip_r = 8.5
    wing = CrossSection.batch_hull([
        CrossSection.circle(6.0, 64).translate((7.0, 0.0)),
        CrossSection.circle(tip_r, 64).translate((p.wing_reach - tip_r, 0.0)),
    ])
    wing_r = rounded_prism(wing, p.head_h, 3.0, 1.5)
    head = hub + wing_r + wing_r.mirror((1.0, 0.0, 0.0))

    # Kegel als Uebergang (sitzt in der Senkung des Koerpers)
    z1 = p.head_h
    z2 = z1 + p.neck_h
    r_neck = p.r_major + p.neck_h
    neck = revolve([(0, z1 - 0.5), (r_neck, z1 - 0.5), (r_neck, z1), (p.r_major, z2), (0, z2)])

    # Gewinde, zur Spitze hin unter 45 Grad auslaufend, glatter Zapfen vorn
    L = p.thread_len
    z3 = z2 + L
    pin = 2.0                                   # glatter Zapfen
    r_pin = p.r_minor - 0.3
    env = revolve([(0, z2 - 0.5), (p.r_major + 1.0, z2 - 0.5),
                   (p.r_major + 1.0, z3 - pin - (p.r_major + 1.0 - r_pin)),
                   (r_pin, z3 - pin), (r_pin, z3 - 0.8), (r_pin - 0.8, z3), (0, z3)])
    thread = thread_solid(p, L + 0.5).translate((0.0, 0.0, z2 - 0.5)) ^ env

    cup = Manifold.sphere(p.cup_r, 96).translate((0.0, 0.0, z3 + p.cup_r - p.cup_depth))
    return head + neck + thread - cup


def screw_in_body(p: Params, screw: Manifold, gap: float, turn_deg: float = 0.0) -> Manifold:
    """Schraube in Koerper-Koordinaten; gap = Abstand Schraubenspitze <-> Amboss."""
    y_tip = p.y_anvil + gap
    s = screw.rotate((0.0, 0.0, turn_deg))
    return s.rotate((90.0, 0.0, 0.0)).translate((0.0, y_tip + p.screw_len, p.z_axis))


# --------------------------------------------------------------------------
# Modifier (Bereiche fuer 100 % Infill)
# --------------------------------------------------------------------------
def make_modifiers(p: Params) -> dict[str, Manifold]:
    r_sleeve = p.r_major + p.clearance + p.sleeve_wall
    boss = Manifold.cylinder(p.boss_len + 3.0, r_sleeve, r_sleeve, 64)
    boss = along_y(boss).translate((0.0, p.y_boss - 1.0, p.z_axis))
    anvil = Manifold.cylinder(p.anvil_block + 3.0, p.anvil_r, p.anvil_r, 64)
    anvil = along_y(anvil).translate((0.0, p.y_anvil - p.anvil_block, p.z_axis))
    shank = Manifold.cylinder(p.shank_into_head + p.neck_h + p.thread_len + 1.0, p.r_major + 3.0, p.r_major + 3.0, 64)
    shank = shank.translate((0.0, 0.0, p.head_h - p.shank_into_head))
    return {"body_gewinde": boss, "body_amboss": anvil, "screw_schaft": shank}


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def mesh_arrays(m: Manifold) -> tuple[np.ndarray, np.ndarray]:
    """Ecken und Dreiecke; in float32 zusammenfallende Ecken werden vereinigt
    und die dabei entstehenden Null-Flaechen-Dreiecke entfernt."""
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    v, inv = np.unique(v, axis=0, return_inverse=True)
    t = inv.reshape(-1)[np.asarray(mesh.tri_verts)]
    keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    return v.astype(np.float64), t[keep]


def write_stl(path: Path, m: Manifold) -> None:
    v, t = mesh_arrays(m)
    tri = v[t].astype(np.float32)
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(nrm, axis=1)
    nrm = nrm / np.where(ln > 0, ln, 1.0)[:, None]
    rec = np.zeros(len(tri), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = nrm, tri
    with open(path, "wb") as f:
        f.write(b"Walnuss-Nussknacker".ljust(80, b" "))
        f.write(np.uint32(len(tri)).tobytes())
        f.write(rec.tobytes())


# Druckeinstellungen, die im Bambu-Studio-Projekt pro Objekt bzw. pro
# Modifier hinterlegt werden (Schluessel = Bambu-Studio-Namen).
BODY_SETTINGS = {"wall_loops": "5", "top_shell_layers": "5", "bottom_shell_layers": "5",
                 "sparse_infill_density": "25%", "sparse_infill_pattern": "gyroid",
                 "enable_support": "0"}
SCREW_SETTINGS = {"wall_loops": "4", "top_shell_layers": "5", "bottom_shell_layers": "5",
                  "sparse_infill_density": "25%", "sparse_infill_pattern": "gyroid",
                  "enable_support": "0"}
SOLID = {"sparse_infill_density": "100%", "sparse_infill_pattern": "zig-zag"}


def _mesh_xml(obj_id: int, m: Manifold, shift: np.ndarray) -> str:
    v, t = mesh_arrays(m)
    v = v + shift
    out = [f'  <object id="{obj_id}" type="model">\n   <mesh>\n    <vertices>\n']
    out.append("".join(f'     <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>\n' for x, y, z in v))
    out.append("    </vertices>\n    <triangles>\n")
    out.append("".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in t))
    out.append("    </triangles>\n   </mesh>\n  </object>\n")
    return "".join(out)


def write_3mf_bambu(path: Path, objects: list[dict]) -> None:
    """Bambu-Studio-Projekt (3MF).  Jedes Objekt besteht aus einem Druckteil und
    beliebig vielen Modifier-Koerpern mit eigenen Einstellungen."""
    ident = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
    res, items, cfg, plate, asm = [], [], [], [], []
    next_id = 1
    for n, obj in enumerate(objects):
        main = obj["parts"][0]["mesh"]
        bb = main.bounding_box()
        shift = -np.array([0.5 * (bb[0] + bb[3]), 0.5 * (bb[1] + bb[4]), bb[2]])
        part_ids = []
        for part in obj["parts"]:
            res.append(_mesh_xml(next_id, part["mesh"], shift))
            part_ids.append(next_id)
            next_id += 1
        obj_id = next_id
        next_id += 1
        comps = "".join(f'    <component objectid="{i}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
                        for i in part_ids)
        res.append(f'  <object id="{obj_id}" type="model">\n   <components>\n{comps}   </components>\n  </object>\n')
        px, py = obj["pos"]
        tr = f"1 0 0 0 1 0 0 0 1 {px:.3f} {py:.3f} 0"
        items.append(f'  <item objectid="{obj_id}" transform="{tr}" printable="1"/>\n')

        c = [f'  <object id="{obj_id}">\n   <metadata key="name" value="{obj["name"]}"/>\n'
             '   <metadata key="extruder" value="1"/>\n']
        c += [f'   <metadata key="{k}" value="{v}"/>\n' for k, v in obj.get("settings", {}).items()]
        for pid, part in zip(part_ids, obj["parts"]):
            sub = "modifier_part" if part.get("modifier") else "normal_part"
            c.append(f'   <part id="{pid}" subtype="{sub}">\n    <metadata key="name" value="{part["name"]}"/>\n'
                     f'    <metadata key="matrix" value="{ident}"/>\n')
            c += [f'    <metadata key="{k}" value="{v}"/>\n' for k, v in part.get("settings", {}).items()]
            c.append('    <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" '
                     'facets_reversed="0" backwards_edges="0"/>\n   </part>\n')
        c.append("  </object>\n")
        cfg.append("".join(c))
        plate.append(f'    <model_instance>\n     <metadata key="object_id" value="{obj_id}"/>\n'
                     f'     <metadata key="instance_id" value="0"/>\n'
                     f'     <metadata key="identify_id" value="{100 + n}"/>\n    </model_instance>\n')
        asm.append(f'   <assemble_item object_id="{obj_id}" instance_id="0" transform="{tr}" offset="0 0 0" />\n')

    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n'
             ' <metadata name="Application">BambuStudio-01.08.00.00</metadata>\n'
             ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
             ' <metadata name="Title">Walnuss-Nussknacker</metadata>\n'
             ' <resources>\n' + "".join(res) + ' </resources>\n'
             ' <build>\n' + "".join(items) + ' </build>\n</model>\n')
    settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "".join(cfg) +
                '  <plate>\n   <metadata key="plater_id" value="1"/>\n'
                '   <metadata key="plater_name" value="Nussknacker"/>\n'
                '   <metadata key="locked" value="false"/>\n' + "".join(plate) + '  </plate>\n'
                '  <assemble>\n' + "".join(asm) + '  </assemble>\n</config>\n')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
             ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
             ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
             ' <Default Extension="config" ContentType="text/xml"/>\n</Types>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n</Relationships>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", settings)


def plate_objects(parts: dict[str, Manifold]) -> list[dict]:
    """Belegung der Druckplatte (Bambu Lab A1, 256 x 256 mm)."""
    return [
        {"name": "Koerper (liegt auf dem Ruecken)", "pos": (84.0, 128.0), "settings": BODY_SETTINGS,
         "parts": [{"name": "Koerper", "mesh": parts["body"]},
                   {"name": "Gewindehuelse 100%", "mesh": parts["body_gewinde"], "modifier": True, "settings": SOLID},
                   {"name": "Ambossblock 100%", "mesh": parts["body_amboss"], "modifier": True, "settings": SOLID}]},
        {"name": "Schraube (Griff unten)", "pos": (178.0, 128.0), "settings": SCREW_SETTINGS,
         "parts": [{"name": "Schraube", "mesh": parts["screw"]},
                   {"name": "Schaft 100%", "mesh": parts["screw_schaft"], "modifier": True, "settings": SOLID}]},
    ]


def build(p: Params | None = None) -> dict[str, Manifold]:
    p = p or Params()
    parts = {"body": make_body(p), "screw": make_screw(p)}
    parts.update(make_modifiers(p))
    return parts


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    stl = out / "stl"
    stl.mkdir(parents=True, exist_ok=True)
    p = Params()
    parts = build(p)
    names = {"body": "nussknacker_koerper", "screw": "nussknacker_schraube",
             "body_gewinde": "modifier_koerper_gewinde", "body_amboss": "modifier_koerper_amboss",
             "screw_schaft": "modifier_schraube_schaft"}
    for key, m in parts.items():
        write_stl(stl / f"{names[key]}.stl", m)
        bb = m.bounding_box()
        print(f"{names[key]:28s} {m.num_tri():7d} Dreiecke  "
              f"{bb[3]-bb[0]:6.1f} x {bb[4]-bb[1]:6.1f} x {bb[5]-bb[2]:6.1f} mm  "
              f"{m.volume()/1000:6.1f} cm3")
    write_3mf_bambu(out / "nussknacker_bambu_a1.3mf", plate_objects(parts))
    print("nussknacker_bambu_a1.3mf geschrieben")


if __name__ == "__main__":
    main()
