"""Draw the day's link-preview card (1200x630): today's headline over the
month's activity line, in the app's colours. Uses Pillow; if Pillow is missing
the card is skipped and yesterday's stays up.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

W, H = 1200, 630
SCALE = 3  # draw large, then shrink, for smooth curves and type
FONTS = Path(__file__).resolve().parent / "fonts"

BG = (21, 19, 42)
INK = (236, 234, 246)
SOFT = (167, 163, 195)
RULE = (46, 43, 76)
HEAT = [(0.0, (76, 70, 128)), (0.3, (125, 95, 218)), (0.55, (214, 77, 158)),
        (0.8, (255, 107, 74)), (1.0, (255, 181, 71))]


def _font(name: str, size: int, **axes):
    from PIL import ImageFont

    font = ImageFont.truetype(str(FONTS / name), size * SCALE)
    order = [a["name"].decode() if isinstance(a["name"], bytes) else a["name"] for a in font.get_variation_axes()]
    defaults = {"Optical size": 12, "Weight": 400}
    wanted = {"Optical size": axes.get("opsz"), "Weight": axes.get("wght")}
    font.set_variation_by_axes([wanted.get(n) or defaults.get(n, 400) for n in order])
    return font


def _heat_at(t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    for (o0, c0), (o1, c1) in zip(HEAT, HEAT[1:]):
        if t <= o1:
            k = (t - o0) / (o1 - o0) if o1 > o0 else 0
            return tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
    return HEAT[-1][1]


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _curve(values: list[float], x0: float, x1: float, top: float, bottom: float, steps: int = 12) -> list[tuple[float, float]]:
    """Points along a monotone cubic through the values (the app draws the same curve)."""
    n = len(values)
    pts = [(x0 + (x1 - x0) * i / (n - 1), bottom - v * (bottom - top)) for i, v in enumerate(values)]
    dx = [pts[i + 1][0] - pts[i][0] for i in range(n - 1)]
    m = [(pts[i + 1][1] - pts[i][1]) / dx[i] for i in range(n - 1)]
    t = [m[0]] + [0 if m[i - 1] * m[i] <= 0 else (m[i - 1] + m[i]) / 2 for i in range(1, n - 1)] + [m[-1]]
    for i in range(n - 1):
        if m[i] == 0:
            t[i] = t[i + 1] = 0
            continue
        a, b = t[i] / m[i], t[i + 1] / m[i]
        s = a * a + b * b
        if s > 9:
            k = 3 / s ** 0.5
            t[i], t[i + 1] = k * a * m[i], k * b * m[i]
    out = []
    for i in range(n - 1):
        (xa, ya), (xb, yb), h = pts[i], pts[i + 1], dx[i]
        for j in range(steps):
            u = j / steps
            h00, h10, h01, h11 = 2*u**3 - 3*u**2 + 1, u**3 - 2*u**2 + u, -2*u**3 + 3*u**2, u**3 - u**2
            out.append((xa + h * u, h00 * ya + h10 * h * t[i] + h01 * yb + h11 * h * t[i + 1]))
    out.append(pts[-1])
    return out


def combined_pulse(day: dict) -> list[float]:
    lines = [t.get("pulse") or [] for t in day.get("topics", []) if any((t.get("pulse") or [0]))]
    if not lines:
        return []
    n = min(len(line) for line in lines)
    summed = [sum(line[i] for line in lines) for i in range(n)]
    peak = max(summed) or 1
    return [v / peak for v in summed]


def render(day: dict, out_path: Path, site_host: str = "") -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  [card] Pillow not installed; keeping the previous preview card")
        return False

    S = SCALE
    img = Image.new("RGB", (W * S, H * S), BG)
    draw = ImageDraw.Draw(img)
    margin = 80 * S

    # Top row: wordmark and date
    word = _font("Literata-latin.ttf", 54, opsz=72, wght=620)
    draw.text((margin, 50 * S), day.get("title") or "Pulse", font=word, fill=INK)
    when = date.fromisoformat(day["date"])
    label = f"{when:%A} {when.day} {when:%B}"
    small = _font("SchibstedGrotesk-latin.ttf", 26, wght=600)
    draw.text((W * S - margin, 74 * S), label, font=small, fill=SOFT, anchor="ra")

    # Headline: as large as fits in three lines
    headline = ((day.get("overview") or {}).get("headline") or "Today's brief").strip()
    for size in range(66, 43, -2):
        font = _font("Literata-latin.ttf", size, opsz=48, wght=600)
        lines = _wrap(draw, headline, font, W * S - 2 * margin)
        if len(lines) <= 3:
            break
    lines = lines[:3]
    y = 150 * S
    step = int(size * 1.14 * S)
    for line in lines:
        draw.text((margin, y), line, font=font, fill=INK)
        y += step

    # The month's activity, coloured by height like the app
    top, bottom = max(y + 24 * S, 395 * S), 560 * S
    values = combined_pulse(day)
    gradient = Image.new("RGB", (1, H * S))
    for py in range(H * S):
        t = (bottom - py) / (bottom - top) if bottom > top else 0
        gradient.putpixel((0, py), _heat_at(t))
    gradient = gradient.resize((W * S, H * S))
    if values:
        pts = _curve(values, 0, W * S, top, bottom)
        area = Image.new("L", (W * S, H * S), 0)
        ImageDraw.Draw(area).polygon(pts + [(W * S, bottom), (0, bottom)], fill=52)
        img.paste(gradient, (0, 0), area)
        stroke = Image.new("L", (W * S, H * S), 0)
        sd = ImageDraw.Draw(stroke)
        sd.line(pts, fill=255, width=7 * S, joint="curve")
        r = 3.5 * S
        for px, py in (pts[0], pts[-1]):
            sd.ellipse((px - r, py - r, px + r, py + r), fill=255)
        img.paste(gradient, (0, 0), stroke)
    draw.line([(0, bottom + S), (W * S, bottom + S)], fill=RULE, width=2 * S)

    # Footer
    foot = _font("SchibstedGrotesk-latin.ttf", 26, wght=700)
    soft = _font("SchibstedGrotesk-latin.ttf", 26, wght=600)
    base = H * S - 34 * S
    draw.text((84 * S, base), site_host or (day.get("title") or "Pulse"), font=foot, fill=INK, anchor="ls")
    draw.text((W * S - margin, base), "Daily brief from Reddit, Hacker News, GitHub and arXiv",
              font=soft, fill=SOFT, anchor="rs")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.resize((W, H), Image.LANCZOS).save(out_path, optimize=True)
    return True
