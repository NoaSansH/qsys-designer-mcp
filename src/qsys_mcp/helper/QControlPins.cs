// Control pins: the pins a component's controls can show (Properties > Control Pins in Designer), so
// that control wires can join controls. Designer records a shown control pin only as a Pin in the
// component's pin collection: UserCreated true, Domain control (2), Direction Input or Output (an input
// and output pin is two Pins), CodeName = the control's code name, TypeIndex 0xFFFFFFFF, ControlId null.
// Nothing else on the component records it.
//
// A pin is turned on through a Designer method that does it, when one is found. Otherwise the Pin is
// made with its own constructor and added through the component. Member names aren't confirmed for
// every Designer version, so each lookup tries the likely names and, when none fits, fails with the
// members that do exist.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

static class QControlPins
{
    const int ControlDomain = 2;              // Pin.Domain of a control pin in saved files (1 audio, 8 video)
    const int InputDirection = 1, OutputDirection = 2;
    const uint UserTypeIndex = 0xFFFFFFFF;    // Pin.TypeIndex of a pin the user added
    const BindingFlags Declared = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
    const BindingFlags DeclaredInstance = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly;

    // Designer methods that may turn a control pin on, tried first and in this order: on the component
    // (and its base classes), or static on ComponentHelper or Pin taking the component. After these, any
    // other method named like Add/Create/Show...(Control|User)...Pin whose parameters can all be filled in.
    static readonly string[] MethodNames = { "AddControlPin", "CreateControlPin", "ShowControlPin", "EnableControlPin",
        "AddUserPin", "CreateUserPin", "AddUserControlPin", "CreateUserControlPin", "SetControlPin", "ToggleControlPin",
        "SetControlPinStyle", "SetPinStyle", "UpdateControlPin" };
    static readonly string[] Verbs = { "Add", "Create", "Show", "Enable", "Set", "Toggle", "Make", "New", "Insert", "Update", "Turn", "Expose" };
    static readonly string[] Avoid = { "Remove", "Delete", "Hide", "Disable", "Clear", "Get", "Find", "Can", "Is", "Has", "Wire", "Wires",
        "Color", "Colour", "Label", "Name", "Position", "Graphic", "Signal", "Draw", "Render", "Layout", "Select", "Copy", "Paste",
        "Validate", "Check", "Sort", "Order", "Size", "Move", "Container", "Bus", "Jumper", "Changed", "Event" };
    // Component methods that add an existing Pin (the constructor route uses one when it exists)
    static readonly string[] AddPinNames = { "AddPin", "AddUserPin", "AddControlPin", "InsertPin" };

    class Want   // the pin(s) to show for one control
    {
        public object Comp, Descriptor;
        public string Control, Pretty;
        public bool In, Out;
    }

    static Type PinType { get { return Q.T("QSC.QSys.Design.Pin"); } }
    static Type DirectionType { get { return MemberType(PinType, "Direction"); } }
    static Type DomainType { get { return MemberType(PinType, "Domain"); } }
    static object DirValue(bool isIn) { return Enum.ToObject(DirectionType, isIn ? InputDirection : OutputDirection); }
    static object DomainValue() { return Enum.ToObject(DomainType, ControlDomain); }

    // ---- reading pins -------------------------------------------------------------------
    // a pin added for a control rather than one of the block's own: saved files mark it both ways
    public static bool IsUserPin(object pin)
    {
        if (true.Equals(Member(pin, "UserCreated"))) return true;
        var ti = Member(pin, "TypeIndex");
        try { return ti != null && unchecked((uint)Convert.ToInt64(ti)) == UserTypeIndex; } catch { return false; }
    }

    public static bool IsControl(object pin)
    {
        var d = Member(pin, "Domain");
        if (d == null) return false;
        if (string.Equals(d.ToString(), "Control", StringComparison.OrdinalIgnoreCase)) return true;
        try { return Convert.ToInt32(d) == ControlDomain; } catch { return false; }
    }

    // "audio", "control", "video"...: Designer's name for the pin's domain, lowercased
    public static string DomainName(object pin)
    {
        if (IsControl(pin)) return "control";
        var d = Member(pin, "Domain");
        return d == null ? "unknown" : d.ToString().ToLowerInvariant();
    }

