// UCI parts of the headless helper: a panel's size, group boxes and headers on UCI pages, and reading
// them back. Designer's public names for these are not confirmed on every version, so each property is
// looked up from a short list of likely names; when none fits, the error lists the type's real ones.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

static class QUci
{
    const BindingFlags Declared = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly;
    static readonly string[] FrameOnly = { "fill", "stroke", "stroke_width", "corner_radius" };
    static readonly string[] NumberWords = { "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty" };

    // ---- panel size ---------------------------------------------------------------------
    // Real files keep a panel's size in pixels in _HorizontalResolution2/_VerticalResolution2, and an aspect
    // in _HorizontalResolution/_VerticalResolution (4 and 3, even on 1280 x 800 panels). Designer's own UCI
    // export calls the pixels "HorizontalResolution", so properties are matched to these fields by their
    // current values, not trusted by name.
    static readonly string[] PixelsH = { "HorizontalResolution2", "HorizontalResolution", "HorizontalPixels", "PixelWidth", "ResolutionWidth" };
    static readonly string[] PixelsV = { "VerticalResolution2", "VerticalResolution", "VerticalPixels", "PixelHeight", "ResolutionHeight" };
    static readonly string[] AspectH = { "HorizontalResolution", "HorizontalAspect", "AspectWidth" };
    static readonly string[] AspectV = { "VerticalResolution", "VerticalAspect", "AspectHeight" };

    // Sets a UCI's size the way a custom panel has it: panel type Custom (a device preset imposes its own
    // size), the orientation, the aspect ratio that reads as width:height, then the pixels. Adds what the
    // panel ended up with to report; throws if Designer kept another size.
    public static void SetSize(object panel, int width, int height, JObject report)
    {
        var what = "UCI '" + report["name"] + "'";
        if (width <= 0 || height <= 0) throw new Exception(what + ": give both width and height, in pixels");
        var w = PixelProperty(panel, "Horizontal", PixelsH, what);
        var h = PixelProperty(panel, "Vertical", PixelsV, what);
        var aw = AspectProperty(panel, "Horizontal", AspectH, w);
        var ah = AspectProperty(panel, "Vertical", AspectV, h);
        var notes = new JArray();
        if (!Same(ValueOf(panel, w), width) || !Same(ValueOf(panel, h), height))
        {
            SetPanelType(panel, what, notes);
            SetOrientation(panel, width >= height, what, notes);
            var aspect = SetAspectRatio(panel, width, height, what, notes);
            if (aw != null && ah != null)
            {
                Assign(panel, aw, aspect[0], what);
                Assign(panel, ah, aspect[1], what);
                report["aspect"] = aspect[0] + ":" + aspect[1];
            }
            Assign(panel, w, width, what);
            Assign(panel, h, height, what);
        }
        ReadSize(panel, report);
        if (notes.Count > 0) report["notes"] = notes;
        var fw = Q.TryF(panel, "_HorizontalResolution2");
        var fh = Q.TryF(panel, "_VerticalResolution2");
        if (!Same(ValueOf(panel, w), width) || !Same(ValueOf(panel, h), height) || (fw != null && !Same(fw, width)) || (fh != null && !Same(fh, height)))
            throw new Exception(what + ": asked for " + width + " x " + height + " but Designer kept " + ValueOf(panel, w) + " x " + ValueOf(panel, h) +
                " (" + w.Name + ", " + h.Name + ")" + (fw != null ? ", stored " + fw + " x " + fh : "") + "; panel now: " + report.ToString(Newtonsoft.Json.Formatting.None));
    }

