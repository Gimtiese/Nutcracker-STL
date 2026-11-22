#!/usr/bin/env python3
"""Festigkeitsabschaetzung fuer den Nussknacker.

1) Koerper: Voxel-FEM (1 mm).  Waende und Deck-/Bodenschichten zaehlen als
   massives PLA, die Fuellung als weicheres Ersatzmaterial (E ~ Dichte^2).
   Last: Schraubenkraft F auf die Gewindeflanken gegen F auf die Ambossmulde.
2) Schraube: Handrechnung (Torsion, Druck, Biegung).

Die Zahlen sind Abschaetzungen: Materialkennwerte und vor allem die
Tragfaehigkeit der Fuellung sind grobe Annahmen, die Druckqualitaet streut.

Aufruf:  python3 tools/festigkeit.py [Varianten ...] [--bild docs/auslastung.png]
"""
import sys, time
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from scipy import ndimage
from PIL import Image, ImageDraw, ImageFont
import pyamg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nutcracker as nc  # noqa: E402

HF, K = 0.25, 4
H = HF * K                     # Kantenlaenge der FE-Voxel (mm)
ES, NU = 3000.0, 0.35          # PLA: E-Modul (MPa), Querkontraktion
F = 1000.0                     # Schraubenkraft (N)
LINE = 0.44                    # mittlere Bahnbreite (mm)
SIGMA_WAND = 40.0              # Festigkeit in der Lagenebene (MPa)
SIGMA_LAGE = 20.0              # Haftung zwischen den Lagen (MPa), Annahme

p = nc.Params()


def voxelize(body):
    bb = body.bounding_box()
    x0, y0, z0 = bb[0] - H, bb[1] - H, -H
    nx = int(np.ceil((bb[3] - x0 + H) / H)) * K
    ny = int(np.ceil((bb[4] - y0 + H) / H)) * K
    nz = int(np.ceil((bb[5] - z0 + H) / H)) * K
    occ = np.zeros((nz, ny, nx), bool)
    for k in range(nz):
        z = z0 + (k + 0.5) * HF
        if z <= 0.0 or z >= bb[5]:
            continue
        acc = np.zeros((ny, nx), bool)
        for poly in body.slice(z).to_polygons():
            im = Image.new("1", (nx, ny), 0)
            ImageDraw.Draw(im).polygon([((q[0] - x0) / HF, (q[1] - y0) / HF) for q in poly], fill=1)
            acc ^= np.asarray(im, bool)
        occ[k] = acc
    return occ, (x0, y0, z0)


def hex_ke(a, nu):
    lam = nu / ((1 + nu) * (1 - 2 * nu)); mu = 1 / (2 * (1 + nu))
    C = np.zeros((6, 6)); C[:3, :3] = lam; C[np.arange(3), np.arange(3)] += 2 * mu; C[3:, 3:] = np.eye(3) * mu
    xn = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], float)

    def Bm(xi):
        dN = 0.125 * xn * (1 + xn[:, [1, 2, 0]] * xi[[1, 2, 0]]) * (1 + xn[:, [2, 0, 1]] * xi[[2, 0, 1]]) * (2 / a)
        B = np.zeros((6, 24))
        for n in range(8):
            dx, dy, dz = dN[n]
            B[0, 3*n] = dx; B[1, 3*n+1] = dy; B[2, 3*n+2] = dz
            B[3, 3*n] = dy; B[3, 3*n+1] = dx
            B[4, 3*n+1] = dz; B[4, 3*n+2] = dy
            B[5, 3*n] = dz; B[5, 3*n+2] = dx
        return B
    g = 1 / np.sqrt(3); Ke = np.zeros((24, 24))
    for gx in (-g, g):
        for gy in (-g, g):
            for gz in (-g, g):
                B = Bm(np.array([gx, gy, gz])); Ke += B.T @ C @ B * (a / 2) ** 3
    return Ke, Bm(np.zeros(3)), C


