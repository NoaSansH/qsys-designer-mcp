// qsys-designer-mcp helper: builds and reads .qsys files through Q-SYS Designer's own .NET
// libraries, with no Designer window. The Python server compiles this against the user's
// installed Designer (any version) and runs one command per process:
//
//   qhelper.exe <designer-dir> <request.json> <response.json>
//
// Commands (request "command"): info, probe, build, read, set_values, copy.
// Uses Designer internals that are not a public API; see the README's licensing note.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

static class QsysHelper
{
    // Main must not touch Newtonsoft types: they load from the Designer folder, and the resolver
    // for that folder is only installed by Q.Init. The JSON work happens in Run().
    [STAThread]
    static int Main(string[] args)
    {
        if (args.Length < 3) { Console.Error.WriteLine("usage: qhelper <designer-dir> <request.json> <response.json>"); return 2; }
        var requestPath = Path.GetFullPath(args[1]);
        var responsePath = Path.GetFullPath(args[2]);
        try { Q.Init(Path.GetFullPath(args[0])); }
        catch (Exception ex)
        {
            while (ex.InnerException != null) ex = ex.InnerException;
            File.WriteAllText(responsePath, "{\"ok\": false, \"error\": \"could not load Designer from " +
                args[0].Replace("\\", "\\\\") + ": " + ex.Message.Replace("\\", "/").Replace("\"", "'") + "\"}");
            return 0;
        }
        Run(requestPath, responsePath);
        return 0;
    }

    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
    static void Run(string requestPath, string responsePath)
    {
        JObject response;
        try
        {
            var request = JObject.Parse(File.ReadAllText(requestPath));
            response = Dispatch(request);
            response["ok"] = true;
        }
        catch (Exception ex)
        {
            while (ex.InnerException != null) ex = ex.InnerException;
            response = new JObject { { "ok", false }, { "error", ex.GetType().Name + ": " + ex.Message }, { "trace", ex.StackTrace } };
        }
        File.WriteAllText(responsePath, response.ToString());
    }

    static JObject Dispatch(JObject req)
    {
        switch ((string)req["command"])
        {
            case "info": return Info();
            case "probe": return Probe(req);
            case "build": return Build(req);
            case "read": return Read((string)req["path"]);
            case "set_values": return SetValues(req);
            case "copy": return Copy(req);
            default: throw new Exception("unknown command " + req["command"]);
        }
    }

    // ---- shared ---------------------------------------------------------------------
    static readonly string[] AccessModes = { "None", "External", "Script", "All" };

    static object NewDocument(string path, out object manager)
    {
        var fio = Q.T("QSC.QSys.Design.FileIO");
        var doc = Activator.CreateInstance(Q.T("QSC.QSys.Design.Document"), true);
        Q.Call(fio, "InitRoot", doc, path, false);
        var mgrField = Q.T("QSC.QSys.App").GetField("Manager", Q.All);
        manager = mgrField.GetValue(null);
        if (manager == null) { manager = Activator.CreateInstance(Q.T("QSC.QSys.Design.Manager"), true); mgrField.SetValue(null, manager); }
        return doc;
    }

    static void EndOperation(object manager)
    {
        manager.GetType().GetMethods(Q.All).First(m => m.Name == "EndOperation" && m.GetParameters().Length == 0).Invoke(manager, null);
    }

    static void AttachAll(object doc)
    {
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
            foreach (var el in Q.L(Q.Get(page, "AllChildren")))
                if (!(bool)Q.Get(el, "IsAttached")) Q.Call(el, "Attach");
        foreach (var panel in Q.L(Q.Get(doc, "WebPanels")))
            foreach (var pg in Q.L(Q.Get(panel, "Pages")))
                foreach (var el in Q.L(Q.Get(pg, "AllChildren")))
                    if (!(bool)Q.Get(el, "IsAttached")) Q.Call(el, "Attach");
        QSnapshots.Attach(doc);
    }

    static void Save(object doc, string path) { Q.Call(Q.T("QSC.QSys.Design.FileIO"), "SaveImp", path, doc); }

    static string DesignerVersion() { return Q.Asm.GetName().Version.ToString(); }

    static List<object> AllComponents(object doc)
    {
        var list = new List<object>();
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
            list.AddRange(Q.L(Q.Get(page, "AllChildren")).Where(k => k.GetType().Name == "Component" || QSnapshots.IsController(k)));
        return list;
    }

    internal static string NameOf(object comp)
    {
        var code = Convert.ToString(Q.TryGet(comp, "CodeName"));
        return string.IsNullOrEmpty(code) ? Convert.ToString(Q.Get(comp, "Label")) : code;
    }

    internal static void SetAccess(object comp, string mode)
    {
        if (Array.IndexOf(AccessModes, mode) < 0) throw new Exception("script_access must be one of " + string.Join(", ", AccessModes));
        var sa = comp.GetType().GetProperty("ScriptAccess", Q.All);
        sa.SetValue(comp, Enum.Parse(sa.PropertyType, mode), null);
    }

