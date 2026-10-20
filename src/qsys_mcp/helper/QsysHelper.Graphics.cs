// Schematic notes and frames: text, headers and group boxes on schematic pages (the Designer classes
// the UCI builder uses for text). Their property names don't always match their serialized fields
// (TextBlock's _Color is TextColor), so each spec key tries a list of likely names, and the build
// warns, listing the type's writable properties, when none of them takes the value.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

static partial class QsysHelper
{
    static readonly Dictionary<string, string> GraphicTypes = new Dictionary<string, string> {
        { "text", "QSC.QSys.Design.Graphics.TextBlock" }, { "header", "QSC.QSys.Design.Graphics.Header" },
        { "group_box", "QSC.QSys.Design.Graphics.GroupBox" } };

    // spec key -> property names to try, most likely first
    static readonly Dictionary<string, string[]> GraphicProps = new Dictionary<string, string[]> {
        { "position", new[] { "Position", "Location" } }, { "size", new[] { "Size" } }, { "z", new[] { "ZOrder" } },
        { "text", new[] { "Label", "Text" } }, { "font_size", new[] { "FontSize" } }, { "bold", new[] { "Weight", "FontWeight" } },
        { "color", new[] { "TextColor", "Color", "ForegroundColor", "Foreground" } },
        { "fill", new[] { "Fill", "FillColor", "Background", "BackgroundColor" } },
        { "stroke", new[] { "StrokeColor", "Stroke", "BorderColor" } },
        { "stroke_width", new[] { "StrokeWidth", "StrokeThickness", "BorderThickness" } },
        { "corner_radius", new[] { "CornerRadius" } },
        { "align", new[] { "HorizontalTextAlignment", "HorizontalAlignment", "TextAlignment" } },
        { "valign", new[] { "VerticalTextAlignment", "VerticalAlignment" } } };

    static readonly Type[] NumericTypes = { typeof(double), typeof(float), typeof(int), typeof(uint), typeof(long), typeof(short), typeof(decimal) };

    // spec.graphics onto the first layer of their pages; returns how many. Group boxes get a ZOrder below
    // everything already on their page, text and headers one above it, each in list order.
    static int BuildGraphics(JArray specs, Dictionary<string, object> pages, string firstPage, JArray warnings)
    {
        if (specs == null) return 0;
        var boxes = new Dictionary<object, int>();   // page -> how many group boxes go on it
        foreach (JObject gs in specs)
            if ((string)gs["type"] == "group_box") { var pg = GraphicPage(gs, pages, firstPage); int n; boxes.TryGetValue(pg, out n); boxes[pg] = n + 1; }
        var z = new Dictionary<object, int[]>();       // page -> next ZOrder for {group boxes, text and headers}
        var warned = new HashSet<string>();
        int count = 0;
        foreach (JObject gs in specs)
        {
            var kind = (string)gs["type"];
            string typeName;
            if (kind == null || !GraphicTypes.TryGetValue(kind, out typeName))
                throw new Exception("graphics[" + count + "]: type must be text, header or group_box, not " + kind);
            var page = GraphicPage(gs, pages, firstPage);
            int[] next;
            if (!z.TryGetValue(page, out next))
            {
                var range = ZRange(page);
                int n; boxes.TryGetValue(page, out n);
                z[page] = next = new[] { range[0] - n, range[1] + 1 };
            }
            var el = Activator.CreateInstance(Q.T(typeName), true);
            bool box = kind == "group_box";
            SetGraphic(el, "position", new System.Windows.Point((double?)gs["x"] ?? 0, (double?)gs["y"] ?? 0), warned, warnings);
            SetGraphic(el, "size", new System.Windows.Size((double?)gs["w"] ?? (kind == "text" ? 200 : 400), (double?)gs["h"] ?? (box ? 300 : 40)), warned, warnings);
            if (gs["text"] != null) SetGraphic(el, "text", (string)gs["text"], warned, warnings);
            SetGraphic(el, "font_size", (double?)gs["font_size"] ?? (kind == "header" ? 16 : 12), warned, warnings);
            if (gs["bold"] != null) SetGraphic(el, "bold", (bool)gs["bold"] ? "Bold" : "Normal", warned, warnings);
            SetGraphic(el, "color", GraphicColour(gs, "color", "#FF000000"), warned, warnings);
            if (gs["fill"] != null) SetGraphic(el, "fill", GraphicColour(gs, "fill", null), warned, warnings);
            if (gs["stroke"] != null || box) SetGraphic(el, "stroke", GraphicColour(gs, "stroke", "#FF000000"), warned, warnings);
            if (gs["stroke_width"] != null || box) SetGraphic(el, "stroke_width", (double?)gs["stroke_width"] ?? 1, warned, warnings);
            if (gs["corner_radius"] != null) SetGraphic(el, "corner_radius", (double)gs["corner_radius"], warned, warnings);
            if (gs["align"] != null) SetGraphic(el, "align", (string)gs["align"], warned, warnings);
            if (gs["valign"] != null) SetGraphic(el, "valign", (string)gs["valign"], warned, warnings);
            if (kind == "text") TrySet(el, new[] { "SizeToText" }, false);   // keep the given size
            Q.Call(Q.L(Q.Get(page, "Layers"))[0], "AddChild", el);
            SetGraphic(el, "z", box ? next[0]++ : next[1]++, warned, warnings);
            count++;
        }
        return count;
    }

    static object GraphicPage(JObject gs, Dictionary<string, object> pages, string firstPage)
    {
        var name = (string)gs["page"] ?? firstPage;
        if (!pages.ContainsKey(name)) throw new Exception("graphics: no page '" + name + "' (declare it in spec.pages)");
        return pages[name];
    }