    // the control a control pin shows: the code name of a pin the user added, else the block's own binding
    public static string ControlOf(object pin)
    {
        if (!IsControl(pin)) return null;
        var s = Convert.ToString(Member(pin, IsUserPin(pin) ? "CodeName" : "ControlId"));
        return string.IsNullOrEmpty(s) ? null : s;
    }

    // read and probe: a pin's domain, whether it was added for a control, and the control a control pin shows
    public static void Describe(JObject o, object pin)
    {
        o["domain"] = DomainName(pin);
        if (IsUserPin(pin)) o["user_created"] = true;
        var control = ControlOf(pin);
        if (control != null) o["control"] = control;
    }

    // Pins answering to a name: by pin name (case-insensitive), else a control pin by its control code
    // name. Two pins with one name are told apart by their control.
    public static List<object> Match(List<object> pins, string pinRef)
    {
        var byName = pins.Where(p => Same(Q.PinName(p), pinRef)).ToList();
        if (byName.Count > 1)
        {
            var exact = byName.Where(p => ControlOf(p) == pinRef).ToList();
            if (exact.Count == 1) return exact;
        }
        if (byName.Count > 0) return byName;
        return pins.Where(p => IsControl(p) && (Same(ControlOf(p), pinRef) || Same(Convert.ToString(Member(p, "CodeName")), pinRef))).ToList();
    }

    // "Mute [mute]" in error messages
    public static string Label(object pin)
    {
        var name = Q.PinName(pin); var control = ControlOf(pin);
        return control == null || control == name ? name : name + " [" + control + "]";
    }

    // why a name didn't resolve, when a control pin explains it
    public static string Hint(object comp, string pinRef, string direction)
    {
        var other = direction == "Input" ? "Output" : "Input";
        if (Match(Q.Pins(comp, other), pinRef).Count > 0)
            return "; '" + pinRef + "' is an " + other.ToLowerInvariant() + " pin on this block";
        try
        {
            if (Q.Descriptors(comp).Any(d => Q.Cid(d) == pinRef))
                return "; " + pinRef + " is a control with no " + direction.ToLowerInvariant() + " pin: add it to the block's control_pins";
        }
        catch { }
        return "";
    }

    // ---- what a control offers ----------------------------------------------------------------
    // The pin directions Designer offers for a control: "in", "out", "both" or "none". From the control
    // descriptor's own pin members when it has them (a pin style such as Input/Output/Both, a user-pin
    // flag); otherwise output for read-only controls and indicators, both for the rest.
    static string Offered(object d, out string source)
    {
        string style = null, styleMember = null;
        object user = null, inFlag = null, outFlag = null;
        foreach (var kv in PinMembers(d))
        {
            if (kv.Value is bool)
            {
                if (kv.Key.IndexOf("user", StringComparison.OrdinalIgnoreCase) >= 0) user = kv.Value;
                else if (kv.Key.IndexOf("input", StringComparison.OrdinalIgnoreCase) >= 0) inFlag = kv.Value;
                else if (kv.Key.IndexOf("output", StringComparison.OrdinalIgnoreCase) >= 0) outFlag = kv.Value;
                continue;
            }
            var s = StyleDir(kv.Value);
            if (s != null && style == null) { style = s; styleMember = kv.Key + "=" + kv.Value; }
        }
        if (style != null && style != "none") { source = "descriptor " + styleMember; return style; }
        if (inFlag != null || outFlag != null)
        {
            bool i = true.Equals(inFlag), o = true.Equals(outFlag);
            source = "descriptor input/output pin flags";
            return i && o ? "both" : i ? "in" : o ? "out" : "none";
        }
        if (style == "none" && false.Equals(user)) { source = "descriptor " + styleMember + ", no user pin"; return "none"; }
        source = "guess";
        return ReadOnly(d) || Indicator(d) ? "out" : "both";
    }