    static JArray ControlInfo(object comp)
    {
        var arr = new JArray();
        foreach (var d in Q.Descriptors(comp))
        {
            var id = Q.Cid(d);
            if (string.IsNullOrEmpty(id)) continue;
            var o = new JObject { { "id", id }, { "name", Convert.ToString(Q.TryF(d, "PrettyName")) },
                { "type", Convert.ToString(Q.TryF(d, "ControlType")) }, { "style", Convert.ToString(Q.TryF(d, "VisualStyle")) } };
            var min = Q.TryF(d, "_MinValue"); var max = Q.TryF(d, "_MaxValue");
            if (min != null) o["min"] = Convert.ToDouble(min);
            if (max != null) o["max"] = Convert.ToDouble(max);
            if ((bool)(Q.TryF(d, "IsReadonly") ?? false)) o["readonly"] = true;
            QControlPins.DescribeControl(o, d);
            arr.Add(o);
        }
        return arr;
    }

    static object Descriptor(object comp, string controlId)
    {
        return Q.Descriptors(comp).FirstOrDefault(d => Q.Cid(d) == controlId);
    }

    static JArray PinInfo(object comp)
    {
        var arr = new JArray();
        foreach (var dir in new[] { "Input", "Output" })
        {
            int i = 0;
            foreach (var p in Q.Pins(comp, dir))
            {
                var o = new JObject { { "dir", dir == "Input" ? "in" : "out" }, { "index", ++i }, { "name", Q.PinName(p) } };
                QControlPins.Describe(o, p);
                arr.Add(o);
            }
        }
        return arr;
    }

    static JObject Props(object comp)
    {
        var o = new JObject();
        foreach (var t in Q.Props(comp)) o[t.Item1] = t.Item2;
        return o;
    }

    // ---- info / probe -----------------------------------------------------------------
    static JObject Info()
    {
        var doc = NewDocument(Path.Combine(Path.GetTempPath(), "qsys-mcp-probe.qsys"), out _unused);
        var core = Q.Get(doc, "Core");
        var models = Q.L(Q.Get(core, "Models")).Select(m => Convert.ToString(Q.TryGet(m, "Value") ?? Q.TryGet(m, "Model") ?? m)).ToList();
        var coreModelType = core.GetType().GetProperty("CoreModel", Q.All).PropertyType;
        var res = new JObject {
            { "designer_version", DesignerVersion() },
            { "default_core", Convert.ToString(Q.Get(core, "CoreModelPretty")) },
            { "core_models", new JArray(Enum.GetNames(coreModelType)) },
            { "inventory_types", new JArray(InventoryTypes().Select(t => t.Name)) } };
        QHardware.AddInfo(res, core, InventoryTypes());   // features, usb_api, usb_bridge_types
        return res;
    }
    static object _unused;

    static IEnumerable<Type> InventoryTypes()
    {
        Type[] types;
        try { types = Q.Asm.GetTypes(); } catch (ReflectionTypeLoadException e) { types = e.Types.Where(x => x != null).ToArray(); }
        var baseT = Q.T("QSC.QSys.Design.Inventory.Base");
        return types.Where(t => t.Namespace == "QSC.QSys.Design.Inventory" && !t.IsAbstract && baseT.IsAssignableFrom(t)
            && t.GetConstructor(Type.EmptyTypes) != null).OrderBy(t => t.Name);
    }

    static JObject Probe(JObject req)
    {
        object manager;
        var doc = NewDocument(Path.Combine(Path.GetTempPath(), "qsys-mcp-probe.qsys"), out manager);
        var core = Q.Get(doc, "Core");
        QHardware.Strict = false;   // a USB check that fails is reported, not fatal
        if (req["core_model"] != null) SetCoreModel(core, (string)req["core_model"]);
        if (req["usb_sound_cards"] != null) Q.Set(Q.Get(core, "UsbBridge"), "USBCount", (int)req["usb_sound_cards"]);
        QHardware.Apply(doc, core, req);   // core.set, core.usb, design.set, as in a spec
        var res = new JObject { { "designer_version", DesignerVersion() }, { "core", Convert.ToString(Q.Get(core, "CoreModelPretty")) } };
        var itemsInfo = new JArray();
        foreach (var inv in (req["inventory"] as JArray ?? new JArray()))
            itemsInfo.Add(QHardware.ItemProbe(AddInventory(doc, (JObject)inv)));
        res["inventory_items"] = itemsInfo;
        var items = new JArray();
        foreach (var owner in new[] { core }.Concat(Q.L(Q.Get(doc, "Inventory")).Where(x => x != core)))
        {
            var seen = new HashSet<string>();
            foreach (var part in QHardware.WithParts(owner))   // the item's own blocks, then its USB bridge's and slots'
            foreach (var pi in Q.L(Q.TryGet(part, "PlaceableItems")))
            {
                var name = (string)Q.Get(pi, "Name");
                if (!seen.Add(name)) continue;
                var o = new JObject { { "item", Convert.ToString(Q.TryGet(owner, "Name")) }, { "placeable", name } };
                try
                {
                    var c = Q.PlaceFromInventory(part, name);
                    o["class"] = (string)Q.Get(c, "ClassName");
                    o["pins"] = PinInfo(c);
                    if ((bool?)req["controls"] == true) o["controls"] = ControlInfo(c);
                }
                catch (Exception e) { o["error"] = (e.InnerException ?? e).Message; }
                items.Add(o);
            }
        }
        if ((bool?)req["placeables"] != false) res["placeables"] = items;
        var classes = new JArray();
        foreach (JObject spec in (req["classes"] as JArray ?? new JArray()))
        {
            var o = new JObject { { "class", (string)spec["class"] } };
            try
            {
                var c = Q.NewComponent((string)spec["class"]);
                var props = spec["properties"] as JObject;
                if (props != null) foreach (var kv in props) Q.SetProp(c, kv.Key, Scalar(kv.Value));
                Q.Refresh(c);
                o["label"] = (string)Q.Get(c, "Label");
                o["properties"] = Props(c);
                QControlPins.Probe(o, c, spec);
                o["pins"] = PinInfo(c);
                o["controls"] = ControlInfo(c);
            }
            catch (Exception e) { o["error"] = (e.InnerException ?? e).Message; }
            classes.Add(o);
        }
        res["classes"] = classes;
        if ((bool?)req["hardware"] == true) res["hardware"] = QHardware.Discover(doc, core, InventoryTypes());
        if (QHardware.Applied.Count > 0) res["applied"] = QHardware.Applied;
        if (QHardware.Warnings.Count > 0) res["warnings"] = QHardware.Warnings;
        return res;
    }