    // The panel's size as stored, plus Designer's names for its aspect ratio, orientation and panel type.
    public static void ReadSize(object panel, JObject o)
    {
        Add(o, "width", Num(Q.TryF(panel, "_HorizontalResolution2") ?? ValueOf(panel, "HorizontalResolution2", "HorizontalPixels", "PixelWidth", "ResolutionWidth")));
        Add(o, "height", Num(Q.TryF(panel, "_VerticalResolution2") ?? ValueOf(panel, "VerticalResolution2", "VerticalPixels", "PixelHeight", "ResolutionHeight")));
        foreach (var pair in new[] { new[] { "aspect_ratio", "AspectRatio" }, new[] { "orientation", "Orientation" }, new[] { "panel_type", "PanelType" } })
        {
            var v = ValueOf(panel, pair[1]) ?? Q.TryF(panel, "_" + pair[1]);
            if (v != null) o[pair[0]] = Convert.ToString(v, CultureInfo.InvariantCulture);
        }
    }

    // The pixel property of one axis: the candidate that holds the stored pixels, else the first whose name
    // can only mean pixels ("HorizontalResolution" alone might be the aspect).
    static PropertyInfo PixelProperty(object panel, string axis, string[] names, string what)
    {
        var px = Q.TryF(panel, "_" + axis + "Resolution2");
        var ar = Q.TryF(panel, "_" + axis + "Resolution");
        var props = Numbers(panel, names);
        PropertyInfo hit = null;
        if (px != null && !Same(px, ar)) hit = props.FirstOrDefault(p => Same(ValueOf(panel, p), px));
        if (hit == null) hit = props.FirstOrDefault(p => p.Name != axis + "Resolution");
        if (hit == null)
            throw new Exception(what + ": can't find the panel's " + axis.ToLowerInvariant() + " size in pixels (tried " + string.Join(", ", names) +
                "); " + panel.GetType().Name + " has: " + Members(panel.GetType()));
        return hit;
    }

    // The aspect property of one axis, only when it is clearly the one holding the stored aspect.
    static PropertyInfo AspectProperty(object panel, string axis, string[] names, PropertyInfo pixels)
    {
        var px = Q.TryF(panel, "_" + axis + "Resolution2");
        var ar = Q.TryF(panel, "_" + axis + "Resolution");
        if (px == null || ar == null || Same(px, ar)) return null;
        return Numbers(panel, names).FirstOrDefault(p => p.Name != pixels.Name && Same(ValueOf(panel, p), ar));
    }

    static void SetPanelType(object panel, string what, JArray notes)
    {
        var p = EnumProperty(panel, "PanelType");
        if (p == null) { notes.Add("no PanelType property, so the panel type was left as it was"); return; }
        var t = Underlying(p.PropertyType);
        var custom = Enum.GetNames(t).FirstOrDefault(n => n.IndexOf("custom", StringComparison.OrdinalIgnoreCase) >= 0);
        if (custom == null) { notes.Add("PanelType has no custom value, so it stays " + ValueOf(panel, p) + "; its values: " + Describe(t)); return; }
        Assign(panel, p, custom, what);
    }

    static void SetOrientation(object panel, bool landscape, string what, JArray notes)
    {
        var p = EnumProperty(panel, "Orientation");
        if (p == null) { if (!landscape) notes.Add("no Orientation property, so a portrait size is only width x height"); return; }
        var t = Underlying(p.PropertyType);
        var name = Enum.GetNames(t).FirstOrDefault(n => n.IndexOf(landscape ? "landscape" : "portrait", StringComparison.OrdinalIgnoreCase) >= 0);
        if (name == null)
        {
            if (landscape) return;   // every panel seen so far is landscape by default
            throw new Exception(what + ": Orientation has no portrait value; values: " + Describe(t));
        }
        Assign(panel, p, name, what);
    }

