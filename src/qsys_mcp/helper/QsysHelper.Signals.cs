// Signal Names, as Designer's "Convert to Signal Names" makes them: pins that carry the same label
// are connected by Designer, with no Wire between them. A pin's label is its Label property (stored
// as Pin._Label). Names are not case sensitive; a name has one source (an output) and any number of
// destinations (inputs). A pin can carry a name and wires at the same time.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

static partial class QsysHelper
{
    // Wires that name a signal: collected while wiring, checked and labelled once every wire is known.
    class SignalPlan
    {
        class Entry { public string Name, From, To; public object A, B; }
        readonly List<Entry> entries = new List<Entry>();
        readonly Dictionary<object, string> wired = new Dictionary<object, string>();   // input pin -> its wire's source

        // true when the wire names a signal (then no Wire is made for it); plain wires are noted for Apply
        public bool Take(JToken w, string from, string to, object a, object b)
        {
            var arr = w as JArray;
            var name = arr != null ? (arr.Count > 2 ? (string)arr[2] : null) : (string)w["signal"];
            if (name == null) { if (!wired.ContainsKey(b)) wired[b] = from; return false; }
            if (name.Trim().Length == 0) throw new Exception("wire " + from + " -> " + to + ": empty signal name");
            entries.Add(new Entry { Name = name, From = from, To = to, A = a, B = b });
            return true;
        }

        // checks the names, labels each pin once (with the name as first written) and returns how many names
        public int Apply()
        {
            var source = new Dictionary<string, Entry>(StringComparer.OrdinalIgnoreCase);   // name -> the entry that gave its source
            var named = new Dictionary<object, Entry>();                                      // pin -> the entry that labelled it
            foreach (var e in entries)
            {
                Entry s, p; string wire;
                if (source.TryGetValue(e.Name, out s) && s.A != e.A)
                    throw new Exception("signal '" + e.Name + "' has two sources, " + s.From + " and " + e.From + " (a name has one source; names are not case sensitive)");
                if (named.TryGetValue(e.A, out p) && !SameName(p.Name, e.Name))
                    throw new Exception(e.From + " already carries signal '" + p.Name + "'; an output carries one name, not also '" + e.Name + "'");
                if (wired.TryGetValue(e.B, out wire))
                    throw new Exception(e.To + " already has a wire from " + wire + "; an input takes a wire or a signal name, not both");
                if (named.TryGetValue(e.B, out p) && !SameName(p.Name, e.Name))
                    throw new Exception(e.To + " already takes signal '" + p.Name + "', not also '" + e.Name + "'");
                if (s == null) source[e.Name] = s = e;
                foreach (var pin in new[] { e.A, e.B })
                    if (!named.ContainsKey(pin)) { SetSignalName(pin, s.Name); named[pin] = e; }
            }
            return source.Count;
        }
    }

    static bool SameName(string a, string b) { return string.Equals(a, b, StringComparison.OrdinalIgnoreCase); }

    // A pin's signal name is its Label.
    static void SetSignalName(object pin, string name)
    {
        var t = pin.GetType();
        var props = t.GetProperties(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        var p = props.FirstOrDefault(x => x.Name == "Label" && x.CanWrite && x.PropertyType == typeof(string) && x.GetIndexParameters().Length == 0);
        if (p == null)
            throw new Exception("can't set signal names: pin type " + t.FullName + " has no writable string property Label; its properties: " +
                string.Join(", ", props.Select(x => x.Name + ":" + x.PropertyType.Name + (x.CanWrite ? "" : " (read-only)")).Distinct().OrderBy(x => x)));
        p.SetValue(pin, name, null);
        var stored = FieldUp(t, "_Label");
        if (stored != null && Convert.ToString(stored.GetValue(pin)) != name)
            throw new Exception("setting " + t.Name + ".Label to '" + name + "' left its stored _Label as '" + stored.GetValue(pin) + "'");
    }

    // A pin's signal name, or null. Reads the stored _Label where there is one, because the Label
    // property might fall back to the pin's own name.
    static string SignalName(object pin)
    {
        try
        {
            var f = FieldUp(pin.GetType(), "_Label");
            var s = Convert.ToString(f != null ? f.GetValue(pin) : Q.TryGet(pin, "Label"));
            if (string.IsNullOrWhiteSpace(s) || (f == null && s == Convert.ToString(Q.TryF(pin, "PrettyName")))) return null;
            return s;
        }
        catch { return null; }
    }

    // a field declared on the type or a base type (GetField alone misses a base class's private fields)
    static FieldInfo FieldUp(Type t, string name)
    {
        for (; t != null; t = t.BaseType)
        {
            var f = t.GetField(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly);
            if (f != null) return f;
        }
        return null;
    }

    // [{name, source, targets}] from the pins' labels, grouped without case (the source's spelling wins).
    // A name with more than one source (not valid in Q-SYS) lists the others under extra_sources.
    static JArray ReadSignals(List<object> comps, Func<object, string> refOf)
    {
        var groups = new Dictionary<string, JObject>(StringComparer.OrdinalIgnoreCase);
        foreach (var c in comps)
            foreach (var dir in new[] { "Output", "Input" })
                foreach (var p in Q.Pins(c, dir))
                {
                    var name = SignalName(p);
                    if (name == null) continue;
                    JObject g;
                    if (!groups.TryGetValue(name, out g))
                        groups[name] = g = new JObject { { "name", name }, { "source", null }, { "targets", new JArray() } };
                    if (dir == "Input") ((JArray)g["targets"]).Add(refOf(p));
                    else if (g["source"].Type == JTokenType.Null) { g["name"] = name; g["source"] = refOf(p); }
                    else
                    {
                        if (g["extra_sources"] == null) g["extra_sources"] = new JArray();
                        ((JArray)g["extra_sources"]).Add(refOf(p));
                    }
                }
        return new JArray(groups.Values.OrderBy(g => (string)g["name"], StringComparer.OrdinalIgnoreCase));
    }
}