    // a pin style value (an enum, or a string that is only a style name) as "in", "out", "both" or "none"
    static string StyleDir(object v)
    {
        if (!(v is Enum) && !(v is string && Regex.IsMatch((string)v, "^(None|In|Input|Out|Output|Both)$", RegexOptions.IgnoreCase))) return null;
        var s = v.ToString();
        if (s.IndexOf("Both", StringComparison.OrdinalIgnoreCase) >= 0) return "both";
        bool i = Regex.IsMatch(s, @"\bIn(put)?\b", RegexOptions.IgnoreCase), o = Regex.IsMatch(s, @"\bOut(put)?\b", RegexOptions.IgnoreCase);
        if (i && o) return "both";   // flags: "Input, Output"
        if (i) return "in";
        if (o) return "out";
        return string.Equals(s, "None", StringComparison.OrdinalIgnoreCase) ? "none" : null;
    }

    static bool ReadOnly(object d)
    {
        foreach (var n in new[] { "IsReadonly", "IsReadOnly", "ReadOnly", "Readonly" })
            if (true.Equals(Member(d, n))) return true;
        return false;
    }

    static bool Indicator(object d)
    {
        var s = Convert.ToString(Member(d, "ControlType")) + " " + Convert.ToString(Member(d, "VisualStyle"));
        return Regex.IsMatch(s, @"\b(Indicator|Meter|Led)\b", RegexOptions.IgnoreCase);
    }

    // a descriptor's fields and properties whose names mention pins, with their values
    static List<KeyValuePair<string, object>> PinMembers(object d)
    {
        var list = new List<KeyValuePair<string, object>>();
        foreach (var t in Up(d.GetType()))
        {
            foreach (var f in t.GetFields(DeclaredInstance))
                if (IsPinName(f.Name)) Push(list, f.Name, f.GetValue(d));
            foreach (var p in t.GetProperties(DeclaredInstance))
            {
                if (!IsPinName(p.Name) || p.GetIndexParameters().Length > 0) continue;
                object v;
                try { v = p.GetValue(d, null); } catch { continue; }
                Push(list, p.Name, v);
            }
        }
        return list;
    }

    static bool IsPinName(string n) { return n.Contains("Pin") || Regex.IsMatch(n, "^_?pin"); }

    static void Push(List<KeyValuePair<string, object>> list, string name, object v)
    {
        name = name.Replace("k__BackingField", "").Trim('<', '>', '_');   // a property and its field are one member
        if (!list.Any(kv => kv.Key == name)) list.Add(new KeyValuePair<string, object>(name, v));
    }

    // probe: each control's offered pin directions
    public static void DescribeControl(JObject o, object descriptor)
    {
        string source;
        o["pin"] = Offered(descriptor, out source);
    }

    // probe (classes): where those directions come from; a class spec's "control_pins" are also turned on
    // there (nothing is saved), so a pin can be tried before a build
    public static void Probe(JObject o, object comp, JObject spec)
    {
        var members = new JObject();
        foreach (var d in Q.Descriptors(comp))
            foreach (var kv in PinMembers(d))
            {
                var seen = members[kv.Key] as JArray;
                if (seen == null) { seen = new JArray(); members[kv.Key] = seen; }
                var v = Convert.ToString(kv.Value);
                if (seen.Count < 8 && !seen.Any(x => (string)x == v)) seen.Add(v);
            }
        if (members.Count > 0)
        {
            o["control_pins_from"] = "control descriptors (" + string.Join(", ", members.Properties().Select(p => p.Name)) + ")";
            o["control_pin_members"] = members;
        }
        else o["control_pins_from"] = "guess: the control descriptors have no pin members, so read-only controls and indicators show \"out\" and the rest \"both\"";
        if (spec == null || spec["control_pins"] == null) return;
        object manager = null;   // inside an operation, as in a build
        try { manager = Q.T("QSC.QSys.App").GetField("Manager", Q.All).GetValue(null); Q.Call(manager, "StartOperation"); }
        catch { manager = null; }
        try { o["control_pins"] = Add(comp, spec["control_pins"]); }
        catch (Exception e) { o["control_pins_error"] = (e.InnerException ?? e).Message; }
        finally
        {
            if (manager != null)
            {
                try { manager.GetType().GetMethods(Q.All).First(m => m.Name == "EndOperation" && m.GetParameters().Length == 0).Invoke(manager, null); }
                catch { }
            }
        }
    }