    // Picks the AspectRatio value that reads as width:height (16:10 for 1280 x 800) by its name or its
    // description, else a custom one. Returns the aspect as two whole numbers, in the panel's orientation.
    static int[] SetAspectRatio(object panel, int width, int height, string what, JArray notes)
    {
        var g = Gcd(width, height);
        var aspect = new[] { width / g, height / g };
        if (Math.Max(aspect[0], aspect[1]) == 8 && Math.Min(aspect[0], aspect[1]) == 5) aspect = Oriented(new[] { 16, 10 }, width >= height);
        var p = EnumProperty(panel, "AspectRatio");
        if (p == null) { notes.Add("no AspectRatio property, so the aspect ratio was left as it was"); return aspect; }
        var t = Underlying(p.PropertyType);
        double want = (double)Math.Max(width, height) / Math.Min(width, height);
        string pick = null;
        foreach (var name in Enum.GetNames(t))
        {
            var r = RatioOf(t, name);
            if (r == null || Math.Abs((double)Math.Max(r[0], r[1]) / Math.Min(r[0], r[1]) - want) > 0.005 * want) continue;
            pick = name;
            aspect = Oriented(r, width >= height);
            break;
        }
        if (pick == null) pick = Enum.GetNames(t).FirstOrDefault(n => n.IndexOf("custom", StringComparison.OrdinalIgnoreCase) >= 0);
        if (pick == null)
            throw new Exception(what + ": no AspectRatio value reads as " + aspect[0] + ":" + aspect[1] + " (" + width + " x " + height + "); values: " + Describe(t));
        Assign(panel, p, pick, what);
        return aspect;
    }

    // "Ratio16x10", "_16_10", "SixteenByTen", or a description such as "16:10 (1280x800)" -> {16, 10}
    static int[] RatioOf(Type enumType, string name)
    {
        var r = RatioIn(name);
        if (r != null) return r;
        foreach (var s in Texts(enumType, name))
        {
            r = RatioIn(s);
            if (r != null) return r;
        }
        return null;
    }