    static object Scalar(JToken t)
    {
        switch (t.Type)
        {
            case JTokenType.Integer: return (int)t;
            case JTokenType.Float: return (double)t;
            case JTokenType.Boolean: return (bool)t;
            default: return (string)t;
        }
    }

    static void SetCoreModel(object core, string model)
    {
        var p = core.GetType().GetProperty("CoreModel", Q.All);
        var norm = model.Replace(" ", "").ToLowerInvariant();
        var name = Enum.GetNames(p.PropertyType).FirstOrDefault(n => n.ToLowerInvariant() == norm);
        if (name == null) throw new Exception("unknown core model '" + model + "'; known: " + string.Join(", ", Enum.GetNames(p.PropertyType)));
        p.SetValue(core, Enum.Parse(p.PropertyType, name), null);
    }

    static object AddInventory(object doc, JObject spec)
    {
        var t = QHardware.FindType(InventoryTypes(), (string)spec["type"]);
        var item = Activator.CreateInstance(t, true);
        ((IList)Q.Get(doc, "Inventory")).Add(item);
        if (spec["name"] != null) Q.Set(item, "Name", (string)spec["name"]);
        QHardware.ApplyItem(item, spec);   // set (enums by name, label or number), usb
        return item;
    }

    // ---- build ------------------------------------------------------------------------
    static JObject Build(JObject req)
    {
        var path = Path.GetFullPath((string)req["path"]);
        if (File.Exists(path) && (bool?)req["overwrite"] != true) throw new Exception(path + " exists; refusing to overwrite (overwrite=true replaces it)");
        var spec = (JObject)req["spec"];
        var cache = req["positions"] as JArray ?? new JArray();   // calibrated: [{class, control, min, max, value, position}]
        object manager;
        var doc = NewDocument(path, out manager);
        Q.Call(manager, "StartOperation");
        var core = Q.Get(doc, "Core");
        var warnings = new JArray();
        var valuesOut = new JArray();

        var coreSpec = spec["core"] as JObject;
        if (coreSpec != null)
        {
            if (coreSpec["model"] != null) SetCoreModel(core, (string)coreSpec["model"]);
            if (coreSpec["name"] != null) Q.Set(core, "Name", (string)coreSpec["name"]);
        }
        if (spec["usb_sound_cards"] != null) Q.Set(Q.Get(core, "UsbBridge"), "USBCount", (int)spec["usb_sound_cards"]);
        QHardware.Apply(doc, core, spec);   // core.set, core.usb, design.set
        var inventory = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        inventory[Convert.ToString(Q.Get(core, "Name"))] = core;
        foreach (JObject inv in (spec["inventory"] as JArray ?? new JArray()))
        {
            var item = AddInventory(doc, inv);
            var actual = Convert.ToString(Q.Get(item, "Name"));
            inventory[actual] = item;
            if (inv["name"] != null && (string)inv["name"] != actual)
            {
                inventory[(string)inv["name"]] = item;   // keep the spec's name usable
                warnings.Add("inventory item '" + inv["name"] + "' was renamed by Designer to '" + actual + "'");
            }
        }

        // schematic pages
        var pages = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        var pageNames = (spec["pages"] as JArray ?? new JArray("Main")).Select(x => (string)x).ToList();
        var docPages = (IList)Q.Get(doc, "Pages");
        for (int i = 0; i < pageNames.Count; i++)
        {
            object page;
            if (i < docPages.Count) page = docPages[i];
            else
            {
                page = Activator.CreateInstance(docPages[0].GetType(), true);
                docPages.Add(page);
            }
            Q.Set(page, "Title", pageNames[i]);
            if (Q.L(Q.Get(page, "Layers")).Count == 0) throw new Exception("could not create schematic page '" + pageNames[i] + "' (no layer)");
            pages[pageNames[i]] = page;
        }

        // components
        var comps = new Dictionary<string, object>(StringComparer.Ordinal);
        var compPage = new Dictionary<object, string>();
        foreach (JObject cs in (spec["components"] as JArray ?? new JArray()))
        {
            var name = (string)cs["name"];
            if (string.IsNullOrEmpty(name)) throw new Exception("every component needs a name");
            if (comps.ContainsKey(name)) throw new Exception("duplicate component name " + name);
            object c;
            var invSpec = cs["inventory"];
            if (invSpec != null)
            {
                string itemName = invSpec.Type == JTokenType.Object ? (string)invSpec["item"] : null;
                string placeable = invSpec.Type == JTokenType.Object ? (string)invSpec["placeable"] : (string)invSpec;
                object owner = itemName == null ? core : (inventory.ContainsKey(itemName) ? inventory[itemName] : null);
                if (owner == null) throw new Exception(name + ": no inventory item '" + itemName + "'");
                c = QHardware.Place(owner, placeable, invSpec.Type == JTokenType.Object ? (string)invSpec["class"] : null);
            }
            else
            {
                c = Q.NewComponent((string)cs["class"]);
                var props = cs["properties"] as JObject;
                if (props != null) foreach (var kv in props) Q.SetProp(c, kv.Key, Scalar(kv.Value));
                var tc = cs["text_controller"] as JObject;
                if (tc != null) Q.SetProp(c, "config", TextControllerConfig((JArray)tc["controls"]));
                Q.Refresh(c);
            }
            var pageName = (string)cs["page"] ?? pageNames[0];
            if (!pages.ContainsKey(pageName)) throw new Exception(name + ": no page '" + pageName + "' (declare it in spec.pages)");
            Q.Set(c, "Position", new System.Windows.Point((double?)cs["x"] ?? 0, (double?)cs["y"] ?? 0));
            Q.Call(Q.L(Q.Get(pages[pageName], "Layers"))[0], "AddChild", c);
            Q.Set(c, "CodeName", name);
            Q.Set(c, "UserLabel", (string)cs["label"] ?? name);
            SetAccess(c, (string)cs["script_access"] ?? "None");
            if ((string)Q.Get(c, "CodeName") != name) warnings.Add(name + ": Designer renamed it to " + Q.Get(c, "CodeName"));
            comps[name] = c;
            compPage[c] = pageName;
        }

        // stored values (after all components exist; Lua comes from text_controller.lua)
        foreach (JObject cs in (spec["components"] as JArray ?? new JArray()))
        {
            var c = comps[(string)cs["name"]];
            var tc = cs["text_controller"] as JObject;
            if (tc != null && tc["lua"] != null) StoreString(c, "code", (string)tc["lua"]);
            var values = cs["values"] as JObject;
            if (values == null) continue;
            foreach (var kv in values) valuesOut.Add(StoreValue(c, kv.Key, kv.Value, cache));
        }

        // control pins, before the wires that join them
        var controlPins = new JArray();
        foreach (JObject cs in (spec["components"] as JArray ?? new JArray()))
            if (cs["control_pins"] != null)
                foreach (var r in QControlPins.Add(comps[(string)cs["name"]], cs["control_pins"])) controlPins.Add(r);

        // wires
        int wireCount = 0;
        foreach (var w in (spec["wires"] as JArray ?? new JArray()))
        {
            string from, to;
            if (w.Type == JTokenType.Array) { from = (string)w[0]; to = (string)w[1]; }
            else { from = (string)w["from"]; to = (string)w["to"]; }
            var a = ResolvePin(comps, from, "Output");
            var b = ResolvePin(comps, to, "Input");
            if (Q.F(a, "Domain").ToString() != Q.F(b, "Domain").ToString())
                throw new Exception("wire " + from + " -> " + to + ": pin types differ (" + Q.F(a, "Domain") + " vs " + Q.F(b, "Domain") + ")");
            var pa = compPage[Q.F(a, "Provider")]; var pb = compPage[Q.F(b, "Provider")];
            if (pa != pb) throw new Exception("wire " + from + " -> " + to + " crosses pages (" + pa + " / " + pb + "); Q-SYS wires must stay on one page");
            var wire = Activator.CreateInstance(Q.T("QSC.QSys.Design.Wire"), true);
            Q.Set(wire, "PinA", a);
            Q.Set(wire, "PinB", b);
            Q.Call(Q.L(Q.Get(pages[pa], "Layers"))[0], "AddChild", wire);
            wireCount++;
        }

        // snapshot banks, once the blocks they store exist; their controllers join comps, so UCIs can use their buttons
        var snapshots = QSnapshots.Build(doc, spec["snapshots"] as JArray, comps, pages, pageNames[0], cache, warnings);

        // UCIs
        int uciElements = 0;
        var uciSizes = new JArray();
        foreach (JObject us in (spec["ucis"] as JArray ?? new JArray())) uciElements += BuildUci(doc, us, comps, uciSizes);

        EndOperation(manager);
        AttachAll(doc);
        Save(doc, path);
        var res = new JObject {
            { "path", path }, { "bytes", new FileInfo(path).Length }, { "designer_version", DesignerVersion() },
            { "snapshots", snapshots },
            { "core", Convert.ToString(Q.Get(core, "CoreModelPretty")) }, { "components", comps.Count }, { "wires", wireCount },
            { "uci_elements", uciElements }, { "uci_sizes", uciSizes }, { "values", valuesOut }, { "control_pins", controlPins }, { "warnings", warnings } };
        QHardware.Report(res, warnings);   // "hardware": what was set
        return res;
    }