    // ---- turning pins on -------------------------------------------------------------------------
    // "control_pins": ["connected", {"control": "mute", "dir": "in" | "out" | "both"}]: one result per control
    public static JArray Add(object comp, JToken specs)
    {
        if (!(specs is JArray)) throw new Exception(Who(comp) + ": control_pins must be a list");
        var results = new JArray();
        foreach (var s in specs)
        {
            if (s.Type == JTokenType.String) results.Add(Show(comp, (string)s, null));
            else if (s.Type == JTokenType.Object) results.Add(Show(comp, (string)s["control"], (string)s["dir"]));
            else throw new Exception(Who(comp) + ": each control_pins entry is a control code name or {\"control\": ..., \"dir\": ...}");
        }
        return results;
    }

    static JObject Show(object comp, string control, string dir)
    {
        var who = Who(comp);
        if (string.IsNullOrEmpty(control)) throw new Exception(who + ": a control_pins entry has no control");
        var d = Q.Descriptors(comp).FirstOrDefault(x => Q.Cid(x) == control);
        if (d == null) throw new Exception(who + " has no control '" + control + "' (probe_components lists its controls)");
        string source;
        var offered = Offered(d, out source);
        if (dir == null)
        {
            if (offered == "none") throw new Exception(who + "." + control + " offers no control pin (" + source + ")");
            dir = offered;
        }
        dir = dir.ToLowerInvariant();
        if (dir != "in" && dir != "out" && dir != "both") throw new Exception(who + "." + control + ": dir must be in, out or both, not '" + dir + "'");
        if (dir != "out" && ReadOnly(d)) throw new Exception(who + "." + control + " is read-only, so it can only have an output pin (dir \"out\")");
        if (source != "guess" && offered != "both" && dir != offered)
            throw new Exception(who + "." + control + " offers " + (offered == "none" ? "no pin" : "only an " + (offered == "in" ? "input" : "output") + " pin") +
                " (" + source + "), not dir '" + dir + "'");
        var w = new Want();
        w.Comp = comp; w.Descriptor = d; w.Control = control;
        var pretty = Convert.ToString(Q.TryF(d, "PrettyName"));
        w.Pretty = string.IsNullOrEmpty(pretty) ? control : pretty;
        w.In = dir != "out"; w.Out = dir != "in";
        return new JObject { { "component", who }, { "control", control }, { "dir", dir }, { "route", TurnOn(w) } };
    }

    // shows the wanted pins; returns how it was done
    static string TurnOn(Want w)
    {
        var notes = new List<string>();
        if (w.In && Find(w, true) != null) notes.Add("input pin already shown");
        if (w.Out && Find(w, false) != null) notes.Add("output pin already shown");
        if (Missing(w).Count == 0) return string.Join("; ", notes);
        // 1. a Designer method that turns the pin on
        var tried = new List<string>();
        foreach (var m in Methods(w.Comp))
        {
            int before = Missing(w).Count;
            bool done = TryMethod(m, w, tried);
            int after = Missing(w).Count;
            if (after < before) notes.Add("Designer method " + Sig(m) + (after > 0 ? " (some pins only)" : ""));
            if (done) break;
        }
        // 2. otherwise the Pin's own constructor, added through the component
        var missing = Missing(w);
        if (missing.Count > 0 && tried.Count > 0) notes.Add("Designer methods tried: " + string.Join("; ", tried));
        foreach (var isIn in missing)
            notes.Add("Pin constructor (" + (isIn ? "input" : "output") + "): " + Construct(w, isIn));
        foreach (var isIn in new[] { true, false })
        {
            if (isIn ? !w.In : !w.Out) continue;
            var pin = Find(w, isIn);
            if (pin == null)
                throw new Exception(Who(w.Comp) + "." + w.Control + ": the " + (isIn ? "input" : "output") + " pin did not appear (" + string.Join("; ", notes) + ")");
            var odd = Odd(pin, w);
            if (odd.Count > 0) notes.Add((isIn ? "input" : "output") + " pin differs from a saved one: " + string.Join(", ", odd));
        }
        return string.Join("; ", notes);
    }

    static List<bool> Missing(Want w)
    {
        var list = new List<bool>();
        if (w.In && Find(w, true) == null) list.Add(true);
        if (w.Out && Find(w, false) == null) list.Add(false);
        return list;
    }

