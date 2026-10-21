"""Design documentation and comparison.

`document` turns a saved design (read_design output) into a self-contained HTML page: inventory,
blocks with their settings, wires and signal names, snapshot banks (optionally the value of every
control in every snapshot, read in emulation), UCIs and notes. `diff` compares two designs.
"""

from __future__ import annotations

import datetime
import html

T = {
    "en": {"title": "Q-SYS design", "core": "Core", "inventory": "Inventory", "blocks": "Blocks", "page": "Page",
           "name": "Code name", "class": "Class", "label": "Label", "access": "Script Access", "settings": "Properties",
           "values": "Stored values", "wires": "Wires", "from": "From", "to": "To", "signal": "Signal name",
           "links": "Connections by signal name", "banks": "Snapshot banks", "bank": "Bank", "count": "Snapshots",
           "members": "Contents", "saved": "Saved", "controller": "Controller", "ucis": "UCIs", "elements": "elements",
           "notes": "Notes", "contents": "What each snapshot sets", "control": "Control", "made": "Generated on",
           "none": "none", "type": "Type", "item": "Item"},
    "fr": {"title": "Design Q-SYS", "core": "Core", "inventory": "Inventaire", "blocks": "Boîtes", "page": "Page",
           "name": "Code Name", "class": "Classe", "label": "Libellé", "access": "Script Access", "settings": "Propriétés",
           "values": "Valeurs enregistrées", "wires": "Fils", "from": "De", "to": "Vers", "signal": "Étiquette",
           "links": "Liaisons par étiquette", "banks": "Banques de snapshots", "bank": "Banque", "count": "Snapshots",
           "members": "Contenu", "saved": "Enregistrés", "controller": "Contrôleur", "ucis": "UCI", "elements": "éléments",
           "notes": "Notes", "contents": "Ce que règle chaque snapshot", "control": "Contrôle", "made": "Généré le",
           "none": "aucun", "type": "Type", "item": "Appareil"},
}

CSS = """
:root { --bg:#ffffff; --fg:#1d1f23; --muted:#5f6670; --line:#d9dde3; --head:#f3f5f8; --accent:#1565c0; }
@media (prefers-color-scheme: dark) { :root { --bg:#16181c; --fg:#e7e9ec; --muted:#9aa1ab; --line:#30343b; --head:#1f2228; --accent:#64a8ff; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px; }
main { max-width:1200px; margin:0 auto; }
h1 { font-size:24px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 8px; border-bottom:2px solid var(--accent); padding-bottom:4px; }
h3 { font-size:15px; margin:20px 0 6px; } .muted { color:var(--muted); }
table { border-collapse:collapse; width:100%; margin:6px 0 12px; font-size:13px; }
th, td { border:1px solid var(--line); padding:4px 6px; text-align:left; vertical-align:top; }
th { background:var(--head); } code { font:12px ui-monospace, Consolas, monospace; }
.wrap { overflow-x:auto; } .kv code { white-space:nowrap; }
"""

SKIP_PROPS = {"ScriptAccess", "external_prefix", "external_prefix_mode", "internal_designator", "pae_assignment", "plugin_source",
              "config", "code", "is_managed", "init_code"}


def _e(s) -> str:
    return html.escape(str(s))


def _kv(d: dict) -> str:
    return "<br>".join(f"<code>{_e(k)}</code> = {_e(v)}" for k, v in d.items()) or "–"


def _value(v: dict) -> str:
    if "string" in v:
        s = v["string"]
        return (s[:60] + "…") if len(s) > 60 else s
    val = v.get("value")
    return f"{val:g}" if isinstance(val, (int, float)) else str(val)