    // "NAME.out.3", "NAME.in.2", "NAME.in.Reference" (pin name, case-insensitive), "NAME.out.connected"
    // (a control pin by its control code name)
    static object ResolvePin(Dictionary<string, object> comps, string endpoint, string direction)
    {
        var dirTag = direction == "Input" ? ".in." : ".out.";
        int at = endpoint.LastIndexOf(dirTag, StringComparison.OrdinalIgnoreCase);
        if (at < 0) throw new Exception("wire endpoint '" + endpoint + "' must look like NAME" + dirTag + "N or NAME" + dirTag + "PinName");
        var name = endpoint.Substring(0, at);
        var pinRef = endpoint.Substring(at + dirTag.Length);
        object c;
        if (!comps.TryGetValue(name, out c)) throw new Exception("wire endpoint '" + endpoint + "': no component " + name);
        var pins = Q.Pins(c, direction);
        int n;
        if (int.TryParse(pinRef, out n))
        {
            if (n < 1 || n > pins.Count) throw new Exception(name + " has " + pins.Count + " " + direction.ToLower() + " pins, not " + n);
            return pins[n - 1];
        }
        var byName = QControlPins.Match(pins, pinRef);
        if (byName.Count != 1)
            throw new Exception(name + " has no single " + direction.ToLower() + " pin named '" + pinRef + "'; pins: " +
                string.Join(", ", pins.Select(QControlPins.Label)) + QControlPins.Hint(c, pinRef, direction));
        return byName[0];
    }

