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
            case "copy_snapshots": return CopySnapshots(req);
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
    }

    static void Save(object doc, string path) { Q.Call(Q.T("QSC.QSys.Design.FileIO"), "SaveImp", path, doc); }

    static string DesignerVersion() { return Q.Asm.GetName().Version.ToString(); }

    // Schematic blocks, including subclasses such as Snapshot Controllers (Snapshot.PlacedComponent).
    static bool IsComponent(object el)
    {
        for (var t = el.GetType(); t != null; t = t.BaseType)
            if (t.FullName == "QSC.QSys.Design.Component") return true;
        return false;
    }

    static List<object> AllComponents(object doc)
    {
        var list = new List<object>();
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
            list.AddRange(Q.L(Q.Get(page, "AllChildren")).Where(IsComponent));
        return list;
    }

    static string NameOf(object comp)
    {
        var code = Convert.ToString(Q.TryGet(comp, "CodeName"));
        return string.IsNullOrEmpty(code) ? Convert.ToString(Q.Get(comp, "Label")) : code;
    }

    static void SetAccess(object comp, string mode)
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
                var signal = Convert.ToString(Q.Get(p, "Label"));
                if (!string.IsNullOrEmpty(signal)) o["signal"] = signal;
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
        return new JObject {
            { "designer_version", DesignerVersion() },
            { "default_core", Convert.ToString(Q.Get(core, "CoreModelPretty")) },
            { "core_models", new JArray(Enum.GetNames(coreModelType)) },
            { "inventory_types", new JArray(InventoryTypes().Select(t => t.Name)) } };
    }
    static object _unused;

    static IEnumerable<Type> InventoryTypes()
    {
        Type[] types;
        try { types = Q.Asm.GetTypes(); } catch (ReflectionTypeLoadException e) { types = e.Types.Where(x => x != null).ToArray(); }
        var baseT = Q.T("QSC.QSys.Design.Inventory.Base");
        return types.Where(t => t.Namespace == "QSC.QSys.Design.Inventory" && !t.IsAbstract && baseT.IsAssignableFrom(t)
            && (t.GetConstructor(Type.EmptyTypes) != null || ModelConstructor(t) != null)).OrderBy(t => t.Name);
    }

    // Items such as cameras are built from a model enum (new OnVifCamera(OnVifCameraModel)).
    static ConstructorInfo ModelConstructor(Type t)
    {
        return t.GetConstructors().FirstOrDefault(c => c.GetParameters().Length == 1 && c.GetParameters()[0].ParameterType.IsEnum);
    }

    static JObject Probe(JObject req)
    {
        object manager;
        var doc = NewDocument(Path.Combine(Path.GetTempPath(), "qsys-mcp-probe.qsys"), out manager);
        var core = Q.Get(doc, "Core");
        if (req["core_model"] != null) SetCoreModel(core, (string)req["core_model"]);
        if (req["usb_sound_cards"] != null) Q.Set(Q.Get(core, "UsbBridge"), "USBCount", (int)req["usb_sound_cards"]);
        var res = new JObject { { "designer_version", DesignerVersion() }, { "core", Convert.ToString(Q.Get(core, "CoreModelPretty")) } };
        var itemsInfo = new JArray();
        foreach (var inv in (req["inventory"] as JArray ?? new JArray()))
        {
            var item = AddInventory(doc, (JObject)inv);
            var props = new JObject();
            foreach (var pi in item.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                if (!pi.CanWrite || pi.GetIndexParameters().Length > 0) continue;
                var pt = pi.PropertyType;
                if (!(pt.IsPrimitive || pt.IsEnum || pt == typeof(string))) continue;
                object v = null; try { v = pi.GetValue(item, null); } catch { continue; }
                props[pi.Name] = pt.IsEnum ? (JToken)(v + " (" + string.Join("|", Enum.GetNames(pt)) + ")") : JToken.FromObject(v ?? "");
            }
            itemsInfo.Add(new JObject { { "type", item.GetType().Name }, { "name", Convert.ToString(Q.Get(item, "Name")) }, { "settable", props } });
        }
        res["inventory_items"] = itemsInfo;
        var items = new JArray();
        foreach (var owner in new[] { core }.Concat(Q.L(Q.Get(doc, "Inventory")).Where(x => x != core)))
            foreach (var pi in Q.L(Q.TryGet(owner, "PlaceableItems")))
            {
                var name = (string)Q.Get(pi, "Name");
                var o = new JObject { { "item", Convert.ToString(Q.TryGet(owner, "Name")) }, { "placeable", name } };
                try
                {
                    var c = Q.PlaceFromInventory(owner, name);
                    o["class"] = (string)Q.Get(c, "ClassName");
                    o["pins"] = PinInfo(c);
                    if ((bool?)req["controls"] == true) o["controls"] = ControlInfo(c);
                }
                catch (Exception e) { o["error"] = (e.InnerException ?? e).Message; }
                items.Add(o);
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
                o["property_choices"] = PropertyChoices(c);
                o["pins"] = PinInfo(c);
                o["controls"] = ControlInfo(c);
            }
            catch (Exception e) { o["error"] = (e.InnerException ?? e).Message; }
            classes.Add(o);
        }
        res["classes"] = classes;
        return res;
    }

    // For list properties: what each value means, e.g. bandwidth_q_factor: 0 Bandwidth, 1 Q-Factor.
    static JObject PropertyChoices(object comp)
    {
        var res = new JObject();
        var descs = (IEnumerable)Q.Helper.GetMethods(Q.All).First(m => m.Name == "GetComponentProperties" && m.GetParameters().Length == 1)
            .Invoke(null, new[] { comp });
        foreach (var d in descs)
        {
            var choices = Q.TryF(d, "Choices") as IEnumerable;
            if (choices == null) continue;
            var arr = new JArray();
            foreach (var ch in choices)
                arr.Add(new JObject { { "name", Convert.ToString(Q.TryF(ch, "PrettyName")) }, { "value", Convert.ToString(Q.TryF(ch, "Value"), System.Globalization.CultureInfo.InvariantCulture) } });
            if (arr.Count > 0) res[Convert.ToString(Q.TryF(d, "CodeName"))] = arr;
        }
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
        var typeName = (string)spec["type"];
        var t = InventoryTypes().FirstOrDefault(x => x.Name == typeName);
        if (t == null) throw new Exception("unknown inventory type '" + typeName + "' (see info.inventory_types)");
        object item;
        var ctor = ModelConstructor(t);
        if (spec["model"] != null || t.GetConstructor(Type.EmptyTypes) == null)
        {
            if (ctor == null) throw new Exception(typeName + " takes no model");
            var modelT = ctor.GetParameters()[0].ParameterType;
            if (spec["model"] == null) throw new Exception(typeName + " needs a model: " + string.Join(", ", Enum.GetNames(modelT)));
            item = ctor.Invoke(new[] { EnumValue(modelT, (string)spec["model"]) });
        }
        else item = Activator.CreateInstance(t, true);
        ((IList)Q.Get(doc, "Inventory")).Add(item);
        if (spec["name"] != null) Q.Set(item, "Name", (string)spec["name"]);
        SetMembers(item, spec["set"] as JObject);
        var usb = spec["usb_bridge"] as JObject;
        if (usb != null)
        {
            var bridge = Q.TryGet(item, "UsbBridge");
            if (bridge == null) throw new Exception(typeName + " has no USB bridge");
            ConfigureUsbBridge(bridge, usb);
        }
        return item;
    }

    static object EnumValue(Type et, string name)
    {
        var match = Enum.GetNames(et).FirstOrDefault(n => n.Equals(name, StringComparison.OrdinalIgnoreCase));
        if (match == null) throw new Exception(et.Name + " has no value '" + name + "'; values: " + string.Join(", ", Enum.GetNames(et)));
        return Enum.Parse(et, match);
    }

    // Sets public properties by name: enums by name, the rest converted from the JSON scalar.
    static void SetMembers(object target, JObject set)
    {
        if (set == null) return;
        foreach (var kv in set)
        {
            var p = target.GetType().GetProperty(kv.Key, Q.All);
            if (p == null || !p.CanWrite) throw new Exception(target.GetType().Name + " has no settable property " + kv.Key);
            var pt = Nullable.GetUnderlyingType(p.PropertyType) ?? p.PropertyType;
            object v = pt.IsEnum ? EnumValue(pt, (string)kv.Value) : Convert.ChangeType(Scalar(kv.Value), pt);
            p.SetValue(target, v, null);
        }
    }

    // USB bridge of the Core or of a peripheral (I/O-USB Bridge...). Designer only creates the
    // bridge's USB slots (and so its Speakerphone/Soundcard placeables) when USBSlotType changes,
    // so the slot type is set through None first.
    static void ConfigureUsbBridge(object bridge, JObject usb)
    {
        var rest = (JObject)usb.DeepClone();
        var slot = (string)rest["USBSlotType"];
        if (slot != null)
        {
            rest.Remove("USBSlotType");
            Q.SetEnum(bridge, "USBSlotType", "None");
            Q.SetEnum(bridge, "USBSlotType", slot);
        }
        SetMembers(bridge, rest);
    }

    // Exposes control pins (Properties > Control Pins in Designer), e.g. "connected", "active".
    static void AddControlPins(object comp, JArray ids)
    {
        if (ids == null) return;
        var specT = Q.T("QSC.QSys.windows.Editors.PinSpec");
        var create = Q.T("QSC.QSys.windows.Editors.ControlPinEditor").GetMethod("CreatePins", Q.All);
        foreach (var idTok in ids)
        {
            var id = (string)idTok;
            var d = Descriptor(comp, id);
            if (d == null) throw new Exception(NameOf(comp) + " has no control " + id + " for a control pin");
            var style = Q.TryF(d, "PinStyle");
            if (style == null || style.ToString() == "None") throw new Exception(NameOf(comp) + "." + id + " cannot have a control pin");
            if (Q.L(Q.Get(comp, "Pins")).Any(p => (string)Q.F(p, "CodeName") == id)) continue;
            var spec = Activator.CreateInstance(specT);
            var pretty = Convert.ToString(Q.TryF(d, "PrettyName"));
            Q.Set(spec, "CodeName", id);
            Q.Set(spec, "PrettyName", pretty);
            Q.Set(spec, "Label", pretty);
            specT.GetProperty("PinStyle").SetValue(spec, style, null);
            create.Invoke(null, new[] { comp, spec });
        }
    }

    // A schematic plugin, from an installed .qplug (by its name or id) or from a given file.
    static string PluginClass(JToken plugin, string pluginFile)
    {
        var mgrT = Q.T("QSC.QSys.Design.Plugins.Manager");
        var files = new List<string>();
        if (pluginFile != null) files.Add(Path.GetFullPath(pluginFile));
        else
        {
            var docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            foreach (var dir in new[] { Path.Combine(docs, "QSC", "Q-Sys Designer", "Plugins"), Path.Combine(docs, "QSC", "Q-Sys Designer", "Assets") })
                if (Directory.Exists(dir)) files.AddRange(Directory.GetFiles(dir, "*.qplug", SearchOption.AllDirectories));
        }
        var want = (string)plugin;
        var seen = new List<string>();
        foreach (var f in files)
        {
            var text = File.ReadAllText(f);
            var name = System.Text.RegularExpressions.Regex.Match(text, "Name\\s*=\\s*\"([^\"]+)\"").Groups[1].Value;
            var id = System.Text.RegularExpressions.Regex.Match(text, "Id\\s*=\\s*\"([^\"]+)\"").Groups[1].Value;
            seen.Add(name);
            var shortName = name.Contains("~") ? name.Substring(name.LastIndexOf('~') + 1) : name;
            if (pluginFile == null && !(string.Equals(want, name, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(want, shortName, StringComparison.OrdinalIgnoreCase) || string.Equals(want, id, StringComparison.OrdinalIgnoreCase)))
                continue;
            Q.Call(mgrT, "ReadPluginToCatalog", f, false);
            var all = (IDictionary)mgrT.GetProperty("AllPlugins", Q.All).GetValue(null, null);
            foreach (DictionaryEntry kv in all)
                if (string.Equals(Convert.ToString(Q.Get(kv.Value, "FilePath")), f, StringComparison.OrdinalIgnoreCase))
                    return (string)Q.Get(kv.Value, "ClassId");
            throw new Exception("Designer could not read the plugin " + f);
        }
        throw new Exception("plugin '" + want + "' is not installed (install it with Designer's Asset Manager, or give plugin_file); installed: " +
            (seen.Count == 0 ? "none" : string.Join(", ", seen)));
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
        if (coreSpec != null && coreSpec["usb_bridge"] is JObject) ConfigureUsbBridge(Q.Get(core, "UsbBridge"), (JObject)coreSpec["usb_bridge"]);
        SetMembers(Q.Get(doc, "Properties"), spec["design_properties"] as JObject);
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
                c = Q.PlaceFromInventory(owner, placeable);
            }
            else
            {
                var cls = cs["plugin"] != null || cs["plugin_file"] != null ? PluginClass(cs["plugin"], (string)cs["plugin_file"]) : (string)cs["class"];
                c = Q.NewComponent(cls);
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
            if (((string)Q.Get(c, "ClassName")).StartsWith("%PLUGIN%")) Q.Call(Q.T("QSC.QSys.Design.Plugins.Manager"), "AddPluginComponent", c);
            AddControlPins(c, cs["control_pins"] as JArray);
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

        // wires
        int wireCount = 0;
        var signalNames = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        foreach (var w in (spec["wires"] as JArray ?? new JArray()))
        {
            string from, to, signal;
            if (w.Type == JTokenType.Array) { from = (string)w[0]; to = (string)w[1]; signal = ((JArray)w).Count > 2 ? (string)w[2] : null; }
            else { from = (string)w["from"]; to = (string)w["to"]; signal = (string)w["signal"]; }
            var a = ResolvePin(comps, from, "Output");
            // A signal name labels the source pin; Designer shows it on every wire leaving that pin.
            if (!string.IsNullOrEmpty(signal))
            {
                object owner;
                if (signalNames.TryGetValue(signal, out owner) && owner != a)
                    throw new Exception("signal name '" + signal + "' is already used by another output pin (" + from + ")");
                var current = Convert.ToString(Q.Get(a, "Label"));
                if (!string.IsNullOrEmpty(current) && current != signal)
                    throw new Exception(from + " already carries the signal name '" + current + "', not '" + signal + "'");
                Q.Set(a, "Label", signal);
                signalNames[signal] = a;
            }
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

        // connections by signal name only: the same name on an output pin and on input pins joins
        // them without a wire (pins can be far apart or on different pages)
        int signalLinks = 0;
        foreach (var s in (spec["signals"] as JArray ?? new JArray()))
        {
            string from, to, name;
            if (s.Type == JTokenType.Array) { from = (string)s[0]; to = (string)s[1]; name = ((JArray)s).Count > 2 ? (string)s[2] : null; }
            else { from = (string)s["from"]; to = (string)s["to"]; name = (string)s["signal"]; }
            if (string.IsNullOrEmpty(name)) throw new Exception("signal " + from + " -> " + to + " needs a name");
            var a = ResolvePin(comps, from, "Output");
            var b = ResolvePin(comps, to, "Input");
            if (Q.F(a, "Domain").ToString() != Q.F(b, "Domain").ToString())
                throw new Exception("signal " + from + " -> " + to + ": pin types differ (" + Q.F(a, "Domain") + " vs " + Q.F(b, "Domain") + ")");
            if (Q.L(Q.Get(b, "Wires")).Count > 0) throw new Exception("signal " + from + " -> " + to + ": the input already has a wire");
            object owner;
            if (signalNames.TryGetValue(name, out owner) && owner != a)
                throw new Exception("signal name '" + name + "' is already used by another output pin (" + from + ")");
            foreach (var pin in new[] { a, b })
            {
                var current = Convert.ToString(Q.Get(pin, "Label"));
                if (!string.IsNullOrEmpty(current) && current != name)
                    throw new Exception((pin == a ? from : to) + " already carries the signal name '" + current + "', not '" + name + "'");
                Q.Set(pin, "Label", name);
            }
            signalNames[name] = a;
            signalLinks++;
        }

        // schematic notes and group boxes
        int graphics = 0;
        foreach (JObject gs in (spec["graphics"] as JArray ?? new JArray()))
        {
            var pageName = (string)gs["page"] ?? pageNames[0];
            if (!pages.ContainsKey(pageName)) throw new Exception("graphics: no page '" + pageName + "'");
            Q.Call(Q.L(Q.Get(pages[pageName], "Layers"))[0], "AddChild", SchematicGraphic(gs));
            graphics++;
        }

        // snapshot banks and their Snapshot Controllers
        var banks = new JArray();
        foreach (JObject bs in (spec["snapshot_banks"] as JArray ?? new JArray()))
            banks.Add(BuildBank(doc, bs, comps, compPage, pages, pageNames[0]));

        // UCIs
        int uciElements = 0;
        foreach (JObject us in (spec["ucis"] as JArray ?? new JArray())) uciElements += BuildUci(doc, us, comps);

        EndOperation(manager);
        AttachAll(doc);
        Save(doc, path);
        return new JObject {
            { "path", path }, { "bytes", new FileInfo(path).Length }, { "designer_version", DesignerVersion() },
            { "core", Convert.ToString(Q.Get(core, "CoreModelPretty")) }, { "components", comps.Count }, { "wires", wireCount },
            { "signal_names", signalNames.Count }, { "signal_links", signalLinks }, { "graphics", graphics }, { "snapshot_banks", banks },
            { "uci_elements", uciElements }, { "values", valuesOut }, { "warnings", warnings } };
    }

    // {"type": "text"|"group", x, y, w, h, text, fill, text_color, stroke_color, font_size, align, valign, bold, z}
    static object SchematicGraphic(JObject gs)
    {
        var kind = (string)gs["type"] ?? "text";
        object el;
        if (kind == "text")
        {
            el = Activator.CreateInstance(Q.T("QSC.QSys.Design.Graphics.TextBlock"), true);
            Q.SetEnum(el, "VerticalTextAlignment", (string)gs["valign"] ?? "Center");
            Q.Set(el, "Fill", Q.C((string)gs["fill"] ?? "#00000000"));
        }
        else if (kind == "group")
        {
            el = Activator.CreateInstance(Q.T("QSC.QSys.Design.Graphics.GroupBox"), true);
            Q.Set(el, "Fill", Q.C((string)gs["fill"] ?? "#00000000"));
            Q.Set(el, "CornerRadius", (double?)gs["corner_radius"] ?? 8);
            Q.Set(el, "StrokeWidth", (int?)gs["stroke_width"] ?? 1);
        }
        else throw new Exception("graphics type must be text or group, not " + kind);
        Q.Set(el, "Label", (string)gs["text"] ?? "");
        Place(el, gs);
        Q.Set(el, "TextColor", Q.C((string)gs["text_color"] ?? "#FF333333"));
        Q.Set(el, "StrokeColor", Q.C((string)gs["stroke_color"] ?? "#FF000000"));
        Q.Set(el, "Font", (string)gs["font"] ?? "Roboto");
        Q.Set(el, "Face", (bool?)gs["bold"] == true ? "Bold" : "Regular");
        Q.Set(el, "FontSize", (double?)gs["font_size"] ?? 12);
        Q.SetEnum(el, "HorizontalTextAlignment", (string)gs["align"] ?? (kind == "group" ? "Left" : "Center"));
        // group boxes sit behind the blocks unless told otherwise
        Q.Set(el, "ZOrder", (int?)gs["z"] ?? (kind == "group" ? -100 : 0));
        return el;
    }

    // {"name", "count", "mode", "controls": [{"component", "controls": [ids]} | {"component", "all": true}],
    //  "controller": {"name", "x", "y", "page", "label", "script_access"}}
    static JObject BuildBank(object doc, JObject bs, Dictionary<string, object> comps, Dictionary<object, string> compPage,
                             Dictionary<string, object> pages, string firstPage)
    {
        var name = (string)bs["name"];
        if (string.IsNullOrEmpty(name)) throw new Exception("every snapshot bank needs a name");
        var bank = Activator.CreateInstance(Q.T("QSC.QSys.Design.Snapshot.Bank"), true);
        ((IList)Q.Get(doc, "Snapshots")).Add(bank);
        Q.Set(bank, "Name", name);
        Q.Set(bank, "Count", (int?)bs["count"] ?? 8);
        if (bs["mode"] != null) Q.SetEnum(bank, "Mode", (string)bs["mode"]);
        var scT = Q.T("QSC.QSys.Design.Snapshot.Component");
        var ctlT = Q.T("QSC.QSys.Design.Snapshot.Control");
        int n = 0;
        foreach (JObject entry in (bs["controls"] as JArray ?? new JArray()))
        {
            object comp;
            if (!comps.TryGetValue((string)entry["component"], out comp)) throw new Exception("snapshot bank " + name + ": no component " + entry["component"]);
            var sc = Activator.CreateInstance(scT, true);
            Q.Set(sc, "DesignComponent", comp);
            var all = (bool?)entry["all"] == true;
            Q.Set(sc, "IsInclusive", all);
            ((IList)Q.Get(bank, "Components")).Add(sc);
            foreach (var idTok in (entry["controls"] as JArray ?? new JArray()))
            {
                var id = (string)idTok;
                var d = Descriptor(comp, id);
                if (d == null) throw new Exception("snapshot bank " + name + ": " + entry["component"] + " has no control " + id);
                var ctl = Activator.CreateInstance(ctlT, true);
                Q.Set(ctl, "CodeName", id);
                Q.Set(ctl, "PrettyName", Convert.ToString(Q.TryF(d, "PrettyName")));
                ((IList)Q.Get(sc, "Controls")).Add(ctl);
                n++;
            }
        }
        var res = new JObject { { "name", name }, { "count", (int)Q.Get(bank, "Count") }, { "controls", n }, { "file", Convert.ToString(Q.Get(bank, "FileName")) } };
        var cs = bs["controller"] as JObject;
        if (cs != null)
        {
            var c = Q.Call(Q.Get(bank, "Creator"), "GetComponent");
            var pageName = (string)cs["page"] ?? firstPage;
            if (!pages.ContainsKey(pageName)) throw new Exception("snapshot bank " + name + ": no page '" + pageName + "'");
            Q.Set(c, "Position", new System.Windows.Point((double?)cs["x"] ?? 0, (double?)cs["y"] ?? 0));
            Q.Call(Q.L(Q.Get(pages[pageName], "Layers"))[0], "AddChild", c);
            var code = (string)cs["name"] ?? name;
            if (comps.ContainsKey(code)) throw new Exception("duplicate component name " + code);
            Q.Set(c, "CodeName", code);
            if (cs["label"] != null) Q.Set(c, "UserLabel", (string)cs["label"]);
            SetAccess(c, (string)cs["script_access"] ?? "None");
            comps[code] = c;
            compPage[c] = pageName;
            res["controller"] = code;
        }
        return res;
    }

    // "NAME.out.3", "NAME.in.2", "NAME.in.Reference" (pin name, case-insensitive)
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
        var byName = pins.Where(p => string.Equals(Q.PinName(p), pinRef, StringComparison.OrdinalIgnoreCase)).ToList();
        if (byName.Count == 0)   // control pins can also be named by their control id, e.g. "camera_status"
            byName = pins.Where(p => string.Equals(Convert.ToString(Q.F(p, "CodeName")), pinRef, StringComparison.OrdinalIgnoreCase)).ToList();
        if (byName.Count != 1)
            throw new Exception(name + " has no single " + direction.ToLower() + " pin named '" + pinRef + "'; pins: " + string.Join(", ", pins.Select(Q.PinName)));
        return byName[0];
    }

    // ---- stored control values --------------------------------------------------------
    // The engine applies a stored control's normalised Position (0..1) when a design starts, not its
    // Value. Positions come from: an explicit "position", the calibration cache (measured on the
    // engine), or a rule. Rules are exact for booleans/integers and for the tapers measured so far;
    // anything else is reported as "assumed" so calibrate_values can confirm it.
    static JObject StoreValue(object comp, string controlId, JToken spec, JArray cache)
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
    static int BuildUci(object doc, JObject us, Dictionary<string, object> comps)
    {
        var panel = Activator.CreateInstance(Q.T("QSC.QSys.Design.WebPanels.Panel"), true);
        Q.Set(panel, "Title", (string)us["name"]);
        ((IList)Q.Get(doc, "WebPanels")).Add(panel);
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
            foreach (JObject es in (ps["elements"] as JArray ?? new JArray()))
            {
                var kind = (string)es["type"] ?? "control";
                object el;
                if (kind == "text") el = UciText(layer, es);
                else if (kind == "control") el = UciControl(layer, es, comps);
                else throw new Exception("UCI element type must be control or text, not " + kind);
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
        var usb = new JArray();
        foreach (var slot in Q.L(Q.TryGet(Q.TryGet(core, "UsbBridge") ?? new object(), "USBSlots")))
            usb.Add(new JObject { { "name", Convert.ToString(Q.Get(slot, "Name")) }, { "type", Convert.ToString(Q.Get(slot, "UsbType")) },
                { "inputs", Convert.ToString(Q.Get(slot, "InputCount")) }, { "outputs", Convert.ToString(Q.Get(slot, "OutputCount")) } });
        res["usb_slots"] = usb;
        res["inventory"] = new JArray(Q.L(Q.Get(doc, "Inventory")).Select(i => new JObject { { "type", i.GetType().Name }, { "name", Convert.ToString(Q.TryGet(i, "Name")) } }));

        var wires = new List<object>(); var comps = new List<object>(); var pageOf = new Dictionary<object, string>();
        var pagesOut = new JArray();
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
        {
            var kids = Q.L(Q.Get(page, "AllChildren"));
            var title = Convert.ToString(Q.Get(page, "Title"));
            pagesOut.Add(title);
            foreach (var k in kids)
            {
                if (IsComponent(k)) { comps.Add(k); pageOf[k] = title; }
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
            var csize = Q.TryGet(c, "Size");
            if (csize is System.Windows.Size) { o["w"] = ((System.Windows.Size)csize).Width; o["h"] = ((System.Windows.Size)csize).Height; }
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
        // pins joined by signal name only (no wire): an input named like an output is fed by it
        var outputsByName = new Dictionary<string, object>();
        foreach (var c in comps)
            foreach (var p in Q.Pins(c, "Output"))
            {
                var n = Convert.ToString(Q.Get(p, "Label"));
                if (!string.IsNullOrEmpty(n) && !outputsByName.ContainsKey(n)) outputsByName[n] = p;
            }
        var links = new JArray();
        foreach (var c in comps)
            foreach (var p in Q.Pins(c, "Input"))
            {
                var n = Convert.ToString(Q.Get(p, "Label"));
                object src;
                if (string.IsNullOrEmpty(n) || Q.L(Q.Get(p, "Wires")).Count > 0 || !outputsByName.TryGetValue(n, out src)) continue;
                links.Add(new JArray(Ref(src), Ref(p), n));
                ((JArray)res["wires"]).Add(new JArray(Ref(src), Ref(p)));
            }
        res["signal_links"] = links;
        var byId = comps.ToDictionary(c => Convert.ToString(Q.Get(c, "Id")), c => c);
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
                    var label = Convert.ToString(Q.TryGet(el, "Label"));
                    if (!string.IsNullOrEmpty(label)) eo["label"] = label;
                    if (el.GetType().Name == "ControlStyle")
                    {
                        object comp;
                        eo["component"] = byId.TryGetValue(Convert.ToString(Q.Get(el, "Id")), out comp) ? NameOf(comp) : null;
                        eo["control"] = Convert.ToString(Q.Get(el, "Name"));
                        eo["style"] = Convert.ToString(Q.TryGet(el, "VisualStyle"));
                    }
                    els.Add(eo);
                }
                pagesJ.Add(new JObject { { "name", Convert.ToString(Q.Get(pg, "Title")) }, { "elements", els } });
            }
            ucis.Add(new JObject { { "name", Convert.ToString(Q.Get(panel, "Title")) }, { "pages", pagesJ } });
        }
        res["ucis"] = ucis;

        var graphics = new JArray();
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
            foreach (var g in Q.L(Q.Get(page, "AllChildren")).Where(k => k.GetType().Name == "TextBlock" || k.GetType().Name == "GroupBox"))
            {
                var pos = (System.Windows.Point)Q.Get(g, "Position"); var size = (System.Windows.Size)Q.Get(g, "Size");
                graphics.Add(new JObject { { "type", g.GetType().Name == "GroupBox" ? "group" : "text" }, { "page", Convert.ToString(Q.Get(page, "Title")) },
                    { "x", pos.X }, { "y", pos.Y }, { "w", size.Width }, { "h", size.Height }, { "text", Convert.ToString(Q.Get(g, "Label")) } });
            }
        res["graphics"] = graphics;

        var banks = new JArray();
        foreach (var bank in Q.L(Q.Get(doc, "Snapshots")).Where(b => b.GetType().Name == "Bank"))
        {
            var ctl = new JArray();
            foreach (var sc in Q.L(Q.Get(bank, "Components")))
            {
                var dc = Q.Get(sc, "DesignComponent");
                ctl.Add(new JObject { { "component", dc == null ? null : NameOf(dc) }, { "all", (bool)Q.Get(sc, "IsInclusive") },
                    { "controls", new JArray(Q.L(Q.Get(sc, "Controls")).Select(x => Convert.ToString(Q.Get(x, "CodeName")))) } });
            }
            var file = Convert.ToString(Q.Get(bank, "FileName"));
            var stored = Q.L(Q.Get(doc, "CoreFiles")).Select(f => (string)Q.F(f, "FileName"))
                .Where(f => f.StartsWith("controlset." + file + ".snapshot.")).Select(f => int.Parse(f.Substring(f.LastIndexOf('.') + 1))).OrderBy(i => i);
            var placed = Q.Get(bank, "Component");
            banks.Add(new JObject { { "name", Convert.ToString(Q.Get(bank, "Name")) }, { "count", (int)Q.Get(bank, "Count") },
                { "mode", Q.Get(bank, "Mode").ToString() }, { "controller", placed == null ? null : NameOf(placed) },
                { "controls", ctl }, { "saved_snapshots", new JArray(stored) } });
        }
        res["snapshot_banks"] = banks;
        return res;
    }

    // copy_snapshots: {from, path, out_path?} - copies the saved contents of each snapshot bank (the
    // engine's controlset.<bank>.snapshot.N files) from one design into another with banks of the
    // same names, e.g. from a test copy where the snapshots were saved in emulation.
    static JObject CopySnapshots(JObject req)
    {
        var src = Q.Load(Path.GetFullPath((string)req["from"]));
        var path = Path.GetFullPath((string)req["path"]);
        var outPath = Path.GetFullPath((string)req["out_path"] ?? path);
        var doc = Q.Load(path);
        Func<object, Dictionary<string, string>> files = d => Q.L(Q.Get(d, "Snapshots")).Where(b => b.GetType().Name == "Bank")
            .ToDictionary(b => Convert.ToString(Q.Get(b, "Name")), b => Convert.ToString(Q.Get(b, "FileName")));
        var srcBanks = files(src); var dstBanks = files(doc);
        var srcFiles = Q.L(Q.Get(src, "CoreFiles"));
        var dstList = (IList)Q.Get(doc, "CoreFiles");
        var only = req["banks"] is JArray ? ((JArray)req["banks"]).Select(x => (string)x).ToList() : null;
        var copied = new JObject();
        foreach (var kv in dstBanks)
        {
            if (only != null && !only.Contains(kv.Key)) continue;
            string srcFile;
            if (!srcBanks.TryGetValue(kv.Key, out srcFile)) continue;
            var prefix = "controlset." + srcFile + ".snapshot.";
            var mine = "controlset." + kv.Value + ".snapshot.";
            foreach (var old in dstList.Cast<object>().Where(f => ((string)Q.F(f, "FileName")).StartsWith(mine)).ToList()) dstList.Remove(old);
            var numbers = new JArray();
            foreach (var f in srcFiles.Where(f => ((string)Q.F(f, "FileName")).StartsWith(prefix)))
            {
                var name = (string)Q.F(f, "FileName");
                var cf = Activator.CreateInstance(f.GetType());
                f.GetType().GetField("FileName").SetValue(cf, mine + name.Substring(prefix.Length));
                f.GetType().GetField("Contents").SetValue(cf, ((byte[])Q.F(f, "Contents")).Clone());
                dstList.Add(cf);
                numbers.Add(int.Parse(name.Substring(prefix.Length)));
            }
            copied[kv.Key] = new JArray(numbers.OrderBy(x => (int)x));
        }
        Save(doc, outPath);
        return new JObject { { "path", outPath }, { "copied", copied } };
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

    // copy: {path, out_path, script_access?} - a copy, optionally with every block's Script Access changed
    static JObject Copy(JObject req)
    {
        var path = Path.GetFullPath((string)req["path"]);
        var outPath = Path.GetFullPath((string)req["out_path"]);
        if (File.Exists(outPath) && (bool?)req["overwrite"] != true) throw new Exception(outPath + " exists");
        var doc = Q.Load(path);
        int changed = 0;
        if (req["script_access"] != null)
            foreach (var c in AllComponents(doc)) { SetAccess(c, (string)req["script_access"]); changed++; }
        Save(doc, outPath);
        return new JObject { { "path", outPath }, { "script_access_changed", changed } };
    }
}