print("Voxelisiere ...", flush=True)
t0 = time.time()
body = nc.make_body(p)
occ, (X0, Y0, Z0) = voxelize(body)
nz, ny, nx = occ.shape
dist_xy = np.zeros(occ.shape, np.float32)                       # Abstand zum Umriss innerhalb der Lage
for k in range(nz):
    if occ[k].any():
        dist_xy[k] = ndimage.distance_transform_edt(occ[k]) * HF
up = np.zeros(occ.shape, np.float32); dn = np.zeros(occ.shape, np.float32)
for k in range(1, nz):
    dn[k] = (dn[k - 1] + HF) * occ[k]
for k in range(nz - 2, -1, -1):
    up[k] = (up[k + 1] + HF) * occ[k]
dist_z = np.minimum(up, dn)                                     # senkrechter Abstand zur naechsten freien Flaeche
SKIN = 1.0                                                      # 5 Deck-/Bodenschichten
xf = (X0 + (np.arange(nx) + 0.5) * HF)[None, None, :]
yf = (Y0 + (np.arange(ny) + 0.5) * HF)[None, :, None]
zf = (Z0 + (np.arange(nz) + 0.5) * HF)[:, None, None]
r_ax2 = xf ** 2 + (zf - p.z_axis) ** 2                         # Abstand^2 zur Schraubenachse
print(f"  {occ.sum() * HF**3 / 1000:.1f} cm3 in {occ.shape} Voxeln, {time.time() - t0:.0f} s", flush=True)

Ke1, B0, C1 = hex_ke(H, NU)
nze, nye, nxe = nz // K, ny // K, nx // K


def block_mean(a):
    return a.reshape(nze, K, nye, K, nxe, K).mean(axis=(1, 3, 5), dtype=np.float32)


occ_f = block_mean(occ)
active = occ_f > 0.3
ek, ej, ei = np.nonzero(active)
ne = len(ek)
nid = lambda k, j, i: (k * (nye + 1) + j) * (nxe + 1) + i          # noqa: E731
off = [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0), (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)]   # (dk, dj, di) passend zu xn
enod = np.stack([nid(ek + a, ej + b, ei + c) for a, b, c in off], 1)
used, enod_c = np.unique(enod, return_inverse=True)
enod_c = enod_c.reshape(enod.shape)
nn = len(used)
nk, rem = np.divmod(used, (nye + 1) * (nxe + 1)); nj, ni = np.divmod(rem, nxe + 1)
xyz = np.c_[X0 + ni * H, Y0 + nj * H, Z0 + nk * H]
edof = (3 * enod_c[:, :, None] + np.arange(3)[None, None, :]).reshape(ne, 24)
cen = np.c_[X0 + (ei + 0.5) * H, Y0 + (ej + 0.5) * H, Z0 + (ek + 0.5) * H]
r_ax = np.hypot(cen[:, 0], cen[:, 2] - p.z_axis)
ang_top = np.degrees(np.arctan2(np.abs(cen[:, 0]), cen[:, 2] - p.z_axis))          # 0 = oben (Dach)
print(f"  FE-Netz: {ne} Elemente, {nn} Knoten", flush=True)

# --- Lasten ---------------------------------------------------------------
R = p.r_major + p.clearance
thr = (r_ax > p.r_minor - 0.3) & (r_ax < R + 0.9) & (cen[:, 1] > p.y_boss + 1.5) & (cen[:, 1] < p.y_top - 3.5) & (ang_top > 64)   # tragende Gaenge
roof = (r_ax > R - 0.5) & (r_ax < R + 1.6) & (cen[:, 1] > p.y_boss + 1.5) & (cen[:, 1] < p.y_top - 2.6) & (ang_top < 30)
cup_c = np.array([0.0, p.y_anvil - p.anvil_cup_depth, p.z_axis])
anv = (np.hypot(cen[:, 0], cen[:, 2] - p.z_axis) < 4.0) & (np.abs(cen[:, 1] - cup_c[1]) < 1.3)
print(f"  Lastelemente: Gewinde {thr.sum()}, Dach {roof.sum()}, Amboss {anv.sum()}", flush=True)
f = np.zeros(3 * nn)