    // ---- stored control values --------------------------------------------------------
    // The engine applies a stored control's normalised Position (0..1) when a design starts, not its
    // Value. Positions come from: an explicit "position", the calibration cache (measured on the
    // engine), or a rule. Rules are exact for booleans/integers and for the tapers measured so far;
    // anything else is reported as "assumed" so calibrate_values can confirm it.
    internal static JObject StoreValue(object comp, string controlId, JToken spec, JArray cache)
    {
        var d = Descriptor(comp, controlId);
        if (d == null) throw new Exception(NameOf(comp) + " has no control " + controlId);
        JToken v = spec; double? explicitPos = null;
        if (spec.Type == JTokenType.Object) { v = spec["value"]; explicitPos = (double?)spec["position"]; }
        var result = new JObject { { "component", NameOf(comp) }, { "class", (string)Q.Get(comp, "ClassName") }, { "control", controlId } };
        if (v.Type == JTokenType.String)
        {
            StoreString(comp, controlId, (string)v);
            result["value"] = v; result["source"] = "string";
            return result;
        }
        double value = v.Type == JTokenType.Boolean ? ((bool)v ? 1 : 0) : (double)v;
        double min = Convert.ToDouble(Q.TryF(d, "_MinValue") ?? 0), max = Convert.ToDouble(Q.TryF(d, "_MaxValue") ?? 1);
        var type = Convert.ToString(Q.TryF(d, "ControlType"));
        var known = KnownRange((string)Q.Get(comp, "ClassName"), controlId);
        if (known != null) { min = known[0]; max = known[1]; }
        if (value < min - 1e-9 || value > max + 1e-9)
        {
            if (known != null) throw new Exception(NameOf(comp) + "." + controlId + "=" + value + " is outside " + min + ".." + max);
            result["warning"] = "outside the descriptor range " + min + ".." + max + " (some I/O controls report a generic range); calibrate to confirm";
            min = Math.Min(min, value); max = Math.Max(max, value);
        }
        var cls = (string)Q.Get(comp, "ClassName");
        var hit = cache.FirstOrDefault(e => (string)e["class"] == cls && (string)e["control"] == controlId &&
            Math.Abs((double)e["min"] - min) < 1e-6 && Math.Abs((double)e["max"] - max) < 1e-6 && Math.Abs((double)e["value"] - value) < 1e-6);
        double pos; string source;
        if (explicitPos.HasValue) { pos = explicitPos.Value; source = "explicit"; }
        else if (hit != null) { pos = (double)hit["position"]; source = "calibrated"; }
        else
        {
            string rule;
            pos = PositionRule((string)Q.Get(comp, "ClassName"), controlId, type, value, min, max, out rule);
            source = rule;
        }
        var cvT = Q.T("QSC.QSys.Design.CachedControlValue");
        object cv = Activator.CreateInstance(cvT);
        cvT.GetField("Value").SetValue(cv, (float)value);
        cvT.GetField("Position").SetValue(cv, (float)pos);
        Q.Call(comp, "UpdateControlValue", controlId, cv);
        result["value"] = value; result["position"] = pos; result["min"] = min; result["max"] = max;
        result["source"] = source;
        return result;
    }

    // Real ranges of controls whose descriptors report a generic range (measured on the engine).
    static double[] KnownRange(string cls, string controlId)
    {
        if (cls.StartsWith("io_card_flex") || cls.StartsWith("io_card_mic") || cls.Contains("mic_line"))
        {
            if (controlId.EndsWith("_preamp_gain")) return new[] { 0.0, 64.0 };
            if (controlId.EndsWith("_sensitivity")) return new[] { -40.0, 24.0 };
        }
        return null;
    }