    static int[] RatioIn(string s)
    {
        var m = Regex.Match(s, "([0-9]+) *[:xX/] *([0-9]+)");
        if (m.Success) return Positive(int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture), int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture));
        var nums = new List<int>();
        foreach (Match t in Regex.Matches(s, "[0-9]+|[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+"))
        {
            int n;
            if (int.TryParse(t.Value, NumberStyles.None, CultureInfo.InvariantCulture, out n)) nums.Add(n);
            else
            {
                var i = Array.IndexOf(NumberWords, t.Value.ToLowerInvariant());
                if (i >= 0) nums.Add(i);
            }
        }
        return nums.Count == 2 ? Positive(nums[0], nums[1]) : null;
    }

    static int[] Positive(int a, int b) { return a > 0 && b > 0 ? new[] { a, b } : null; }

    static int[] Oriented(int[] r, bool landscape)
    {
        var big = Math.Max(r[0], r[1]); var small = Math.Min(r[0], r[1]);
        return landscape ? new[] { big, small } : new[] { small, big };
    }

    static int Gcd(int a, int b)
    {
        while (b != 0) { var r = a % b; a = b; b = r; }
        return a;
    }

    // ---- group boxes and headers --------------------------------------------------------------
    public static bool IsGraphic(string kind) { return kind == "group_box" || kind == "header"; }

    // Frames go behind everything else on their page, so the page adds them first.
    public static bool IsFrame(JObject es) { return (string)es["type"] == "group_box"; }

    // A group box (frame with a title) or a header (title with a line), the same objects Designer puts
    // on schematic pages.
    public static object Graphic(object layer, string kind, JObject es)
    {
        var what = "UCI " + kind + (es["text"] != null ? " '" + es["text"] + "'" : "");
        var frame = kind == "group_box";
        if (!frame)
            foreach (var k in FrameOnly)
                if (es[k] != null) throw new Exception(what + ": a header has no " + k + " (a group_box does)");
        var el = New(frame ? "QSC.QSys.Design.Graphics.GroupBox" : "QSC.QSys.Design.Graphics.Header");
        Put(el, what, "text", (string)es["text"] ?? "", "Label", "Text");
        Place(el, es);
        if (es["font_size"] != null) Put(el, what, "font_size", (double)es["font_size"], "FontSize");
        if (es["bold"] != null) SetBold(el, what, (bool)es["bold"]);
        if (es["color"] != null) Put(el, what, "color", (string)es["color"], "TextColor", "Color");
        if (es["align"] != null) Put(el, what, "align", (string)es["align"], "HorizontalTextAlignment", "HorizontalAlignment", "TextAlignment");
        if (frame)
        {
            if (es["fill"] != null) Put(el, what, "fill", (string)es["fill"], "Fill", "FillColor", "BackgroundColor");
            if (es["stroke"] != null) Put(el, what, "stroke", (string)es["stroke"], "StrokeColor", "Stroke", "BorderColor");
            if (es["stroke_width"] != null) Put(el, what, "stroke_width", (int)es["stroke_width"], "StrokeWidth", "StrokeThickness", "BorderWidth");
            if (es["corner_radius"] != null)
            {
                Put(el, what, "corner_radius", (double)es["corner_radius"], "CornerRadius");
                // every box Designer saves has this set; without it the radius might be converted on load
                var flag = Find(el.GetType(), "IsCornerRadiusUpdated");
                if (flag != null && flag.CanWrite) Assign(el, flag, true, what);
            }
        }
        Q.Call(layer, "AddChild", el);
        return el;
    }

    // Designer writes bold as the font face "Bold" (its FontWeight stays 0).
    static void SetBold(object el, string what, bool bold)
    {
        var names = new[] { "Face", "FontFace", "FontStyle" };
        var p = Writable(el, names);
        if (p == null) throw NoProperty(el, what, "bold", names);
        var t = Underlying(p.PropertyType);
        var faces = bold ? new[] { "Bold" } : new[] { "Regular", "Normal" };
        object face = faces[0];
        if (t.IsEnum)
        {
            face = faces.Select(f => Enum.GetNames(t).FirstOrDefault(n => n.Equals(f, StringComparison.OrdinalIgnoreCase))).FirstOrDefault(n => n != null);
            if (face == null) throw new Exception(what + ": " + p.Name + " has no " + faces[0] + " value; values: " + Describe(t));
        }
        Assign(el, p, face, what);
    }

    static void Place(object el, JObject es)
    {
        Q.Set(el, "Position", new System.Windows.Point((double?)es["x"] ?? 0, (double?)es["y"] ?? 0));
        Q.Set(el, "Size", new System.Windows.Size((double?)es["w"] ?? 100, (double?)es["h"] ?? 40));
    }

    static object New(string fullName)
    {
        var t = Q.Asm.GetType(fullName, false);
        if (t == null) throw new Exception("this Designer has no " + fullName + "; its graphics types: " + GraphicsTypes());
        return Activator.CreateInstance(t, true);
    }

    static string GraphicsTypes()
    {
        Type[] types;
        try { types = Q.Asm.GetTypes(); } catch (ReflectionTypeLoadException e) { types = e.Types.Where(x => x != null).ToArray(); }
        return string.Join(", ", types.Where(x => x.Namespace == "QSC.QSys.Design.Graphics").Select(x => x.Name).OrderBy(n => n, StringComparer.Ordinal));
    }

    // Group box and header styling, under the spec's key names.
    public static void ReadGraphic(object el, JObject eo)
    {
        var type = el.GetType().Name;
        if (type != "GroupBox" && type != "Header") return;
        if (eo["label"] == null)
        {
            // the title, under "label" like every other element's text
            var text = Convert.ToString(ValueOf(el, "Text"), CultureInfo.InvariantCulture);
            if (!string.IsNullOrEmpty(text)) eo["label"] = text;
        }
        Add(eo, "font_size", Num(ValueOf(el, "FontSize")));
        var face = ValueOf(el, "Face", "FontFace", "FontStyle");
        if (face != null) eo["bold"] = Convert.ToString(face, CultureInfo.InvariantCulture).IndexOf("Bold", StringComparison.OrdinalIgnoreCase) >= 0;
        Add(eo, "color", Hex(ValueOf(el, "TextColor", "Color")));
        var align = ValueOf(el, "HorizontalTextAlignment", "HorizontalAlignment", "TextAlignment");
        if (align != null) eo["align"] = Convert.ToString(align, CultureInfo.InvariantCulture);
        if (type != "GroupBox") return;
        Add(eo, "fill", Hex(ValueOf(el, "Fill", "FillColor", "BackgroundColor")));
        Add(eo, "stroke", Hex(ValueOf(el, "StrokeColor", "Stroke", "BorderColor")));
        Add(eo, "stroke_width", Num(ValueOf(el, "StrokeWidth", "StrokeThickness", "BorderWidth")));
        Add(eo, "corner_radius", Num(ValueOf(el, "CornerRadius")));
    }

    // ---- tolerant reflection ------------------------------------------------------------------
    // A property by name, most derived first (a property redeclared lower down is not ambiguous here).
    static PropertyInfo Find(Type t, string name)
    {
        for (var x = t; x != null; x = x.BaseType)
        {
            var p = x.GetProperties(Declared).FirstOrDefault(y => y.Name == name && y.GetIndexParameters().Length == 0);
            if (p != null) return p;
        }
        return null;
    }

    static PropertyInfo Writable(object o, string[] names)
    {
        foreach (var n in names)
        {
            var p = Find(o.GetType(), n);
            if (p != null && p.CanWrite) return p;
        }
        return null;
    }

    static List<PropertyInfo> Numbers(object o, string[] names)
    {
        var list = new List<PropertyInfo>();
        foreach (var n in names)
        {
            var p = Find(o.GetType(), n);
            if (p != null && p.CanRead && p.CanWrite && IsNumber(p.PropertyType)) list.Add(p);
        }
        return list;
    }

    static PropertyInfo EnumProperty(object o, string name)
    {
        var p = Find(o.GetType(), name);
        return p != null && p.CanWrite && Underlying(p.PropertyType).IsEnum ? p : null;
    }

    // Sets the first of names that o has, converting the spec's value to the property's type.
    static void Put(object o, string what, string key, object value, params string[] names)
    {
        var p = Writable(o, names);
        if (p == null) throw NoProperty(o, what, key, names);
        Assign(o, p, value, what);
    }

    static void Assign(object o, PropertyInfo p, object value, string what)
    {
        try { p.SetValue(o, ToType(p.PropertyType, value, what + ", " + p.Name), null); }
        catch (Exception e)
        {
            // no inner exception: the helper reports only the innermost message, and this one says where
            throw new Exception(what + ": setting " + o.GetType().Name + "." + p.Name + " to " + value + " failed: " + Inner(e).Message);
        }
    }

    static Exception NoProperty(object o, string what, string key, string[] names)
    {
        return new Exception(what + ": can't set " + key + ", " + o.GetType().Name + " has none of " + string.Join(", ", names) +
            "; its properties: " + Members(o.GetType()));
    }

    static object ToType(Type type, object v, string what)
    {
        var t = Underlying(type);
        var s = v as string;
        if (t.IsEnum)
        {
            if (s == null) return Enum.ToObject(t, v);
            var name = Enum.GetNames(t).FirstOrDefault(n => n.Equals(s, StringComparison.OrdinalIgnoreCase));
            if (name == null) throw new Exception(what + " has no value '" + s + "'; values: " + Describe(t));
            return Enum.Parse(t, name);
        }
        if (s != null && s.StartsWith("#", StringComparison.Ordinal))
        {
            var c = Q.C(s);
            if (t == typeof(System.Windows.Media.Color)) return c;
            if (typeof(System.Windows.Media.Brush).IsAssignableFrom(t) && t.IsAssignableFrom(typeof(System.Windows.Media.SolidColorBrush)))
                return new System.Windows.Media.SolidColorBrush(c);
            uint argb = ((uint)c.A << 24) | ((uint)c.R << 16) | ((uint)c.G << 8) | c.B;
            if (t == typeof(uint)) return argb;
            if (t == typeof(int)) return unchecked((int)argb);
        }
        if (t == typeof(string)) return Convert.ToString(v, CultureInfo.InvariantCulture);
        return Convert.ChangeType(v, t, CultureInfo.InvariantCulture);
    }

    // The value of the first of names that o has (null if none, or if reading it fails).
    static object ValueOf(object o, params string[] names)
    {
        foreach (var n in names)
        {
            var p = Find(o.GetType(), n);
            if (p != null && p.CanRead) return ValueOf(o, p);
        }
        return null;
    }

    static object ValueOf(object o, PropertyInfo p)
    {
        try { return p.GetValue(o, null); } catch { return null; }
    }

    static string Members(Type t)
    {
        var names = new List<string>();
        for (var x = t; x != null && x != typeof(object); x = x.BaseType)
            foreach (var p in x.GetProperties(Declared))
                if (p.GetIndexParameters().Length == 0) names.Add(p.Name + ":" + p.PropertyType.Name);
        return string.Join(", ", names.Distinct().OrderBy(n => n, StringComparer.Ordinal));
    }

    // "Name (description)" for each value of an enum, from its attributes such as [Description].
    static string Describe(Type t)
    {
        return string.Join(", ", Enum.GetNames(t).Select(n =>
        {
            var d = Texts(t, n).FirstOrDefault(x => x != n);
            return d == null ? n : n + " (" + d + ")";
        }));
    }

    static IEnumerable<string> Texts(Type enumType, string name)
    {
        var f = enumType.GetField(name, BindingFlags.Public | BindingFlags.Static);
        object[] attrs;
        try { attrs = f == null ? new object[0] : f.GetCustomAttributes(false); } catch { attrs = new object[0]; }
        foreach (var a in attrs)
            foreach (var p in a.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                if (p.PropertyType != typeof(string) || p.GetIndexParameters().Length > 0) continue;
                string s = null;
                try { s = (string)p.GetValue(a, null); } catch { }
                if (!string.IsNullOrEmpty(s)) yield return s;
            }
    }

    static Type Underlying(Type t) { return Nullable.GetUnderlyingType(t) ?? t; }

    static bool IsNumber(Type t)
    {
        t = Underlying(t);
        return t == typeof(double) || t == typeof(float) || t == typeof(int) || t == typeof(uint) || t == typeof(long) || t == typeof(short) || t == typeof(decimal);
    }

    static bool Same(object a, object b)
    {
        if (a == null || b == null) return false;
        try { return Math.Abs(Convert.ToDouble(a, CultureInfo.InvariantCulture) - Convert.ToDouble(b, CultureInfo.InvariantCulture)) < 1e-6; }
        catch { return false; }
    }

    static Exception Inner(Exception e)
    {
        while (e.InnerException != null) e = e.InnerException;
        return e;
    }

    static void Add(JObject o, string key, JToken v) { if (v != null) o[key] = v; }

    // whole numbers as integers (1280, not 1280.0)
    static JToken Num(object v)
    {
        if (v == null) return null;
        double d;
        try { d = Convert.ToDouble(v, CultureInfo.InvariantCulture); } catch { return Convert.ToString(v, CultureInfo.InvariantCulture); }
        return d == Math.Floor(d) && Math.Abs(d) < 1e9 ? (JToken)(long)d : (JToken)d;
    }

    static JToken Hex(object v)
    {
        var brush = v as System.Windows.Media.SolidColorBrush;
        if (brush != null) v = brush.Color;
        if (v is System.Windows.Media.Color)
        {
            var c = (System.Windows.Media.Color)v;
            return string.Format(CultureInfo.InvariantCulture, "#{0:X2}{1:X2}{2:X2}{3:X2}", c.A, c.R, c.G, c.B);
        }
        if (v is uint) return "#" + ((uint)v).ToString("X8", CultureInfo.InvariantCulture);
        if (v is int) return "#" + unchecked((uint)(int)v).ToString("X8", CultureInfo.InvariantCulture);
        return null;
    }
}