def document(design: dict, title: str | None = None, lang: str = "fr", snapshot_values: dict | None = None) -> str:
    t = T.get(lang, T["en"])
    name = title or design["path"].replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    out = [f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{_e(name)}</title><style>{CSS}</style></head><body><main>",
           f"<h1>{_e(name)}</h1><p class='muted'>{t['title']} · Designer {_e(design.get('saved_by') or design.get('designer_version', ''))} · "
           f"{t['made']} {datetime.date.today().isoformat()}</p>"]
    core = design.get("core", {})
    out.append(f"<h2>{t['inventory']}</h2><table><tr><th>{t['type']}</th><th>{t['item']}</th></tr>"
               f"<tr><td>{t['core']} {_e(core.get('model', ''))}</td><td>{_e(core.get('name', ''))}</td></tr>")
    for i in design.get("inventory", []):
        if i["type"] != "Core":
            out.append(f"<tr><td>{_e(i['type'])}</td><td>{_e(i['name'])}</td></tr>")
    out.append("</table>")

    out.append(f"<h2>{t['blocks']}</h2>")
    for page in design.get("pages", []):
        comps = sorted((c for c in design["components"] if c.get("page") == page), key=lambda c: (c.get("y", 0), c.get("x", 0)))
        out.append(f"<h3>{t['page']} {_e(page)}</h3><div class='wrap'><table><tr><th>{t['name']}</th><th>{t['class']}</th>"
                   f"<th>{t['label']}</th><th>{t['access']}</th><th>{t['settings']}</th><th>{t['values']}</th></tr>")
        for c in comps:
            props = {k: v for k, v in c.get("properties", {}).items() if k not in SKIP_PROPS and len(str(v)) < 80}
            vals = {k: _value(v) for k, v in c.get("values", {}).items() if k != "code"}
            cls = c["class"] if not c["class"].startswith("%PLUGIN%") else "plugin"
            out.append(f"<tr><td><code>{_e(c['name'])}</code></td><td>{_e(cls)}</td><td>{_e(c.get('user_label') or c.get('label', ''))}</td>"
                       f"<td>{_e(c.get('script_access', ''))}</td><td class='kv'>{_kv(props)}</td><td class='kv'>{_kv(vals)}</td></tr>")
        out.append("</table></div>")

    links = {(a, b) for a, b, _ in design.get("signal_links", [])}
    signal = {}
    for c in design["components"]:
        for p in c.get("pins", []):
            if p.get("signal") and p["dir"] == "out":
                signal[f"{c['name']}.out.{p['index']}"] = p["signal"]
    wires = [w for w in design.get("wires", []) if tuple(w) not in links]
    out.append(f"<h2>{t['wires']} ({len(wires)})</h2><table><tr><th>{t['from']}</th><th>{t['to']}</th><th>{t['signal']}</th></tr>")
    for a, b in sorted(wires):
        out.append(f"<tr><td><code>{_e(a)}</code></td><td><code>{_e(b)}</code></td><td>{_e(signal.get(a, ''))}</td></tr>")
    out.append("</table>")
    if design.get("signal_links"):
        out.append(f"<h3>{t['links']} ({len(design['signal_links'])})</h3><table><tr><th>{t['from']}</th><th>{t['to']}</th><th>{t['signal']}</th></tr>")
        for a, b, n in sorted(design["signal_links"]):
            out.append(f"<tr><td><code>{_e(a)}</code></td><td><code>{_e(b)}</code></td><td>{_e(n)}</td></tr>")
        out.append("</table>")

    banks = design.get("snapshot_banks", [])
    out.append(f"<h2>{t['banks']}</h2>")
    if not banks:
        out.append(f"<p class='muted'>{t['none']}</p>")
    for b in banks:
        members = "<br>".join(f"<code>{_e(m['component'])}</code>: " + (", ".join(m["controls"]) if not m["all"] else "*")
                              for m in b["controls"])
        out.append(f"<h3>{t['bank']} {_e(b['name'])}</h3><table><tr><th>{t['controller']}</th><th>{t['count']}</th><th>{t['saved']}</th>"
                   f"<th>{t['members']}</th></tr><tr><td><code>{_e(b.get('controller') or '–')}</code></td><td>{b['count']}</td>"
                   f"<td>{', '.join(map(str, b['saved_snapshots'])) or t['none']}</td><td>{members}</td></tr></table>")
        values = (snapshot_values or {}).get(b["name"])
        if values:
            nums = sorted(values, key=int)
            keys = sorted({k for n in nums for k in values[n]})
            out.append(f"<p><b>{t['contents']}</b></p><div class='wrap'><table><tr><th>{t['control']}</th>"
                       + "".join(f"<th>{_e(n)}</th>" for n in nums) + "</tr>")
            for k in keys:
                out.append(f"<tr><td><code>{_e(k)}</code></td>" + "".join(f"<td>{_e(values[n].get(k, ''))}</td>" for n in nums) + "</tr>")
            out.append("</table></div>")

    out.append(f"<h2>{t['ucis']}</h2><ul>")
    for u in design.get("ucis", []):
        size = f" · {u['width']} × {u['height']}" if u.get("width") else ""
        out.append(f"<li><b>{_e(u['name'])}</b>{size} · " + ", ".join(
            f"{_e(p['name'])} ({len(p.get('elements', []))} {t['elements']})" for p in u.get("pages", [])) + "</li>")
    out.append("</ul>")
    notes = [g for g in design.get("graphics", []) if g.get("text")]
    if notes:
        out.append(f"<h2>{t['notes']}</h2><ul>")
        for g in notes:
            out.append(f"<li>{_e(g['text']).replace(chr(10), '<br>')}</li>")
        out.append("</ul>")
    out.append("</main></body></html>")
    return "\n".join(out)