    static object Find(Want w, bool isIn)
    {
        var dir = DirValue(isIn).ToString();
        return Q.L(Q.Get(w.Comp, "Pins")).FirstOrDefault(p => Convert.ToString(Member(p, "Direction")) == dir && IsControl(p)
            && (Same(ControlOf(p), w.Control) || Same(Convert.ToString(Member(p, "CodeName")), w.Control)));
    }

    // what makes a pin unlike the control pins Designer saves
    static List<string> Odd(object pin, Want w)
    {
        var odd = new List<string>();
        if (!true.Equals(Member(pin, "UserCreated"))) odd.Add("UserCreated=" + Member(pin, "UserCreated"));
        var ti = Member(pin, "TypeIndex");
        if (ti != null && unchecked((uint)Convert.ToInt64(ti)) != UserTypeIndex) odd.Add("TypeIndex=" + ti);
        if (Convert.ToString(Member(pin, "CodeName")) != w.Control) odd.Add("CodeName=" + Member(pin, "CodeName"));
        if (Member(pin, "ControlId") != null) odd.Add("ControlId='" + Member(pin, "ControlId") + "'");
        if (!ReferenceEquals(Member(pin, "Provider"), w.Comp)) odd.Add("Provider is not the component");
        return odd;
    }

    // candidate Designer methods: the known names first, then others named like a control pin "add"
    static List<MethodInfo> Methods(object comp)
    {
        var all = PinMethods(comp);
        var list = new List<MethodInfo>();
        foreach (var name in MethodNames) list.AddRange(all.Where(m => m.Name == name));
        foreach (var m in all)
        {
            if (list.Contains(m)) continue;
            var words = Regex.Matches(m.Name, "[A-Z][a-z0-9]*").Cast<Match>().Select(x => x.Value).ToList();
            if (words.Count == 0 || !Verbs.Contains(words[0])) continue;
            if (!words.Contains("Pin") && !words.Contains("Pins")) continue;
            if (!words.Contains("Control") && !words.Contains("User")) continue;
            if (words.Any(x => Avoid.Contains(x))) continue;
            list.Add(m);
        }
        return list;
    }

    // methods whose names mention pins: the component's (and its bases'), and static ones on ComponentHelper and Pin
    static List<MethodInfo> PinMethods(object comp)
    {
        var list = new List<MethodInfo>();
        foreach (var t in Up(comp.GetType())) list.AddRange(t.GetMethods(Declared));
        list.AddRange(Q.Helper.GetMethods(Declared).Where(m => m.IsStatic));
        list.AddRange(PinType.GetMethods(Declared).Where(m => m.IsStatic));
        return list.Where(m => m.Name.Contains("Pin") && !m.Name.Contains("<") && !m.IsGenericMethodDefinition && !m.IsAbstract)
            .Distinct().ToList();
    }

    // tries one method for the pins still missing; true when they are all there afterwards
    static bool TryMethod(MethodInfo m, Want w, List<string> tried)
    {
        var ps = m.GetParameters();
        foreach (var isIn in Missing(w))
        {
            if (Find(w, isIn) != null) continue;   // an earlier call made it
            bool both;
            var args = BindMethod(m, ps, w, isIn, out both);
            if (args == null) return false;
            object result;
            try { result = m.Invoke(m.IsStatic ? null : w.Comp, args); }
            catch (Exception e) { tried.Add(Sig(m) + ": " + (e.InnerException ?? e).Message); return false; }
            if (result != null && PinType.IsInstanceOfType(result) && !InPins(w.Comp, result))
            {
                try { AddToComponent(w.Comp, result); }
                catch (Exception e) { tried.Add(Sig(m) + ": returned a pin that could not be added (" + e.Message + ")"); return false; }
            }
            if (both) break;   // a pin style or in/out flags: one call shows every wanted pin
        }
        if (Missing(w).Count == 0) return true;
        tried.Add(Sig(m) + ": ran, but the pin did not appear");
        return false;
    }

