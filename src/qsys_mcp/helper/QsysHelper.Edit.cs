// edit: changes a saved design in place, operation by operation, without rebuilding it. Everything the
// operations don't touch stays as it was, including snapshot contents, UCIs and positions.
//
//   {"command": "edit", "path": ..., "out_path"?: ..., "ops": [{"op": "move", "component": "MIX", "x": 300, "y": 40}, ...]}
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

static partial class QsysHelper
{
    static JObject Edit(JObject req)
    {
        var path = Path.GetFullPath((string)req["path"]);
        var outPath = Path.GetFullPath((string)req["out_path"] ?? path);
        var doc = Q.Load(path);
        AttachAll(doc);   // pins only list their wires once the page's elements are attached
        var manager = EnsureManager();
        Q.Call(manager, "StartOperation");
        var ctx = ContextOf(doc);
        var cache = req["positions"] as JArray ?? new JArray();
        var done = new JArray();
        int index = 0;
        foreach (JObject op in (req["ops"] as JArray ?? new JArray()))
        {
            var kind = (string)op["op"];
            try { done.Add(new JObject { { "op", kind }, { "result", ApplyOp(ctx, op, cache) } }); }
            catch (Exception ex)
            {
                while (ex.InnerException != null) ex = ex.InnerException;
                throw new Exception("ops[" + index + "] (" + kind + "): " + ex.Message);
            }
            index++;
        }
        EndOperation(manager);
        AttachAll(doc);
        Save(doc, outPath);
        return new JObject { { "path", outPath }, { "applied", done }, { "warnings", ctx.Warnings } };
    }

    // controls: {path, components?: [names]} - every control of the blocks (id, name, type, range, pins)
    static JObject Controls(JObject req)
    {
        var doc = Q.Load(Path.GetFullPath((string)req["path"]));
        var want = req["components"] is JArray ? ((JArray)req["components"]).Select(x => (string)x).ToList() : null;
        var res = new JObject();
        foreach (var c in AllComponents(doc))
        {
            var name = NameOf(c);
            if (want != null && !want.Contains(name)) continue;
            var arr = ControlInfo(c);
            foreach (JObject o in arr)
            {
                var d = Descriptor(c, (string)o["id"]);
                var ps = Q.TryF(d, "PinStyle");
                if (ps != null) o["pin_style"] = ps.ToString();
            }
            res[name] = new JObject { { "class", (string)Q.Get(c, "ClassName") }, { "script_access", Q.Get(c, "ScriptAccess").ToString() },
                { "controls", arr } };
        }
        return new JObject { { "components", res } };
    }

