from __future__ import annotations

import base64
import html as _html
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

import folium

from ..preview.index import render_index_html

CATEGORICAL_PALETTE = [
    "#0a58ca",
    "#d9534f",
    "#5cb85c",
    "#f0ad4e",
    "#6f42c1",
    "#20c997",
    "#e83e8c",
    "#fd7e14",
    "#0dcaf0",
    "#6c757d",
]
SEQUENTIAL_COLORS = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]
DEFAULT_COLOR = "#0a58ca"
NULL_COLOR = "#9aa0a6"


def to_data_url(html: str) -> str:
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{b64}"


def wkt_polygon_coords(wkt: str) -> List[Tuple[float, float]]:
    """Exterior ring of a POLYGON WKT as [(lat, lon), ...] for folium."""
    m = re.search(r"POLYGON\s*\(\(([^)]*)\)\)", wkt, flags=re.I)
    if not m:
        return []
    pts = []
    for pair in m.group(1).split(","):
        lon, lat = pair.split()[:2]
        pts.append((float(lat), float(lon)))
    return pts


def _float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def count_plottable(rows: List[Dict[str, Any]], lat_col: str, lon_col: str) -> int:
    """How many rows have a usable coordinate pair."""
    return sum(
        1
        for r in rows
        if _float(r.get(lat_col)) is not None and _float(r.get(lon_col)) is not None
    )


def _base_map(lats: List[float], lons: List[float]) -> folium.Map:
    if lats and lons:
        center = [sum(lats) / len(lats), sum(lons) / len(lons)]
    else:
        center = [0.0, 0.0]
    return folium.Map(location=center, zoom_start=4, control_scale=True)


def _popup(row: Dict[str, Any], popup_cols: Optional[List[str]]) -> Optional[str]:
    if not popup_cols:
        return None
    parts = [f"{c}: {row.get(c)}" for c in popup_cols if c in row]
    return "<br>".join(parts) if parts else None


def _add_fences(m: folium.Map, fences: Optional[List[Dict[str, str]]]) -> None:
    for f in fences or []:
        coords = wkt_polygon_coords(f.get("wkt", ""))
        if coords:
            folium.Polygon(
                locations=coords,
                color="#d9534f",
                weight=2,
                fill=True,
                fill_opacity=0.08,
                tooltip=f.get("fence", ""),
            ).add_to(m)


def _color_mapping(
    values: List[Any],
) -> Tuple[Callable[[Any], str], List[Tuple[str, str]]]:
    """Return (value->color, legend entries). Numeric values get a graduated
    scale; everything else is treated as categories."""
    present = [v for v in values if v is not None and v != ""]
    nums = [_float(v) for v in present]
    numeric = [n for n in nums if n is not None]
    if present and len(numeric) == len(nums):
        lo, hi = min(numeric), max(numeric)
        if lo == hi:
            color = SEQUENTIAL_COLORS[-1]
            return (
                lambda v: color if _float(v) is not None else NULL_COLOR,
                [(f"{lo:.2f}", color)],
            )
        n = len(SEQUENTIAL_COLORS)
        width = (hi - lo) / n

        def fn(v: Any) -> str:
            f = _float(v)
            if f is None:
                return NULL_COLOR
            # Clamp both ends: a value below lo would otherwise index from the
            # end of the palette and silently paint an outlier the top color.
            return SEQUENTIAL_COLORS[max(0, min(n - 1, int((f - lo) / width)))]

        legend = [
            (f"{lo + i * width:.1f} – {lo + (i + 1) * width:.1f}", c)
            for i, c in enumerate(SEQUENTIAL_COLORS)
        ]
        return fn, legend

    cats = sorted({str(v) for v in present})
    cmap = {
        c: CATEGORICAL_PALETTE[i % len(CATEGORICAL_PALETTE)] for i, c in enumerate(cats)
    }
    return (
        lambda v: cmap.get(str(v), NULL_COLOR) if v is not None else NULL_COLOR,
        [(c, cmap[c]) for c in cats],
    )


def _size_mapping(values: List[Any]) -> Callable[[Any], float]:
    valid = [n for n in (_float(v) for v in values) if n is not None]
    if not valid:
        return lambda v: 5.0
    lo, hi = min(valid), max(valid)
    if hi == lo:
        return lambda v: 7.0

    def fn(v: Any) -> float:
        f = _float(v)
        return 4.0 if f is None else 4.0 + 11.0 * (f - lo) / (hi - lo)

    return fn