    // Arguments for a method, or null when one can't be filled in or the method doesn't take the control
    // and a direction (and, when static, the component). both: the direction is a pin style or in/out flags.
    static object[] BindMethod(MethodInfo m, ParameterInfo[] ps, Want w, bool isIn, out bool both)
    {
        both = false;
        bool control = false, direction = false, comp = !m.IsStatic;
        int inFlag = -1, outFlag = -1;
        for (int i = 0; i < ps.Length; i++)
        {
            var n = (ps[i].Name ?? "").ToLowerInvariant();
            if (ps[i].ParameterType != typeof(bool)) continue;
            if (n.Contains("input")) inFlag = i;
            else if (n.Contains("output")) outFlag = i;
        }
        var args = new object[ps.Length];
        for (int i = 0; i < ps.Length; i++)
        {
            var p = ps[i]; var t = p.ParameterType; var n = (p.Name ?? "").ToLowerInvariant();
            if (t.IsByRef || t.IsPointer) return null;
            if (t != typeof(object) && t.IsInstanceOfType(w.Descriptor)) { args[i] = w.Descriptor; control = true; }
            else if (t != typeof(object) && t.IsInstanceOfType(w.Comp)) { args[i] = w.Comp; comp = true; }
            else if (t == typeof(string))
            {
                if (n.Contains("label") || n.Contains("signal")) args[i] = p.IsOptional ? p.DefaultValue : null;   // no signal name: one would join pins by name
                else if (n.Contains("pretty") || n.Contains("display")) args[i] = w.Pretty;
                else { args[i] = w.Control; control = true; }
            }
            else if (t == DirectionType) { args[i] = DirValue(isIn); direction = true; }
            else if (t == DomainType) args[i] = DomainValue();
            else if (t.IsEnum)
            {
                // another direction or pin style enum: Input/Output, and Both when it has one (then one call does both)
                var names = Enum.GetNames(t);
                string bothName = Pick(names, "Both", "InputOutput", "InOut"), inName = Pick(names, "Input", "In"), outName = Pick(names, "Output", "Out");
                if (inName == null || outName == null) return null;
                if (bothName != null) { args[i] = Enum.Parse(t, w.In && w.Out ? bothName : w.In ? inName : outName); both = true; }
                else args[i] = Enum.Parse(t, isIn ? inName : outName);
                direction = true;
            }
            else if (t == typeof(bool))
            {
                if (i == inFlag) { args[i] = outFlag >= 0 ? w.In : isIn; direction = true; both = outFlag >= 0; }
                else if (i == outFlag) { args[i] = inFlag >= 0 ? w.Out : !isIn; direction = true; both = inFlag >= 0; }
                else if (Regex.IsMatch(n, "^(is)?(enable|enabled|show|shown|visible|on|add|create|value|state|checked|selected|active|user|usercreated)$")) args[i] = true;
                else return null;
            }
            else if (p.IsOptional) args[i] = p.DefaultValue;
            else return null;
        }
        return control && direction && comp ? args : null;
    }

    static string Pick(string[] names, params string[] wanted)
    {
        return names.FirstOrDefault(n => wanted.Any(x => string.Equals(n, x, StringComparison.OrdinalIgnoreCase)));
    }

    // fallback: a Pin from its own constructor (the one that takes the most), fields checked against the
    // saved form, then added through the component
    static string Construct(Want w, bool isIn)
    {
        var pinT = PinType;
        var ctors = pinT.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .OrderByDescending(c => c.GetParameters().Length).ToList();
        object pin = null; ConstructorInfo used = null;
        var failed = new List<string>();
        foreach (var c in ctors)
        {
            var args = BindCtor(c.GetParameters(), w, isIn);
            if (args == null) continue;
            try { pin = c.Invoke(args); used = c; break; }
            catch (Exception e) { failed.Add(Sig(c) + ": " + (e.InnerException ?? e).Message); }
        }
        if (pin == null)
            throw new Exception("could not show the " + (isIn ? "input" : "output") + " pin of " + Who(w.Comp) + "." + w.Control +
                ": no Designer method turned it on and no Pin constructor fits" + (failed.Count > 0 ? " (" + string.Join("; ", failed) + ")" : "") + ". " + Members(w.Comp));
        var set = new List<string>();
        Ensure(pin, "Provider", w.Comp, set);
        Ensure(pin, "Direction", DirValue(isIn), set);
        Ensure(pin, "Domain", DomainValue(), set);
        Ensure(pin, "UserCreated", true, set);
        Ensure(pin, "TypeIndex", UserTypeIndex, set);
        Ensure(pin, "CodeName", w.Control, set);
        Ensure(pin, "ControlId", null, set);
        var pretty = Convert.ToString(Member(pin, "PrettyName"));
        if (string.IsNullOrEmpty(pretty) || pretty == w.Control) Ensure(pin, "PrettyName", w.Pretty, set);
        FillCollections(pin, set);
        var how = Sig(used) + " + " + AddToComponent(w.Comp, pin);
        return set.Count > 0 ? how + " (fields set: " + string.Join(", ", set) + ")" : how;
    }