def add(mask, vec):
    idx = np.nonzero(mask)[0]
    v = np.broadcast_to(vec, (len(idx), 3)) / (8.0 * len(idx)) if np.ndim(vec) == 1 else np.asarray(vec) / 8.0
    for c in range(3):
        np.add.at(f, 3 * enod_c[idx] + c, np.repeat(v[:, c:c+1], 8, 1))


# Flankendruck so verteilt, dass seine Resultierende auf der Schraubenachse liegt
# (die steife Schraube stuetzt sich sonst einseitig ab; ohne das Dach-Viertel
# laege der Schwerpunkt der tragenden Flanken 3 mm unter der Achse)
dx_t, dz_t = cen[thr, 0], cen[thr, 2] - p.z_axis
A_ = np.array([[np.sum(dx_t * dx_t), np.sum(dx_t * dz_t)], [np.sum(dx_t * dz_t), np.sum(dz_t * dz_t)]])
ab = np.linalg.solve(A_, -np.array([dx_t.sum(), dz_t.sum()]))
w_t = 1.0 + ab[0] * dx_t + ab[1] * dz_t
w_t /= w_t.sum()
print(f"  Flankendruck: Gewichte {w_t.min() * thr.sum():.2f} .. {w_t.max() * thr.sum():.2f} (1 = gleichmaessig)", flush=True)
add(thr, np.c_[np.zeros(thr.sum()), F * w_t, np.zeros(thr.sum())] * 1.0)
rad = np.c_[dx_t, np.zeros(thr.sum()), dz_t]; rad /= np.linalg.norm(rad, axis=1)[:, None]
frad = np.tan(np.radians(p.flank_deg)) * F * rad * w_t[:, None]
add(thr, frad)
add(roof, -frad.sum(0))                                           # Schraube stuetzt sich am Dach ab
add(anv, np.array([0.0, -F, 0.0]))
mom = np.cross(xyz - np.array([0.0, 0.0, p.z_axis]), f.reshape(-1, 3)).sum(0)
print("  Summe der Momente um die Achse (Nmm):", np.round(mom, 3), flush=True)
print("  Summe der Kraefte:", np.round(f.reshape(-1, 3).sum(0), 6), flush=True)

# Starrkoerperbewegung sperren (3 Knoten auf der Auflageflaeche, Straffaktor)
bed = np.nonzero(np.abs(xyz[:, 2]) < 1e-6)[0]
pick = lambda pt: bed[np.argmin(np.linalg.norm(xyz[bed, :2] - pt, axis=1))]     # noqa: E731
fix = [3 * pick((0, -25)) + c for c in (0, 1, 2)] + [3 * pick((0, 25)) + c for c in (0, 2)] + [3 * pick((18, 0)) + 2]
Bnull = np.zeros((3 * nn, 6))
Bnull[0::3, 0] = 1; Bnull[1::3, 1] = 1; Bnull[2::3, 2] = 1
Bnull[0::3, 3] = -xyz[:, 1]; Bnull[1::3, 3] = xyz[:, 0]
Bnull[1::3, 4] = -xyz[:, 2]; Bnull[2::3, 4] = xyz[:, 1]
Bnull[0::3, 5] = xyz[:, 2]; Bnull[2::3, 5] = -xyz[:, 0]
rows = np.repeat(edof, 24, axis=1).ravel().astype(np.int32)
cols = np.tile(edof, (1, 24)).ravel().astype(np.int32)

# Bereiche fuer die Auswertung
reg_head = cen[:, 1] > p.y_boss - 6
reg_anv = cen[:, 1] < p.y_anvil + 8
reg_mid = ~reg_head & ~reg_anv
near_thr = (r_ax < R + 3.0) & (cen[:, 1] > p.y_boss - 1)
near_cup = np.linalg.norm(cen - cup_c, axis=1) < 7.0


def infill_strength(rho):          # grobe Schaetzung der Tragfaehigkeit von Gyroid-Fuellung (MPa)
    return 36.0 * rho ** 1.5