    static double PositionRule(string cls, string controlId, string type, double v, double min, double max, out string rule)
    {
        if (max <= min) { rule = "exact"; return 0; }
        double lin = (v - min) / (max - min);
        if (type == "Boolean" || type == "Integer" || (min == 0 && max == 1)) { rule = "exact"; return lin; }
        if (cls == "mixer" && controlId.EndsWith("_gain") && controlId.Contains("_output_") && controlId.StartsWith("input_") && max == 10 && Math.Abs(v + 6) < 1e-6)
        { rule = "measured"; return 0.58499997; }
        if (controlId.Contains("frequency") && min > 0)
        { rule = min == 10 && max == 20000 ? "measured-log" : "assumed-log"; return Math.Log10(v / min) / Math.Log10(max / min); }
        if (controlId == "ratio" && min >= 1) { rule = min == 1 && max == 100 ? "measured-log" : "assumed-log"; return Math.Log10(v / min) / Math.Log10(max / min); }
        rule = (cls == "gain" && controlId == "gain") || KnownRange(cls, controlId) != null || (cls == "limiter_peak" && controlId == "threshold_level")
            ? "measured-linear" : "assumed-linear";
        return lin;
    }

    static void StoreString(object comp, string controlId, string text)
    {
        var cvT = Q.T("QSC.QSys.Design.CachedControlValue");
        object cv = Activator.CreateInstance(cvT);
        cvT.GetField("String").SetValue(cv, text);
        Q.Call(comp, "UpdateControlValue", controlId, cv);
    }

    // ---- Text Controller --------------------------------------------------------------
    static string TextControllerConfig(JArray controls)
    {
        var specs = new JArray();
        foreach (JObject c in controls)
        {
            var type = ((string)c["type"] ?? "button").ToLowerInvariant();
            var o = new JObject { { "Name", (string)c["name"] }, { "Count", (double?)c["count"] ?? 1 }, { "PinStyle", (string)c["pins"] ?? "None" } };
            switch (type)
            {
                case "button":
                    o["ControlType"] = "Button"; o["ButtonType"] = Cap((string)c["button_type"] ?? "Toggle");
                    o["ControlUnit"] = "Integer"; o["Min"] = 0; o["Max"] = 1; break;
                case "knob":
                    o["ControlType"] = "Knob"; o["ControlUnit"] = (string)c["unit"] ?? "dB";
                    o["Min"] = (double?)c["min"] ?? 0; o["Max"] = (double?)c["max"] ?? 1; break;
                case "led": case "indicator":
                    o["ControlType"] = "Indicator"; o["IndicatorType"] = Cap((string)c["indicator_type"] ?? "Led"); break;
                case "status_text": case "text_indicator":
                    o["ControlType"] = "Indicator"; o["IndicatorType"] = "Text"; break;
                case "text":
                    o["ControlType"] = "Text"; o["TextType"] = (string)c["text_type"] ?? "TextBox"; break;
                default: throw new Exception("text controller control type must be button, knob, led, status_text or text, not " + type);
            }
            specs.Add(o);
        }
        // Stored as the minimal spec Designer's editor would write. (Round-tripping it through Designer's
        // own DeviceControllerSpecs type adds fields that Designer 10's native reader rejects.)
        return new JObject { { "ControlSpecs", specs } }.ToString(Newtonsoft.Json.Formatting.None);
    }

    static string Cap(string s) { return string.IsNullOrEmpty(s) ? s : char.ToUpperInvariant(s[0]) + s.Substring(1); }

    // ---- UCI ----------------------------------------------------------------------------
    static int BuildUci(object doc, JObject us, Dictionary<string, object> comps, JArray sizes)
    {
        var panel = Activator.CreateInstance(Q.T("QSC.QSys.Design.WebPanels.Panel"), true);
        Q.Set(panel, "Title", (string)us["name"]);
        ((IList)Q.Get(doc, "WebPanels")).Add(panel);
        var size = new JObject { { "name", (string)us["name"] } };
        if (us["width"] != null || us["height"] != null) QUci.SetSize(panel, (int?)us["width"] ?? 0, (int?)us["height"] ?? 0, size);
        else QUci.ReadSize(panel, size);
        sizes.Add(size);
        var pages = (IList)Q.Get(panel, "Pages");
        int count = 0, index = 0;
        foreach (JObject ps in (us["pages"] as JArray ?? new JArray()))
        {
            object page;
            if (index < pages.Count) page = pages[index];
            else
            {
                page = Activator.CreateInstance(Q.T("QSC.QSys.Design.WebPanels.Page"), true);
                try { Q.Set(page, "Panel", panel); } catch { }
                pages.Add(page);
            }
            index++;
            Q.Set(page, "Title", (string)ps["name"]);
            if (ps["color"] != null) Q.Set(page, "Color", Q.C((string)ps["color"]));
            var layer = Q.L(Q.Get(page, "Layers"))[0];
            // group boxes first: they get the page's lowest ZOrder, so they sit behind everything else on it
            var elements = (ps["elements"] as JArray ?? new JArray()).Cast<JObject>().ToList();
            foreach (var es in elements.Where(e => QUci.IsFrame(e)).Concat(elements.Where(e => !QUci.IsFrame(e))))
            {
                var kind = (string)es["type"] ?? "control";
                object el;
                if (kind == "text") el = UciText(layer, es);
                else if (kind == "control") el = UciControl(layer, es, comps);
                else if (QUci.IsGraphic(kind)) el = QUci.Graphic(layer, kind, es);
                else throw new Exception("UCI element type must be control, text, group_box or header, not " + kind);
                Q.Set(el, "ZOrder", ++count);
            }
        }
        return count;
    }