def _index(design: dict) -> dict:
    return {c["name"]: c for c in design["components"]}


def diff(a: dict, b: dict, positions: bool = False) -> dict:
    """What changed from design a to design b: blocks added, removed or changed (class, label,
    Script Access, properties, stored values, signal names, optionally position), wires, signal
    links, snapshot banks and UCIs."""
    ia, ib = _index(a), _index(b)
    changed = []
    for n in sorted(set(ia) & set(ib)):
        ca, cb, d = ia[n], ib[n], {}
        for k in ("class", "user_label", "script_access", "page"):
            if ca.get(k) != cb.get(k):
                d[k] = [ca.get(k), cb.get(k)]
        if positions and (ca.get("x"), ca.get("y")) != (cb.get("x"), cb.get("y")):
            d["position"] = [[ca.get("x"), ca.get("y")], [cb.get("x"), cb.get("y")]]
        for section in ("properties", "values"):
            sa, sb = ca.get(section, {}), cb.get(section, {})
            delta = {k: [sa.get(k), sb.get(k)] for k in sorted(set(sa) | set(sb))
                     if k not in ("plugin_source",) and _plain(sa.get(k)) != _plain(sb.get(k))}
            if delta:
                d[section] = delta
        pa = {f"{p['dir']}.{p['index']}": p.get("signal") for p in ca.get("pins", [])}
        pb = {f"{p['dir']}.{p['index']}": p.get("signal") for p in cb.get("pins", [])}
        if pa != pb:
            d["pins"] = {k: [pa.get(k), pb.get(k)] for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)}
        if d:
            changed.append({"component": n, **d})
    wa, wb = {tuple(w) for w in a.get("wires", [])}, {tuple(w) for w in b.get("wires", [])}
    ba, bb = {x["name"]: x for x in a.get("snapshot_banks", [])}, {x["name"]: x for x in b.get("snapshot_banks", [])}
    banks = {n: {k: [ba[n].get(k), bb[n].get(k)] for k in ("count", "controller", "controls", "saved_snapshots")
                 if ba[n].get(k) != bb[n].get(k)} for n in set(ba) & set(bb)}
    ua = {u["name"]: sum(len(p.get("elements", [])) for p in u.get("pages", [])) for u in a.get("ucis", [])}
    ub = {u["name"]: sum(len(p.get("elements", [])) for p in u.get("pages", [])) for u in b.get("ucis", [])}
    return {
        "components_added": sorted(set(ib) - set(ia)), "components_removed": sorted(set(ia) - set(ib)),
        "components_changed": changed,
        "connections_added": sorted(wb - wa), "connections_removed": sorted(wa - wb),
        "banks_added": sorted(set(bb) - set(ba)), "banks_removed": sorted(set(ba) - set(bb)),
        "banks_changed": {n: d for n, d in banks.items() if d},
        "ucis": {n: [ua.get(n), ub.get(n)] for n in sorted(set(ua) | set(ub)) if ua.get(n) != ub.get(n)},
        "same": not (set(ia) ^ set(ib) or changed or wa ^ wb or set(ba) ^ set(bb) or any(banks.values())),
    }


def _plain(v):
    if isinstance(v, dict):
        return v.get("string", round(v["value"], 6) if isinstance(v.get("value"), float) else v.get("value"))
    return v