    static System.Windows.Media.Color GraphicColour(JObject gs, string key, string fallback)
    {
        var s = (string)gs[key] ?? fallback;
        try { return Q.C(s); }
        catch (Exception) { throw new Exception("graphics: " + key + " '" + s + "' is not a colour (use #AARRGGBB)"); }
    }

    // the lowest and highest ZOrder on a page (0 when there are none)
    static int[] ZRange(object page)
    {
        int lo = 0, hi = 0;
        foreach (var k in Q.L(Q.Get(page, "AllChildren")))
        {
            var v = Q.TryGet(k, "ZOrder");
            if (!(v is int || v is double)) continue;
            int n = (int)Convert.ToDouble(v);
            lo = Math.Min(lo, n); hi = Math.Max(hi, n);
        }
        return new[] { lo, hi };
    }

    // sets a spec key on a graphic, or warns (once per type and key) when none of its properties takes it
    static void SetGraphic(object el, string key, object value, HashSet<string> warned, JArray warnings)
    {
        var why = TrySet(el, GraphicProps[key], value);
        if (why == null || !warned.Add(el.GetType().Name + "." + key)) return;
        warnings.Add("graphics: " + el.GetType().Name + " took no '" + key + "' (tried " + string.Join(", ", GraphicProps[key]) + ": " + why +
            "); its writable properties: " + WritableProps(el.GetType()));
    }

    // sets the first writable property of these names that takes the value; null, or why none did
    static string TrySet(object o, string[] names, object value)
    {
        string why = "no such property";
        foreach (var n in names)
            foreach (var p in o.GetType().GetProperties(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
            {
                if (p.Name != n || !p.CanWrite || p.GetIndexParameters().Length > 0) continue;
                object v;
                if (!Coerce(value, p.PropertyType, out v)) { why = p.Name + " is " + p.PropertyType.Name; continue; }
                try { p.SetValue(o, v, null); return null; }
                catch (Exception e) { why = p.Name + ": " + (e.InnerException ?? e).Message; }
            }
        return why;
    }

    // a spec value as a property's type: a Color as Color, uint or int (0xAARRGGBB), Brush or "#AARRGGBB";
    // a name as an enum value or FontWeight; a number as any numeric type or Thickness. False if it doesn't fit.
    static bool Coerce(object v, Type t, out object r)
    {
        r = null;
        t = Nullable.GetUnderlyingType(t) ?? t;
        try
        {
            if (t.IsInstanceOfType(v)) r = v;
            else if (v is System.Windows.Media.Color)
            {
                var c = (System.Windows.Media.Color)v;
                uint argb = ((uint)c.A << 24) | ((uint)c.R << 16) | ((uint)c.G << 8) | c.B;
                if (t == typeof(uint)) r = argb;
                else if (t == typeof(int)) r = unchecked((int)argb);
                else if (t == typeof(string)) r = c.ToString();
                else if (t.IsAssignableFrom(typeof(System.Windows.Media.SolidColorBrush))) r = new System.Windows.Media.SolidColorBrush(c);
            }
            else if (v is string && t.IsEnum)
            {
                var name = Enum.GetNames(t).FirstOrDefault(x => x.Equals((string)v, StringComparison.OrdinalIgnoreCase));
                if (name != null) r = Enum.Parse(t, name);
            }
            else if (v is string && t == typeof(System.Windows.FontWeight))
            {
                var fw = typeof(System.Windows.FontWeights).GetProperty((string)v, BindingFlags.Public | BindingFlags.Static | BindingFlags.IgnoreCase);
                if (fw != null) r = fw.GetValue(null, null);
            }
            else if ((v is double || v is int) && t == typeof(System.Windows.Thickness)) r = new System.Windows.Thickness(Convert.ToDouble(v));
            else if ((v is double || v is int) && NumericTypes.Contains(t)) r = Convert.ChangeType(v, t, System.Globalization.CultureInfo.InvariantCulture);
        }
        catch (Exception) { r = null; }
        return r != null;
    }

    static string WritableProps(Type t)
    {
        return string.Join(", ", t.GetProperties(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .Where(p => p.CanWrite && p.GetIndexParameters().Length == 0).Select(p => p.Name + ":" + p.PropertyType.Name).Distinct().OrderBy(x => x));
    }

    // [{type, page, x, y, w, h, text}] for the schematic pages' graphics; other graphic classes keep their class name as type
    static JArray ReadGraphics(object doc)
    {
        var arr = new JArray();
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
            foreach (var el in Q.L(Q.Get(page, "AllChildren")))
            {
                var t = el.GetType();
                if (t.Namespace == null || !t.Namespace.StartsWith("QSC.QSys.Design.Graphics")) continue;
                var kind = GraphicTypes.Where(x => x.Value == t.FullName).Select(x => x.Key).FirstOrDefault() ?? t.Name;
                var o = new JObject { { "type", kind }, { "page", Convert.ToString(Q.Get(page, "Title")) } };
                var pos = Q.TryGet(el, "Position");
                if (pos is System.Windows.Point) { o["x"] = ((System.Windows.Point)pos).X; o["y"] = ((System.Windows.Point)pos).Y; }
                var size = Q.TryGet(el, "Size");
                if (size is System.Windows.Size && !((System.Windows.Size)size).IsEmpty)
                { o["w"] = ((System.Windows.Size)size).Width; o["h"] = ((System.Windows.Size)size).Height; }
                var text = Convert.ToString(Q.TryGet(el, "Label") ?? Q.TryGet(el, "Text"));
                if (!string.IsNullOrEmpty(text)) o["text"] = text;
                arr.Add(o);
            }
        return arr;
    }
}