    static object UciControl(object layer, JObject es, Dictionary<string, object> comps)
    {
        var name = (string)es["component"]; var controlId = (string)es["control"];
        object comp;
        if (!comps.TryGetValue(name, out comp)) throw new Exception("UCI control: no component " + name);
        var d = Descriptor(comp, controlId);
        if (d == null) throw new Exception("UCI control: " + name + " has no control " + controlId);
        var cs = Q.Helper.GetMethod("CreateControlStyle", Q.All).Invoke(null, new[] { comp, d, false });
        Place(cs, es);
        if (es["style"] != null) Q.SetEnum(cs, "VisualStyle", (string)es["style"]);
        if (es["label"] != null) Q.Set(cs, "Label", (string)es["label"]);
        if (es["color"] != null) Q.Set(cs, "Color", Q.C((string)es["color"]));
        if (es["text_color"] != null) { Q.Set(cs, "AutoColorText", false); Q.Set(cs, "TextColor", Q.C((string)es["text_color"])); }
        if (es["font_size"] != null) Q.Set(cs, "FontSize", (double)es["font_size"]);
        if (es["corner_radius"] != null) Q.Set(cs, "CornerRadius", (double)es["corner_radius"]);
        if (es["off_color"] != null) { Q.Set(cs, "IsOffColorOverridden", true); Q.Set(cs, "OffColor", Q.C((string)es["off_color"])); }
        if (es["show_textbox"] != null) Q.Set(cs, "IsTextboxVisible", (bool)es["show_textbox"]);
        Q.Call(layer, "AddChild", cs);
        return cs;
    }

    static object UciText(object layer, JObject es)
    {
        var tb = Activator.CreateInstance(Q.T("QSC.QSys.Design.Graphics.TextBlock"), true);
        Q.Set(tb, "Label", (string)es["text"] ?? "");
        Place(tb, es);
        Q.Set(tb, "TextColor", Q.C((string)es["color"] ?? "#FFFFFFFF"));
        Q.Set(tb, "FontSize", (double?)es["font_size"] ?? 12);
        Q.SetEnum(tb, "HorizontalTextAlignment", (string)es["align"] ?? "Left");
        Q.SetEnum(tb, "VerticalTextAlignment", "Center");
        Q.Call(layer, "AddChild", tb);
        return tb;
    }

    static void Place(object el, JObject es)
    {
        Q.Set(el, "Position", new System.Windows.Point((double?)es["x"] ?? 0, (double?)es["y"] ?? 0));
        Q.Set(el, "Size", new System.Windows.Size((double?)es["w"] ?? 100, (double?)es["h"] ?? 40));
    }