    // a collection the constructor left null (such as the pin's wires), made as Designer's are: owned by the pin
    static void FillCollections(object pin, List<string> set)
    {
        foreach (var t in Up(pin.GetType()))
            foreach (var f in t.GetFields(DeclaredInstance))
            {
                var ft = f.FieldType;
                if (!ft.IsGenericType || ft.IsAbstract || ft.IsInterface || !typeof(System.Collections.IEnumerable).IsAssignableFrom(ft) || f.GetValue(pin) != null) continue;
                var ctors = ft.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                var owned = ctors.FirstOrDefault(c => c.GetParameters().Length == 1 && c.GetParameters()[0].ParameterType.IsInstanceOfType(pin));
                var plain = ctors.FirstOrDefault(c => c.GetParameters().Length == 0);
                if (owned == null && plain == null) continue;
                try { f.SetValue(pin, owned != null ? owned.Invoke(new[] { pin }) : plain.Invoke(null)); set.Add(f.Name); }
                catch { }
            }
    }

    static object[] BindCtor(ParameterInfo[] ps, Want w, bool isIn)
    {
        var args = new object[ps.Length];
        for (int i = 0; i < ps.Length; i++)
        {
            var p = ps[i]; var t = p.ParameterType; var n = (p.Name ?? "").ToLowerInvariant();
            if (t.IsByRef || t.IsPointer) return null;
            if (t == DirectionType) args[i] = DirValue(isIn);
            else if (t == DomainType) args[i] = DomainValue();
            else if (t != typeof(object) && t.IsInstanceOfType(w.Comp)) args[i] = w.Comp;
            else if (t != typeof(object) && t.IsInstanceOfType(w.Descriptor)) args[i] = w.Descriptor;
            else if (t == typeof(string))
            {
                if (n.Contains("pretty") || n.Contains("display")) args[i] = w.Pretty;
                else if (n.Contains("controlid") || n == "id" || n.Contains("label") || n.Contains("signal")) args[i] = null;
                else if (n.Contains("code") || n.Contains("name") || n.Contains("control")) args[i] = w.Control;
                else return null;
            }
            else if (t == typeof(bool) && (n.Contains("user") || n.Contains("created"))) args[i] = true;
            else if ((t == typeof(uint) || t == typeof(int)) && n.Contains("graphic")) args[i] = As(0, t);
            else if ((t == typeof(uint) || t == typeof(int)) && n.Contains("type")) args[i] = As(UserTypeIndex, t);
            else if (p.IsOptional) args[i] = p.DefaultValue;
            else return null;
        }
        return args;
    }

    // adds a Pin through the component: its own add method when it has one, else its pin collection's Add
    static string AddToComponent(object comp, object pin)
    {
        var errors = new List<string>();
        foreach (var t in Up(comp.GetType()))
            foreach (var m in t.GetMethods(DeclaredInstance))
            {
                var ps = m.GetParameters();
                if (!AddPinNames.Contains(m.Name) || ps.Length != 1 || ps[0].ParameterType == typeof(object) || !ps[0].ParameterType.IsInstanceOfType(pin)) continue;
                try { m.Invoke(comp, new[] { pin }); }
                catch (Exception e) { errors.Add(Sig(m) + ": " + (e.InnerException ?? e).Message); continue; }
                if (InPins(comp, pin)) return t.Name + "." + m.Name;
            }
        foreach (var source in new[] { "Pins", "_Pins" })
        {
            var coll = Member(comp, source);
            if (coll == null) continue;
            var add = coll.GetType().GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
                .FirstOrDefault(m => m.Name == "Add" && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType.IsInstanceOfType(pin));
            if (add == null) continue;
            try { add.Invoke(coll, new[] { pin }); }
            catch (Exception e) { errors.Add(source + ".Add: " + (e.InnerException ?? e).Message); continue; }
            if (InPins(comp, pin)) return source + ".Add";
        }
        throw new Exception("could not add a pin to " + Who(comp) + (errors.Count > 0 ? " (" + string.Join("; ", errors) + ")" : "") + ". " + Members(comp));
    }