    // types: {pattern} / members: {type} - for maintaining the helper when Designer moves its classes
    static JObject Types(JObject req)
    {
        Type[] types;
        try { types = Q.Asm.GetTypes(); } catch (System.Reflection.ReflectionTypeLoadException e) { types = e.Types.Where(x => x != null).ToArray(); }
        var re = new System.Text.RegularExpressions.Regex((string)req["pattern"], System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        return new JObject { { "types", new JArray(types.Where(t => t.FullName != null && re.IsMatch(t.FullName)).Select(t => t.FullName).OrderBy(n => n).Take(400)) } };
    }

    static JObject Members(JObject req)
    {
        var t = Q.T((string)req["type"]);
        var res = new JArray();
        foreach (var m in t.GetMembers(Q.All | System.Reflection.BindingFlags.DeclaredOnly))
        {
            var mb = m as System.Reflection.MethodBase;
            res.Add(m.MemberType + " " + m.Name + (mb != null ? "(" + string.Join(", ", mb.GetParameters().Select(p => p.ParameterType.FullName + " " + p.Name)) + ")" + (mb.IsStatic ? " static" : "") : ""));
        }
        return new JObject { { "type", t.FullName }, { "base", t.BaseType == null ? null : t.BaseType.FullName }, { "members", res } };
    }

    // {"op": "set_uci_element", "uci", "page"?, "match": {"text": "..."} | {"component", "control"}, "set": {x, y, w, h, text}}
    // One element of a UCI moved, resized or relabelled; the match must find exactly one.
    static JObject SetUciElement(Ctx ctx, JObject op)
    {
        var panel = Q.L(Q.Get(ctx.Doc, "WebPanels")).FirstOrDefault(pn => Convert.ToString(Q.Get(pn, "Title")) == (string)op["uci"]);
        if (panel == null) throw new Exception("no UCI named " + op["uci"]);
        var match = (JObject)op["match"] ?? new JObject();
        var byId = ctx.Comps.Values.ToDictionary(c => Convert.ToString(Q.Get(c, "Id")), c => c);
        var hits = new List<object>();
        foreach (var pg in Q.L(Q.Get(panel, "Pages")))
        {
            if (op["page"] != null && Convert.ToString(Q.Get(pg, "Title")) != (string)op["page"]) continue;
            foreach (var el in Q.L(Q.Get(pg, "AllChildren")))
            {
                if (match["text"] != null && Convert.ToString(Q.TryGet(el, "Label")) == (string)match["text"]) hits.Add(el);
                else if (match["component"] != null && el.GetType().Name == "ControlStyle")
                {
                    object comp;
                    if (byId.TryGetValue(Convert.ToString(Q.Get(el, "Id")), out comp) && NameOf(comp) == (string)match["component"]
                        && Convert.ToString(Q.Get(el, "Name")) == (string)match["control"]) hits.Add(el);
                }
            }
        }
        if (hits.Count != 1)
            throw new Exception("set_uci_element: " + hits.Count + " elements match " + match.ToString(Newtonsoft.Json.Formatting.None) + " (give the page, or a closer match)");
        var e = hits[0];
        var set = (JObject)op["set"] ?? new JObject();
        var pos = (System.Windows.Point)Q.Get(e, "Position");
        var size = (System.Windows.Size)Q.Get(e, "Size");
        Q.Set(e, "Position", new System.Windows.Point((double?)set["x"] ?? pos.X, (double?)set["y"] ?? pos.Y));
        Q.Set(e, "Size", new System.Windows.Size((double?)set["w"] ?? size.Width, (double?)set["h"] ?? size.Height));
        if (set["text"] != null) Q.Set(e, "Label", (string)set["text"]);
        if (set["font_size"] != null) Q.Set(e, "FontSize", (double)set["font_size"]);
        return new JObject { { "element", e.GetType().Name }, { "was", new JObject { { "x", pos.X }, { "y", pos.Y }, { "w", size.Width }, { "h", size.Height } } } };
    }

    static object EnsureManager()
    {
        var mgrField = Q.T("QSC.QSys.App").GetField("Manager", Q.All);
        var manager = mgrField.GetValue(null);
        if (manager == null) { manager = Activator.CreateInstance(Q.T("QSC.QSys.Design.Manager"), true); mgrField.SetValue(null, manager); }
        return manager;
    }

    // The same context Build works with, read from a saved design.
    static Ctx ContextOf(object doc)
    {
        var ctx = new Ctx { Doc = doc, Core = Q.Get(doc, "Core") };
        ctx.Inventory[Convert.ToString(Q.Get(ctx.Core, "Name"))] = ctx.Core;
        foreach (var item in Q.L(Q.Get(doc, "Inventory"))) ctx.Inventory[Convert.ToString(Q.TryGet(item, "Name"))] = item;
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
        {
            var title = Convert.ToString(Q.Get(page, "Title"));
            ctx.Pages[title] = page;
            ctx.PageNames.Add(title);
            foreach (var c in Q.L(Q.Get(page, "AllChildren")).Where(IsComponent))
            {
                ctx.Comps[NameOf(c)] = c;
                ctx.CompPage[c] = title;
                foreach (var p in Q.Pins(c, "Output"))
                {
                    var n = Convert.ToString(Q.Get(p, "Label"));
                    if (!string.IsNullOrEmpty(n) && !ctx.SignalNames.ContainsKey(n)) ctx.SignalNames[n] = p;
                }
            }
        }
        return ctx;
    }

    static object Comp(Ctx ctx, JObject op)
    {
        var name = (string)op["component"];
        object c;
        if (name == null || !ctx.Comps.TryGetValue(name, out c)) throw new Exception("no component " + name);
        return c;
    }

    static JToken ApplyOp(Ctx ctx, JObject op, JArray cache)
    {
        switch ((string)op["op"])
        {
            case "set_value":
                return StoreValue(Comp(ctx, op), (string)op["control"], op["position"] != null ? op : op["value"], cache);
            case "set_values":
            {
                var c = Comp(ctx, op);
                return new JArray(((JObject)op["values"]).Properties().Select(kv => StoreValue(c, kv.Name, kv.Value, cache)));
            }
            case "set_property":
            {
                // a property can change the block's pins: wires to pins that disappear are reported
                var c = Comp(ctx, op);
                var before = PinWires(ctx, c);
                Q.SetProp(c, (string)op["property"], Scalar(op["value"]));
                Q.Refresh(c);
                var lost = before.Where(w => !Q.L(Q.Get(c, "Pins")).Contains(Q.Get(w, "PinA")) && !Q.L(Q.Get(c, "Pins")).Contains(Q.Get(w, "PinB"))).ToList();
                foreach (var w in lost) RemoveElement(ctx, w);
                return new JObject { { "pins", PinInfo(c) }, { "wires_removed", lost.Count } };
            }
            case "move":
            {
                var el = op["component"] != null ? Comp(ctx, op) : Graphic(ctx, op);
                var pos = (System.Windows.Point)Q.Get(el, "Position");
                var x = (double?)op["x"] ?? pos.X + ((double?)op["dx"] ?? 0);
                var y = (double?)op["y"] ?? pos.Y + ((double?)op["dy"] ?? 0);
                Q.Set(el, "Position", new System.Windows.Point(x, y));
                return new JObject { { "x", x }, { "y", y } };
            }
            case "set_label":
                Q.Set(Comp(ctx, op), "UserLabel", (string)op["label"]);
                return "ok";
            case "set_access":
                SetAccess(Comp(ctx, op), (string)op["script_access"]);
                return "ok";
            case "rename":
            {
                var c = Comp(ctx, op);
                var name = (string)op["name"];
                if (ctx.Comps.ContainsKey(name)) throw new Exception("a component is already named " + name);
                ctx.Comps.Remove((string)op["component"]);
                Q.Set(c, "CodeName", name);
                ctx.Comps[name] = c;
                return "ok (Lua, UCIs over QRC and control systems that use the old code name must change too)";
            }
            case "set_lua":
                StoreString(Comp(ctx, op), "code", (string)op["lua"]);
                return "ok";
            case "add_control_pins":
                AddControlPins(Comp(ctx, op), op["controls"] as JArray);
                return PinInfo(Comp(ctx, op));
            case "rename_inventory":
            {
                // {"op": "rename_inventory", "item": "old name" (the Core's too), "name": "new name"}
                object item;
                if (!ctx.Inventory.TryGetValue((string)op["item"] ?? "", out item))
                    throw new Exception("no inventory item '" + op["item"] + "'; items: " + string.Join(", ", ctx.Inventory.Keys));
                Q.Set(item, "Name", (string)op["name"]);
                var actual = Convert.ToString(Q.Get(item, "Name"));
                return actual == (string)op["name"] ? "ok" : "renamed '" + actual + "' (Designer's hostname rules)";
            }
            case "add_inventory":
                return Convert.ToString(Q.Get(AddInventoryItem(ctx, (JObject)op["item"]), "Name"));
            case "set_usb_bridge":
            {
                var owner = op["item"] == null ? ctx.Core : (ctx.Inventory.ContainsKey((string)op["item"]) ? ctx.Inventory[(string)op["item"]] : null);
                if (owner == null) throw new Exception("no inventory item " + op["item"]);
                ConfigureUsbBridge(Q.Get(owner, "UsbBridge"), (JObject)op["settings"]);
                return "ok";
            }
            case "set_design_properties":
                SetMembers(Q.Get(ctx.Doc, "Properties"), (JObject)op["settings"]);
                return "ok";
            case "add_component":
            {
                var cs = (JObject)op["component"];
                var c = CreateComponent(ctx, cs);
                return new JObject { { "pins", PinInfo(c) }, { "values", StoreComponentValues(c, cs, cache) } };
            }
            case "remove_component":
            {
                var c = Comp(ctx, op);
                var wires = PinWires(ctx, c);
                foreach (var w in wires) RemoveElement(ctx, w);
                int fromBanks = 0;
                foreach (var bank in Q.L(Q.Get(ctx.Doc, "Snapshots")))
                {
                    var members = (IList)Q.Get(bank, "Components");
                    foreach (var m in members.Cast<object>().Where(m => Q.Get(m, "DesignComponent") == c).ToList()) { members.Remove(m); fromBanks++; }
                }
                RemoveElement(ctx, c);
                ctx.Comps.Remove((string)op["component"]);
                return new JObject { { "wires_removed", wires.Count }, { "snapshot_entries_removed", fromBanks } };
            }
            case "add_wire":
                AddWire(ctx, Ends(op, "add_wire"));
                return "ok";
            case "remove_wire":
            {
                var a = ResolvePin(ctx.Comps, (string)op["from"], "Output");
                var b = ResolvePin(ctx.Comps, (string)op["to"], "Input");
                var w = WiresOf(ctx, b).FirstOrDefault(x => Q.Get(x, "PinA") == a || Q.Get(x, "PinB") == a);
                if (w == null) throw new Exception("no wire " + op["from"] + " -> " + op["to"]);
                RemoveElement(ctx, w);
                return "ok";
            }
            case "add_signal":
                AddSignalLink(ctx, Ends(op, "add_signal"));
                return "ok";
            case "rename_signal":
            {
                // every pin carrying the old name takes the new one (the source and the inputs it feeds)
                var from = (string)op["signal"]; var to = (string)op["name"];
                if (ctx.SignalNames.ContainsKey(to)) throw new Exception("signal name '" + to + "' is already used");
                int n = 0;
                foreach (var c in ctx.Comps.Values)
                    foreach (var p in Q.L(Q.Get(c, "Pins")).Where(p => Convert.ToString(Q.Get(p, "Label")) == from)) { Q.Set(p, "Label", to); n++; }
                if (n == 0) throw new Exception("no pin carries the signal name '" + from + "'");
                ctx.SignalNames[to] = ctx.SignalNames[from];
                ctx.SignalNames.Remove(from);
                return new JObject { { "pins", n } };
            }
            case "remove_signal":
            {
                // {"pin": "NAME.in.N"} clears one pin's name; {"signal": "..."} clears it everywhere
                int n = 0;
                if (op["pin"] != null)
                {
                    var end = (string)op["pin"];
                    var p = ResolvePin(ctx.Comps, end, end.Contains(".in.") ? "Input" : "Output");
                    Q.Set(p, "Label", ""); n = 1;
                }
                else
                    foreach (var c in ctx.Comps.Values)
                        foreach (var p in Q.L(Q.Get(c, "Pins")).Where(p => Convert.ToString(Q.Get(p, "Label")) == (string)op["signal"])) { Q.Set(p, "Label", ""); n++; }
                return new JObject { { "pins", n } };
            }
            case "add_graphic":
                AddGraphic(ctx, (JObject)op["graphic"]);
                return "ok";
            case "set_graphic":
            {
                // {"text": "old text", "set": {"text": "...", "x": ..., "fill": ...}}: same keys as spec.graphics
                var g = Graphic(ctx, op);
                var set = (JObject)op["set"];
                if (set["text"] != null) Q.Set(g, "Label", (string)set["text"]);
                var pos = (System.Windows.Point)Q.Get(g, "Position"); var size = (System.Windows.Size)Q.Get(g, "Size");
                Q.Set(g, "Position", new System.Windows.Point((double?)set["x"] ?? pos.X, (double?)set["y"] ?? pos.Y));
                Q.Set(g, "Size", new System.Windows.Size((double?)set["w"] ?? size.Width, (double?)set["h"] ?? size.Height));
                if (set["fill"] != null) Q.Set(g, "Fill", Q.C((string)set["fill"]));
                if (set["text_color"] != null) Q.Set(g, "TextColor", Q.C((string)set["text_color"]));
                if (set["font_size"] != null) Q.Set(g, "FontSize", (double)set["font_size"]);
                return "ok";
            }
            case "remove_graphic":
                RemoveElement(ctx, Graphic(ctx, op));
                return "ok";
            case "add_named_control":
                AddNamedControl(ctx, op);
                return "ok";
            case "remove_named_control":
            {
                var list = (IList)Q.Get(ctx.Doc, "ExternalControlIds");
                var hit = list.Cast<object>().Where(x => Convert.ToString(Q.Get(x, "Id")) == (string)op["name"]).ToList();
                if (hit.Count == 0) throw new Exception("no named control " + op["name"]);
                foreach (var x in hit)
                {
                    try { Q.Call(x, "Delete"); } catch { }
                    while (list.Contains(x)) list.Remove(x);
                }
                return "ok";
            }
            case "set_global_snapshot_count":
                SetGlobalSnapshots(ctx.Doc, (int)op["count"]);
                return "ok";
            case "add_uci":
            {
                var sizes = new JArray();
                var n = BuildUci(ctx.Doc, (JObject)op["uci"], ctx.Comps, sizes);
                return new JObject { { "elements", n }, { "size", sizes.Count > 0 ? sizes[0] : null } };
            }
            case "remove_uci":
            {
                var panels = (IList)Q.Get(ctx.Doc, "WebPanels");
                var hit = panels.Cast<object>().Where(pn => Convert.ToString(Q.Get(pn, "Title")) == (string)op["name"]).ToList();
                if (hit.Count == 0) throw new Exception("no UCI named " + op["name"]);
                foreach (var pn in hit) panels.Remove(pn);
                return new JObject { { "removed", hit.Count } };
            }
            case "add_snapshot_bank":
                return BuildBank(ctx.Doc, (JObject)op["bank"], ctx.Comps, ctx.CompPage, ctx.Pages, ctx.PageNames[0]);
            case "set_uci_element":
                return SetUciElement(ctx, op);
            default:
                throw new Exception("unknown op; ops are set_value, set_values, set_property, move, set_label, set_access, rename, set_lua, " +
                    "add_control_pins, add_inventory, set_usb_bridge, set_design_properties, add_component, remove_component, add_wire, " +
                    "remove_wire, add_signal, rename_signal, remove_signal, add_graphic, set_graphic, remove_graphic, add_snapshot_bank, " +
                    "add_uci, remove_uci, set_uci_element, add_named_control, remove_named_control, set_global_snapshot_count, " +
                    "rename_inventory");
        }
    }

    // Wires are found on the pages: a loaded design's pins don't list them. A wire may be stored either way round.
    static List<object> WiresOf(Ctx ctx, object pin)
    {
        return ctx.Pages.Values.SelectMany(pg => Q.L(Q.Get(pg, "AllChildren"))).Where(w => w.GetType().Name == "Wire" &&
            (Q.Get(w, "PinA") == pin || Q.Get(w, "PinB") == pin)).ToList();
    }

    static List<object> PinWires(Ctx ctx, object c)
    {
        return Q.L(Q.Get(c, "Pins")).SelectMany(p => WiresOf(ctx, p)).Distinct().ToList();
    }

    // A note or group box, found by its exact text (and page, when given).
    static object Graphic(Ctx ctx, JObject op)
    {
        var text = (string)op["text"];
        var hits = ctx.Pages.Where(kv => op["page"] == null || kv.Key == (string)op["page"])
            .SelectMany(kv => Q.L(Q.Get(kv.Value, "AllChildren")))
            .Where(g => (g.GetType().Name == "TextBlock" || g.GetType().Name == "GroupBox") && Convert.ToString(Q.Get(g, "Label")) == text).ToList();
        if (hits.Count != 1) throw new Exception(hits.Count + " notes or group boxes have the text '" + text + "' (need exactly one; give page)");
        return hits[0];
    }

    static void RemoveElement(Ctx ctx, object el)
    {
        foreach (var page in ctx.Pages.Values)
            foreach (var layer in Q.L(Q.Get(page, "Layers")))
                if (Q.L(Q.Get(layer, "Children")).Contains(el)) { Q.Call(layer, "RemoveChild", el); return; }
        throw new Exception("element not found on any page");
    }
}