def run(name, walls, rho0, zones):
    """zones: Liste (Maske auf dem feinen Gitter, Dichte)."""
    t0 = time.time()
    wall = occ & ((dist_xy < walls * LINE) | (dist_z < SKIN))
    rho = np.full(occ.shape, rho0, np.float32)
    for mask, d in zones:
        rho[np.broadcast_to(mask, rho.shape)] = d
    rel = np.where(wall, 1.0, rho ** 2).astype(np.float32) * occ
    E = ES * block_mean(rel)[active] / np.maximum(occ_f[active], 1e-6) * occ_f[active]
    wall_frac = block_mean(wall)[active] / occ_f[active]
    rho_e = block_mean(np.where(occ & ~wall, rho, 0))[active] / np.maximum(block_mean(occ & ~wall)[active], 1e-6)
    mass = (wall.sum() + (rho * (occ & ~wall)).sum()) * HF ** 3 / 1000 * 1.24
    Kg = sp.coo_matrix(((E[:, None] * Ke1.ravel()[None, :]).ravel(), (rows, cols)), shape=(3 * nn, 3 * nn)).tocsr()
    pen = 1e3 * Kg.diagonal().max()
    Kg = Kg + sp.coo_matrix((np.full(len(fix), pen), (fix, fix)), shape=Kg.shape).tocsr()
    ml = pyamg.smoothed_aggregation_solver(Kg.tobsr(blocksize=(3, 3)), B=Bnull, max_coarse=300)
    res = []
    u = ml.solve(f, tol=1e-8, accel='cg', maxiter=400, residuals=res)
    eps = u[edof] @ B0.T                                           # (ne, 6)
    sig = eps @ C1.T                                               # Spannung bei E = 1
    def vm(s):
        return np.sqrt(0.5 * ((s[:, 0]-s[:, 1])**2 + (s[:, 1]-s[:, 2])**2 + (s[:, 2]-s[:, 0])**2) + 3 * (s[:, 3]**2 + s[:, 4]**2 + s[:, 5]**2))
    sw = ES * sig                                                  # im massiven Material (Wand)
    vm_w = vm(sw)
    szz = sw[:, 2]; tz = np.hypot(sw[:, 4], sw[:, 5])              # ueber die Lagen: Zug, Schub
    is_wall = wall_frac > 0.5
    is_solid = ~is_wall & (rho_e > 0.95)
    is_inf = ~is_wall & ~is_solid
    vm_i = ES * rho_e ** 2 * vm(sig)                               # homogenisiert in der Fuellung
    util = vm_i / infill_strength(np.maximum(rho_e, 0.05))
    out = {"name": name, "walls": walls, "rho0": rho0, "mass_g": round(float(mass), 1), "iters": len(res)}
    far = ~near_thr & ~near_cup
    q = lambda a, m: float(np.percentile(a[m], 99.5)) if m.sum() > 50 else float('nan')      # noqa: E731
    for rn, rm in (("Kopf", reg_head), ("Mitte", reg_mid), ("Amboss", reg_anv)):
        m = rm & far
        out[rn] = {"Wand vM": q(vm_w, m & (is_wall | is_solid)), "Lagen Zug": q(np.maximum(szz, 0), m & (is_wall | is_solid)),
                   "Lagen Schub": q(tz, m & (is_wall | is_solid)), "Fuellung vM": q(vm_i, m & is_inf),
                   "Fuellung Ausl.": q(util, m & is_inf)}
    out["Gewindenaehe"] = {"Wand vM": q(vm_w, near_thr & (is_wall | is_solid)), "Fuellung Ausl.": q(util, near_thr & is_inf),
                           "Lagen Zug": q(np.maximum(szz, 0), near_thr & (is_wall | is_solid))}
    out["Ambossmulde"] = {"Wand vM": q(vm_w, near_cup & (is_wall | is_solid)), "Fuellung Ausl.": q(util, near_cup & is_inf)}
    # Aufweitung der Kammer: Verschiebung Amboss gegen Gewinde
    out["Dehnung_mm"] = float(u[1::3][np.unique(enod_c[thr])].mean() - u[1::3][np.unique(enod_c[anv])].mean())
    print(f"{name}: {time.time() - t0:.0f} s, {len(res)} Iterationen, Masse {mass:.0f} g, Aufweitung {out['Dehnung_mm']:.3f} mm", flush=True)
    out["_feld"] = np.where(is_wall | is_solid, vm_w / SIGMA_WAND, util)      # Auslastung je Element
    return out