    static bool InPins(object comp, object pin) { return Q.L(Q.Get(comp, "Pins")).Any(p => ReferenceEquals(p, pin)); }

    // ---- reflection -----------------------------------------------------------------------------
    static IEnumerable<Type> Up(Type t)
    {
        for (; t != null && t != typeof(object); t = t.BaseType) yield return t;
    }

    // a field or property by name, on the type or a base class (private ones too)
    static object Member(object o, string name)
    {
        foreach (var t in Up(o.GetType()))
        {
            var f = t.GetField(name, DeclaredInstance);
            if (f != null) return f.GetValue(o);
            var p = Property(t, name);
            if (p == null || p.GetIndexParameters().Length > 0) continue;
            try { return p.GetValue(o, null); } catch { return null; }
        }
        return null;
    }

    static PropertyInfo Property(Type t, string name)
    {
        try { return t.GetProperty(name, DeclaredInstance); } catch (AmbiguousMatchException) { return null; }
    }

    static Type MemberType(Type type, string name)
    {
        foreach (var t in Up(type))
        {
            var f = t.GetField(name, DeclaredInstance);
            if (f != null) return f.FieldType;
            var p = Property(t, name);
            if (p != null) return p.PropertyType;
        }
        throw new Exception(type.Name + " has no member " + name + "; fields: " +
            string.Join(", ", Up(type).SelectMany(t => t.GetFields(DeclaredInstance)).Select(f => f.Name)));
    }

    // sets a field (or property) when it doesn't already hold the value; notes the ones it set
    static void Ensure(object o, string name, object want, List<string> set)
    {
        foreach (var t in Up(o.GetType()))
        {
            var f = t.GetField(name, DeclaredInstance);
            var p = f == null ? Property(t, name) : null;
            if (f == null && (p == null || !p.CanWrite || p.GetIndexParameters().Length > 0)) continue;
            var type = f != null ? f.FieldType : p.PropertyType;
            var v = As(want, type);
            var cur = f != null ? f.GetValue(o) : p.GetValue(o, null);
            if (Equals(cur, v)) return;
            if (f != null) f.SetValue(o, v); else p.SetValue(o, v, null);
            set.Add(name);
            return;
        }
    }

    static object As(object v, Type t)
    {
        if (v == null || t.IsInstanceOfType(v)) return v;
        if (t.IsEnum) return Enum.ToObject(t, v);
        if (v is uint && t == typeof(int)) return unchecked((int)(uint)v);
        if (v is int && t == typeof(uint)) return unchecked((uint)(int)v);
        return Convert.ChangeType(v, t);
    }

    static bool Same(string a, string b) { return a != null && string.Equals(a, b, StringComparison.OrdinalIgnoreCase); }

    static string Who(object comp)
    {
        var code = Convert.ToString(Q.TryGet(comp, "CodeName"));
        return string.IsNullOrEmpty(code) ? Convert.ToString(Q.TryGet(comp, "Label")) : code;
    }

    static string Sig(MethodBase m)
    {
        var name = m is ConstructorInfo ? m.DeclaringType.Name : m.DeclaringType.Name + "." + m.Name;
        return name + "(" + string.Join(", ", m.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name)) + ")";
    }

    // the members a failed lookup reports
    static string Members(object comp)
    {
        var pinT = PinType;
        return "Pin methods: " + string.Join("; ", PinMethods(comp).Select(m => (m.IsStatic ? "static " : "") + Sig(m)).Take(60)) +
            ". Pin constructors: " + string.Join("; ", pinT.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance).Select(c => Sig(c))) +
            ". Pin fields: " + string.Join(", ", Up(pinT).SelectMany(t => t.GetFields(DeclaredInstance)).Select(f => f.FieldType.Name + " " + f.Name));
    }
}