    // ---- read -----------------------------------------------------------------------------
    static JObject Read(string path)
    {
        path = Path.GetFullPath(path);
        var doc = Q.Load(path);
        var core = Q.Get(doc, "Core");
        var res = new JObject { { "path", path }, { "bytes", new FileInfo(path).Length }, { "designer_version", DesignerVersion() },
            { "core", new JObject { { "model", Convert.ToString(Q.Get(core, "CoreModelPretty")) }, { "name", Convert.ToString(Q.Get(core, "Name")) } } } };
        QHardware.Describe(doc, core, res);   // core.usb, core.settings, design.settings
        res["usb_slots"] = QHardware.SlotInfo(core);
        res["inventory"] = new JArray(Q.L(Q.Get(doc, "Inventory")).Select(i => QHardware.ItemInfo(i, core)));

        var wires = new List<object>(); var comps = new List<object>(); var pageOf = new Dictionary<object, string>();
        var pagesOut = new JArray();
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
        {
            var kids = Q.L(Q.Get(page, "AllChildren"));
            var title = Convert.ToString(Q.Get(page, "Title"));
            pagesOut.Add(title);
            foreach (var k in kids)
            {
                if (k.GetType().Name == "Component") { comps.Add(k); pageOf[k] = title; }
                else if (k.GetType().Name == "Wire") wires.Add(k);
            }
        }
        res["pages"] = pagesOut;
        var pinRef = new Dictionary<object, string>();
        foreach (var c in comps)
            foreach (var dir in new[] { "Input", "Output" })
            {
                int i = 0;
                foreach (var p in Q.Pins(c, dir)) pinRef[p] = NameOf(c) + (dir == "Input" ? ".in." : ".out.") + (++i);
            }
        Func<object, string> Ref = p => { string r; return pinRef.TryGetValue(p, out r) ? r : "?." + Q.PinName(p); };
        var compsOut = new JArray();
        foreach (var c in comps)
        {
            var o = new JObject { { "name", NameOf(c) }, { "class", (string)Q.Get(c, "ClassName") }, { "label", Convert.ToString(Q.Get(c, "Label")) },
                { "page", pageOf[c] }, { "script_access", Q.Get(c, "ScriptAccess").ToString() } };
            var pos = (System.Windows.Point)Q.Get(c, "Position");
            o["x"] = pos.X; o["y"] = pos.Y;
            o["properties"] = Props(c);
            var vals = new JObject();
            var cached = (IDictionary)Q.Get(c, "CachedControlValues");
            foreach (var k in cached.Keys)
            {
                var v = cached[k]; var s = (string)v.GetType().GetField("String").GetValue(v);
                var vo = new JObject();
                if (s != null) vo["string"] = s;
                else
                {
                    vo["value"] = Convert.ToDouble(v.GetType().GetField("Value").GetValue(v));
                    var p = Convert.ToDouble(v.GetType().GetField("Position").GetValue(v));
                    vo["position"] = double.IsNaN(p) ? null : (JToken)p;
                    var d = Descriptor(c, Convert.ToString(k));
                    var known = KnownRange((string)Q.Get(c, "ClassName"), Convert.ToString(k));
                    if (known != null) { vo["min"] = known[0]; vo["max"] = known[1]; }
                    else if (d != null && Q.TryF(d, "_MinValue") != null)
                    { vo["min"] = Convert.ToDouble(Q.TryF(d, "_MinValue")); vo["max"] = Convert.ToDouble(Q.TryF(d, "_MaxValue")); }
                }
                vals[Convert.ToString(k)] = vo;
            }
            o["values"] = vals;
            o["pins"] = PinInfo(c);
            compsOut.Add(o);
        }
        res["components"] = compsOut;
        res["wires"] = new JArray(wires.Select(w => new JArray(Ref(Q.Get(w, "PinA")), Ref(Q.Get(w, "PinB")))));
        var byId = comps.ToDictionary(c => Convert.ToString(Q.Get(c, "Id")), c => c);
        foreach (var pc in QSnapshots.Controllers(doc)) byId[Convert.ToString(Q.Get(pc, "Id"))] = pc;
        var ucis = new JArray();
        foreach (var panel in Q.L(Q.Get(doc, "WebPanels")))
        {
            var pagesJ = new JArray();
            foreach (var pg in Q.L(Q.Get(panel, "Pages")))
            {
                var els = new JArray();
                foreach (var el in Q.L(Q.Get(pg, "AllChildren")))
                {
                    var pos = (System.Windows.Point)Q.Get(el, "Position");
                    var size = Q.TryGet(el, "Size");
                    var eo = new JObject { { "type", el.GetType().Name }, { "x", pos.X }, { "y", pos.Y } };
                    if (size is System.Windows.Size) { eo["w"] = ((System.Windows.Size)size).Width; eo["h"] = ((System.Windows.Size)size).Height; }
                    var z = Q.TryGet(el, "ZOrder");
                    if (z != null) eo["z"] = Convert.ToInt32(z);
                    var label = Convert.ToString(Q.TryGet(el, "Label"));
                    if (!string.IsNullOrEmpty(label)) eo["label"] = label;
                    if (el.GetType().Name == "ControlStyle")
                    {
                        object comp;
                        eo["component"] = byId.TryGetValue(Convert.ToString(Q.Get(el, "Id")), out comp) ? NameOf(comp) : null;
                        eo["control"] = Convert.ToString(Q.Get(el, "Name"));
                        eo["style"] = Convert.ToString(Q.TryGet(el, "VisualStyle"));
                    }
                    else QUci.ReadGraphic(el, eo);
                    els.Add(eo);
                }
                pagesJ.Add(new JObject { { "name", Convert.ToString(Q.Get(pg, "Title")) }, { "elements", els } });
            }
            var uo = new JObject { { "name", Convert.ToString(Q.Get(panel, "Title")) } };
            QUci.ReadSize(panel, uo);
            uo["pages"] = pagesJ;
            ucis.Add(uo);
        }
        res["ucis"] = ucis;
        res["snapshots"] = QSnapshots.Read(doc);
        return res;
    }

    // ---- edit saved files -----------------------------------------------------------------
    // set_values: {path, out_path?, values:[{component, control, value, position}]} - patches stored values
    static JObject SetValues(JObject req)
    {
        var path = Path.GetFullPath((string)req["path"]);
        var outPath = Path.GetFullPath((string)req["out_path"] ?? path);
        var doc = Q.Load(path);
        var comps = AllComponents(doc).ToDictionary(NameOf, c => c);
        var done = new JArray();
        foreach (JObject v in (JArray)req["values"])
        {
            object c;
            if (!comps.TryGetValue((string)v["component"], out c)) throw new Exception("no component " + v["component"]);
            done.Add(StoreValue(c, (string)v["control"], v, new JArray()));
        }
        Save(doc, outPath);
        return new JObject { { "path", outPath }, { "values", done } };
    }

    // copy: {path, out_path, script_access?, script_access_map?} - a copy, optionally with every block's Script
    // Access changed, and/or set per block from {name: mode} (to put a test copy's access back)
    static JObject Copy(JObject req)
    {
        var path = Path.GetFullPath((string)req["path"]);
        var outPath = Path.GetFullPath((string)req["out_path"]);
        if (File.Exists(outPath) && (bool?)req["overwrite"] != true) throw new Exception(outPath + " exists");
        var doc = Q.Load(path);
        int changed = 0;
        if (req["script_access"] != null)
            foreach (var c in AllComponents(doc)) { SetAccess(c, (string)req["script_access"]); changed++; }
        var map = req["script_access_map"] as JObject;
        if (map != null)
            foreach (var c in AllComponents(doc))
            {
                var mode = (string)map[NameOf(c)];
                if (mode != null) { SetAccess(c, mode); changed++; }
            }
        Save(doc, outPath);
        return new JObject { { "path", outPath }, { "script_access_changed", changed } };
    }
}