cyl = lambda r, ya, yb: (r_ax2 < r * r) & (yf > ya) & (yf < yb)      # noqa: E731
R_SLEEVE = p.r_major + p.clearance + p.sleeve_wall
ZONEN = [(cyl(R_SLEEVE, p.y_boss - 1.0, p.y_top + 2.0), 1.0),
         (cyl(p.anvil_r, p.y_anvil - p.anvil_block, p.y_anvil + 3.0), 1.0)]
ZONEN_ALT = [(cyl(p.r_major + 9.0, p.y_boss - 5.0, p.y_top + 2.0), 1.0),
             (cyl(17.0, p.y_anvil - 14.0, p.y_anvil + 3.0), 1.0)]
VARIANTEN = [
    ("A vorher: 5 Waende, 25 %, grosse 100-%-Bloecke", 5, 0.25, ZONEN_ALT),
    ("B jetzt: 5 Waende, 25 %, Huelse + Ambossblock", 5, 0.25, ZONEN),
    ("C sparsam: 4 Waende, 25 %, Huelse + Ambossblock", 4, 0.25, ZONEN),
    ("D 4 Waende, 30 %, Huelse + Ambossblock", 4, 0.30, ZONEN),
    ("E 3 Waende, 30 %, Huelse + Ambossblock", 3, 0.30, ZONEN),
    ("F 4 Waende, 40 %, ohne massive Zonen", 4, 0.40, []),
]


def bild(feld, pfad, titel):
    """Zwei Schnitte der Auslastung als PNG."""
    U = np.full((nze, nye, nxe), np.nan, np.float32)
    U[ek, ej, ei] = feld

    def farbe(v):
        stops = [(0.0, (232, 236, 226)), (0.1, (176, 208, 150)), (0.25, (242, 214, 96)), (0.5, (233, 138, 56)), (1.0, (190, 40, 40))]
        v = np.clip(v, 0, 1); o = np.zeros(v.shape + (3,), np.float32)
        for (a, ca), (b, cb) in zip(stops[:-1], stops[1:]):
            m = (v >= a) & (v <= b); t = ((v - a) / (b - a))[m]
            o[m] = np.array(ca)[None, :] * (1 - t[:, None]) + np.array(cb)[None, :] * t[:, None]
        return o

    def tafel(A, px=9):
        im = farbe(np.nan_to_num(A, nan=0.0)); im[np.isnan(A)] = (250, 249, 245)
        return Image.fromarray(im.astype(np.uint8)).resize((A.shape[1] * px, A.shape[0] * px), Image.NEAREST)

    kz = int(round((p.z_axis - Z0) / H - 0.5)); ix = int(round((0.0 - X0) / H - 0.5))
    p1, p2 = tafel(U[kz][:, ::-1].T[::-1]), tafel(U[:, :, ix][::-1])
    try:
        gross = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-Medium.otf", 26)
        klein = ImageFont.truetype("/usr/share/fonts/opentype/inter/Inter-Regular.otf", 21)
    except OSError:
        gross = klein = ImageFont.load_default()
    img = Image.new("RGB", (max(p1.width, p2.width, 1180) + 80, p1.height + p2.height + 290), (250, 249, 245))
    dr = ImageDraw.Draw(img)
    dr.text((40, 24), titel, font=gross, fill=(30, 34, 26))
    dr.text((40, 70), "Schnitt auf Höhe der Schraubenachse (Blick von oben in der Drucklage)", font=klein, fill=(80, 88, 76))
    img.paste(p1, (40, 104))
    y2 = 104 + p1.height + 44
    dr.text((40, y2 - 34), "Längsschnitt durch die Schraubenachse (Druckbett unten)", font=klein, fill=(80, 88, 76))
    img.paste(p2, (40, y2))
    yl = y2 + p2.height + 30
    for i, (v, lab) in enumerate([(0.05, "unter 10 %"), (0.18, "10–25 %"), (0.38, "25–50 %"), (0.75, "50–100 %"), (1.0, "über 100 %")]):
        c = tuple(int(x) for x in farbe(np.array([v]))[0]); x = 40 + i * 215
        dr.rectangle([x, yl, x + 34, yl + 26], fill=c, outline=(120, 120, 110)); dr.text((x + 44, yl + 1), lab, font=klein, fill=(60, 66, 56))
    dr.text((40, yl + 40), "Auslastung = Spannung / Tragfähigkeit. Wände und massive Zonen gegen 40 MPa,", font=klein, fill=(80, 88, 76))
    dr.text((40, yl + 68), "Füllung gegen einen Schätzwert nach ihrer Dichte.", font=klein, fill=(80, 88, 76))
    img.save(pfad)
    print("Bild geschrieben:", pfad)