def _legend_html(title: str, entries: List[Tuple[str, str]]) -> str:
    rows = "".join(
        f'<div style="display:flex;align-items:center;margin:2px 0">'
        f'<span style="width:12px;height:12px;background:{c};display:inline-block;'
        f'margin-right:6px;border:1px solid #999"></span>'
        f"{_html.escape(str(label))}</div>"
        for label, c in entries
    )
    return (
        '<div style="position:fixed;bottom:18px;left:12px;z-index:9999;'
        "background:rgba(255,255,255,0.92);padding:8px 10px;border-radius:6px;"
        "box-shadow:0 1px 6px rgba(0,0,0,.2);font:12px system-ui,sans-serif;"
        'max-height:42%;overflow:auto">'
        f'<div style="font-weight:600;margin-bottom:4px">{_html.escape(str(title))}</div>'
        f"{rows}</div>"
    )


def points_map(
    rows: List[Dict[str, Any]],
    lat_col: str,
    lon_col: str,
    popup_cols: Optional[List[str]] = None,
    fences: Optional[List[Dict[str, str]]] = None,
    color_by: Optional[str] = None,
    size_by: Optional[str] = None,
    style: str = "markers",
) -> str:
    """Render points. `color_by` colors markers by a column (numeric → graduated
    scale, else categorical), `size_by` scales radius by a numeric column, and
    `style` is 'markers' (default), 'heatmap', or 'clustered'."""
    pts = []
    for r in rows:
        lat, lon = _float(r.get(lat_col)), _float(r.get(lon_col))
        if lat is None or lon is None:
            continue
        pts.append((lat, lon, r))
    m = _base_map([p[0] for p in pts], [p[1] for p in pts])
    _add_fences(m, fences)

    color_fn, legend = (None, None)
    if color_by:
        color_fn, legend = _color_mapping([p[2].get(color_by) for p in pts])
    size_fn = _size_mapping([p[2].get(size_by) for p in pts]) if size_by else None

    style = (style or "markers").lower()
    if style == "heatmap":
        from folium.plugins import HeatMap

        if size_by:
            nums = [n for n in (_float(p[2].get(size_by)) for p in pts) if n is not None]
            lo, hi = (min(nums), max(nums)) if nums else (0.0, 1.0)
            data = []
            for lat, lon, r in pts:
                f = _float(r.get(size_by))
                w = 1.0 if (f is None or hi == lo) else max((f - lo) / (hi - lo), 0.05)
                data.append([lat, lon, w])
        else:
            data = [[lat, lon] for lat, lon, _ in pts]
        HeatMap(data, radius=18).add_to(m)
    else:
        container: Any = m
        if style == "clustered":
            from folium.plugins import MarkerCluster

            container = MarkerCluster().add_to(m)
        for lat, lon, r in pts:
            folium.CircleMarker(
                location=[lat, lon],
                radius=size_fn(r.get(size_by)) if (size_fn and size_by) else 5,
                color=(
                    color_fn(r.get(color_by))
                    if (color_fn and color_by)
                    else DEFAULT_COLOR
                ),
                fill=True,
                fill_opacity=0.8,
                popup=_popup(r, popup_cols),
                tooltip=f"{lat:.5f}, {lon:.5f}",
            ).add_to(container)

    if legend and color_by:
        root = cast(Any, m.get_root())
        root.html.add_child(folium.Element(_legend_html(color_by, legend)))
    return m.get_root().render()


def routes_map(
    rows: List[Dict[str, Any]],
    from_lat: str,
    from_lon: str,
    to_lat: str,
    to_lon: str,
    popup_cols: Optional[List[str]] = None,
    fences: Optional[List[Dict[str, str]]] = None,
) -> str:
    segs = []
    for r in rows:
        a, b = _float(r.get(from_lat)), _float(r.get(from_lon))
        c, d = _float(r.get(to_lat)), _float(r.get(to_lon))
        if a is None or b is None or c is None or d is None:
            continue
        segs.append((a, b, c, d, r))
    lats = [s[0] for s in segs] + [s[2] for s in segs]
    lons = [s[1] for s in segs] + [s[3] for s in segs]
    m = _base_map(lats, lons)
    _add_fences(m, fences)
    for a, b, c, d, r in segs:
        folium.PolyLine(
            locations=[(a, b), (c, d)],
            color="#0a58ca",
            weight=2,
            opacity=0.8,
            popup=_popup(r, popup_cols),
        ).add_to(m)
        folium.CircleMarker(location=[a, b], radius=3, color="#5cb85c", fill=True).add_to(
            m
        )
        folium.CircleMarker(location=[c, d], radius=3, color="#d9534f", fill=True).add_to(
            m
        )
    return m.get_root().render()


TRACK_COLORS = [
    "#0a58ca",
    "#d9534f",
    "#5cb85c",
    "#f0ad4e",
    "#6f42c1",
    "#20c997",
    "#e83e8c",
    "#fd7e14",
]