def schraube():
    """Handrechnung fuer die stehend gedruckte Schraube."""
    r = p.r_minor
    rm = 0.5 * (p.r_major + p.r_minor)
    lead = np.arctan(p.pitch / (2 * np.pi * rm))

    def arm(mu, mu_tip=0.3, r_tip=4.0):          # Drehmoment je Kraft (mm)
        return rm * np.tan(lead + np.arctan(mu / np.cos(np.radians(p.flank_deg)))) + mu_tip * r_tip
    print("\nSchraube: Drehmoment und Kraft")
    for mu, txt in ((0.25, "trocken"), (0.12, "geoelt")):
        print(f"  {txt:8s}: 1000 N brauchen {arm(mu):.1f} Nm; 10 Nm von Hand ergeben {10000 / arm(mu):.0f} N")
    print("Schaft (Kern r = %.1f mm): Schub zwischen den Lagen durch Torsion (MPa)" % r)
    for name, n, rho in (("4 Waende + 25 %", 4, 0.25), ("5 Waende + 30 %", 5, 0.30), ("massiv", 99, 1.0)):
        t = min(n * LINE, r); ri = r - t
        J = np.pi / 2 * (r ** 4 - ri ** 4) + rho ** 2 * np.pi / 2 * ri ** 4
        print(f"  {name:18s}: bei 5 Nm {5000 * r / J:5.1f}, bei 10 Nm {10000 * r / J:5.1f}")
    for n in (3, 4, 5):
        t = n * LINE
        J = np.pi / 2 * (p.hub_r ** 4 - (p.hub_r - t) ** 4)
        print(f"  Nabe des Griffs, {n} Waende: Schub bei 10 Nm {10000 * p.hub_r / J:.1f} MPa")


if __name__ == "__main__":
    args = sys.argv[1:]
    pfad = args[args.index("--bild") + 1] if "--bild" in args else None
    wahl = [a for a in args if len(a) == 1] or [v[0][0] for v in VARIANTEN]
    erg = [run(*v) for v in VARIANTEN if v[0][0] in wahl]
    print()
    print(f"{'bei F = 1000 N (MPa), 99,5-%-Wert':46s}" + "".join(f"{e['name'][:1]:>8s}" for e in erg))
    for reg in ("Kopf", "Mitte", "Amboss", "Gewindenaehe", "Ambossmulde"):
        for key in erg[0][reg]:
            print(f"{reg + ': ' + key:46s}" + "".join(f"{e[reg][key]:8.2f}" for e in erg))
    print(f"{'Masse Koerper (g, FE-Schaetzung)':46s}" + "".join(f"{e['mass_g']:8.0f}" for e in erg))
    print(f"{'Aufweitung Amboss-Gewinde (mm)':46s}" + "".join(f"{e['Dehnung_mm']:8.3f}" for e in erg))
    for e in erg:
        print(f"  {e['name']}")
    if pfad:
        e = next((x for x in erg if x["name"].startswith("B")), erg[0])
        bild(e["_feld"], pfad, "Auslastung bei 1000 N Schraubenkraft")
    schraube()