def tracks_map(
    rows: List[Dict[str, Any]],
    group_col: str,
    lat_col: str,
    lon_col: str,
    group_distances: Optional[Dict[Any, float]] = None,
    popup_cols: Optional[List[str]] = None,
    fences: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Draw one connected path per group (rows must already be ordered within
    each group). Each track gets its own color, a start/end marker, and a
    tooltip with the group id and total distance."""
    group_distances = group_distances or {}
    groups: Dict[Any, List[tuple]] = {}
    for r in rows:
        lat, lon = _float(r.get(lat_col)), _float(r.get(lon_col))
        if lat is None or lon is None:
            continue
        groups.setdefault(r.get(group_col), []).append((lat, lon, r))

    all_lat = [p[0] for pts in groups.values() for p in pts]
    all_lon = [p[1] for pts in groups.values() for p in pts]
    m = _base_map(all_lat, all_lon)
    _add_fences(m, fences)

    for i, (g, pts) in enumerate(groups.items()):
        color = TRACK_COLORS[i % len(TRACK_COLORS)]
        coords = [(p[0], p[1]) for p in pts]
        km = group_distances.get(g)
        label = f"{group_col}={g}"
        if km is not None:
            label += f" · {km:.1f} km"
        if len(coords) >= 2:
            folium.PolyLine(
                locations=coords,
                color=color,
                weight=3,
                opacity=0.85,
                tooltip=label,
                popup=label,
            ).add_to(m)
        folium.CircleMarker(
            location=coords[0],
            radius=4,
            color="#198754",
            fill=True,
            fill_opacity=1.0,
            tooltip=f"start: {g}",
        ).add_to(m)
        folium.CircleMarker(
            location=coords[-1],
            radius=4,
            color="#dc3545",
            fill=True,
            fill_opacity=1.0,
            tooltip=f"end: {g}",
            popup=_popup(pts[-1][2], popup_cols),
        ).add_to(m)
    return m.get_root().render()


# -- preview persistence (ported from the v0.1 server) ------------------

_TOOLBAR_TPL = Template(
    """
<style>
#dm-toolbar { position: fixed; top: 12px; right: 12px; z-index: 10000; background: rgba(255,255,255,0.95); border-radius: 6px; padding: 6px 10px; box-shadow: 0 1px 6px rgba(0,0,0,0.2); font-family: system-ui, sans-serif; }
#dm-toolbar a { margin-right: 8px; text-decoration: none; color: #222; font-size: 13px; }
</style>
<div id="dm-toolbar">
  <a href="$home" title="Home">Home</a>
  <span style="opacity:0.7">$title</span>
</div>
"""
)


def _preview_config() -> Tuple[Optional[Path], Optional[str]]:
    d = os.environ.get("DUCKMOVE_PREVIEW_DIR")
    u = os.environ.get("DUCKMOVE_PREVIEW_URL")
    return (Path(d) if d else None, u)


def _inject_toolbar(html: str, homepage_url: str, title: str = "Map") -> str:
    toolbar = _TOOLBAR_TPL.substitute(home=homepage_url, title=title)
    idx = html.lower().rfind("</body>")
    if idx == -1:
        return html + toolbar
    return html[:idx] + toolbar + html[idx:]


def persist_map(html: str, kind: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Save HTML to the preview dir and update manifest/index.

    Returns {} when no preview is configured (caller falls back to a data URL).
    """
    preview_dir, preview_url = _preview_config()
    if not (preview_dir and preview_url):
        return {}
    preview_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = preview_dir / "manifest.json"
    try:
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else []
        )
        if not isinstance(manifest, list):
            manifest = []
    except Exception:
        manifest = []

    created_at = datetime.now(timezone.utc).isoformat()
    fname = (
        f"{kind}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.html"
    )
    home_url = preview_url.rstrip("/") + "/"
    title = meta.get("title") or f"{kind.title()} map"
    (preview_dir / fname).write_text(
        _inject_toolbar(html, homepage_url=home_url, title=title), encoding="utf-8"
    )

    entry = {
        "file": fname,
        "url": preview_url.rstrip("/") + "/" + fname,
        "created_at": created_at,
        "kind": kind,
        "meta": meta,
    }
    manifest.append(entry)
    manifest.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (preview_dir / "index.html").write_text(
            render_index_html(manifest, created_at), encoding="utf-8"
        )
    except Exception:
        pass

    return {
        "file": fname,
        "file_path": str(preview_dir / fname),
        "url": entry["url"],
        "homepage_url": home_url,
        "created_at": created_at,
    }
