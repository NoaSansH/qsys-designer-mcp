// Shared helpers for the headless helper: load Designer's assembly, create components, set
// properties, wire pins, read designs back. Works against any installed Designer version:
// the folder is passed in at run time and the helper is compiled against that version.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;

static class Q
{
    public static string DesignerDir;
    public const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
    public static Assembly Asm;
    static System.Diagnostics.Stopwatch SW = System.Diagnostics.Stopwatch.StartNew();

    // Call first from Main (before touching any Designer type).
    public static void Init(string designerDir)
    {
        DesignerDir = designerDir;
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            var n = new AssemblyName(e.Name).Name;
            if (n.EndsWith(".resources")) return null;
            foreach (var ext in new[] { ".dll", ".exe" })
            { var p = Path.Combine(DesignerDir, n + ext); if (File.Exists(p)) return Assembly.LoadFrom(p); }
            return null;
        };
        Directory.SetCurrentDirectory(DesignerDir);
        // Designer 9.x (.NET Framework) keeps its code in the exe; 10.x (.NET 8+) in "Q-Sys Designer.dll"
        var dll = Path.Combine(DesignerDir, "Q-Sys Designer.dll");
        Asm = Assembly.LoadFrom(File.Exists(dll) ? dll : Path.Combine(DesignerDir, "Q-Sys Designer.exe"));
        // WPF pack://application resources must resolve against Designer's exe
        if (System.Windows.Application.Current == null) new System.Windows.Application();
        typeof(System.Windows.Application).GetField("_resourceAssembly", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, Asm);
        // Designer 10.5+ wants its runtime services set up before it loads a design
        var runtime = Asm.GetType("QSC.QSys.Design.DesignRuntime", false);
        var host = Asm.GetType("QSC.QSys.Design.HostDesignRuntime", false);
        if (runtime != null && host != null)
        {
            object current = null;
            try { current = runtime.GetProperty("Current", All).GetValue(null, null); } catch { }
            if (current == null) runtime.GetMethod("Initialize", All).Invoke(null, new[] { Construct(host, 0) });
        }
    }

    // Builds a Designer service the way its app would: each interface parameter is filled with the
    // Designer class that implements it, built the same way (Designer 10.5's DesignRuntime chain).
    static Type[] _types;
    public static object Construct(Type t, int depth)
    {
        if (depth > 8) throw new Exception("cannot build " + t.FullName + ": dependencies too deep");
        if (t.IsInterface || t.IsAbstract)
        {
            if (_types == null)
                try { _types = Asm.GetTypes(); } catch (ReflectionTypeLoadException e) { _types = e.Types.Where(x => x != null).ToArray(); }
            var impl = _types.FirstOrDefault(x => !x.IsAbstract && !x.IsInterface && t.IsAssignableFrom(x) && !x.Name.Contains("Mock") && !x.Name.Contains("Test"));
            if (impl == null) throw new Exception("no Designer class implements " + t.FullName);
            t = impl;
        }
        var ctor = t.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance).OrderBy(c => c.GetParameters().Length).First();
        return ctor.Invoke(ctor.GetParameters().Select(p => p.HasDefaultValue ? p.DefaultValue : Construct(p.ParameterType, depth + 1)).ToArray());
    }

    public static int Guard(Func<int> body)
    {
        try { return body(); }
        catch (Exception ex)
        {
            while (ex.InnerException != null) ex = ex.InnerException;
            Console.WriteLine("ERROR " + ex.GetType().Name + ": " + ex.Message + "\n" + ex.StackTrace);
            return 1;
        }
    }

    // ---- reflection ---------------------------------------------------------
    public static Type T(string n) { return Asm.GetType(n, true); }

    // the plugin catalog moved in Designer 10.5 (same API)
    public static Type PluginManager
    {
        get { return Asm.GetType("QSC.QSys.Design.Plugins.Manager", false) ?? T("QSC.QSys.Util.FileHandler.Managers.PluginManager"); }
    }
    public static object Get(object o, string p) { var pi = o.GetType().GetProperty(p, All); if (pi == null) throw new Exception(o.GetType().Name + " has no property " + p); return pi.GetValue(o, null); }
    public static object TryGet(object o, string p) { try { var pi = o.GetType().GetProperty(p, All); return pi == null ? null : pi.GetValue(o, null); } catch { return null; } }
    public static void Set(object o, string p, object v) { o.GetType().GetProperty(p, All).SetValue(o, v, null); }
    public static object F(object o, string f) { return o.GetType().GetField(f, All).GetValue(o); }
    public static object TryF(object o, string f) { var fi = o.GetType().GetField(f, All); return fi == null ? null : fi.GetValue(o); }
    public static object Call(object o, string m, params object[] a)
    {
        var t = o as Type ?? o.GetType();
        var mi = t.GetMethods(All).First(x => x.Name == m && x.GetParameters().Length == a.Length && !x.IsGenericMethod);
        return mi.Invoke(o is Type ? null : o, a);
    }
    public static List<object> L(object o) { return o == null ? new List<object>() : ((IEnumerable)o).Cast<object>().ToList(); }
    public static void Log(string s) { Console.WriteLine(string.Format("[{0,5} ms] {1}", SW.ElapsedMilliseconds, s)); }
    public static void SetEnum(object o, string prop, string name)
    {
        var p = o.GetType().GetProperty(prop, All);
        var et = Nullable.GetUnderlyingType(p.PropertyType) ?? p.PropertyType;
        var match = Enum.GetNames(et).FirstOrDefault(n => n.Equals(name, StringComparison.OrdinalIgnoreCase));
        if (match == null) throw new Exception(prop + " has no value '" + name + "'; values: " + string.Join(", ", Enum.GetNames(et)));
        p.SetValue(o, Enum.Parse(et, match), null);
    }

    // ---- documents ------------------------------------------------------------
    public static object Load(string path)
    {
        var fio = T("QSC.QSys.Design.FileIO");
        var load = fio.GetMethods(All).First(m => m.Name == "Load" && m.GetParameters()[0].ParameterType == typeof(string));
        var doc = load.Invoke(null, new object[] { path, null });
        Call(fio, "InitRoot", doc, path, false);
        RegisterPlugins(doc);
        return doc;
    }

    // A loaded design's plugin blocks only offer their controls once Designer knows their class. The
    // design carries each plugin's code (plugin_source), so the class is registered from it.
    static void RegisterPlugins(object doc)
    {
        var add = PluginManager.GetMethods(All).First(m => m.Name == "AddPlugin");
        foreach (var page in L(Get(doc, "Pages")))
            foreach (var el in L(Get(page, "AllChildren")))
            {
                var cls = Convert.ToString(TryGet(el, "ClassName"));
                if (!cls.StartsWith("%PLUGIN%")) continue;
                var source = Convert.ToString(GetProp(el, "plugin_source"));
                if (string.IsNullOrEmpty(source)) continue;
                try { add.Invoke(null, new object[] { cls, source, null }); } catch { }
            }
    }

    // ---- components -------------------------------------------------------------
    public static object NewComponent(string cls)
    {
        var creatorT = T("QSC.QSys.Drag.ComponentCreator`1").MakeGenericType(T("QSC.QSys.Design.Component"));
        var cc = Activator.CreateInstance(creatorT, true);
        Set(cc, "ClassName", cls);
        var create = creatorT.GetMethods(All).First(m => m.Name == "Create" && m.GetParameters().Length == 1);
        return create.Invoke(cc, new object[] { new System.Windows.Point(0, 0) });
    }

    public static Type Helper { get { return T("QSC.QSys.Design.ComponentHelper"); } }

    public static object GetProp(object comp, string codeName)
    {
        return Helper.GetMethods(All).First(m => m.Name == "GetComponentProperty" && m.GetParameters().Length == 2 && !m.IsGenericMethod)
            .Invoke(null, new[] { comp, (object)codeName });
    }

    public static void SetProp(object comp, string codeName, object value)
    {
        var cur = GetProp(comp, codeName);
        object v = value;
        if (cur != null && value != null && cur.GetType() != value.GetType()) v = Convert.ChangeType(value, cur.GetType());
        Helper.GetMethods(All).First(m => m.Name == "UpdateComponentProperty" && !m.IsGenericMethod &&
            m.GetParameters()[0].ParameterType.Name == "Component").Invoke(null, new[] { comp, codeName, v });
    }

    public static void Refresh(object comp) { Helper.GetMethod("UpdateComponent", All).Invoke(null, new[] { comp }); }

    // all properties as (codeName, value)
    public static List<Tuple<string, string>> Props(object comp)
    {
        return L(Get(comp, "Properties")).Select(p => Tuple.Create(
            Convert.ToString(TryF(p, "CodeName") ?? TryGet(p, "CodeName") ?? TryGet(p, "Name")),
            Convert.ToString(TryF(p, "Value") ?? TryGet(p, "Value")))).ToList();
    }

    public static List<object> Pins(object comp, string direction)
    {
        return L(Get(comp, "Pins")).Where(p => F(p, "Direction").ToString() == direction)
            .OrderBy(p => Convert.ToDouble(Get(p, "GraphicIndex"))).ToList();
    }

    // n-th (1-based) pin of a direction ("Input"/"Output"), in drawing order.
    public static object Pin(object comp, string direction, int n)
    {
        var pins = Pins(comp, direction);
        if (n < 1 || n > pins.Count)
            throw new Exception(Get(comp, "Label") + " has " + pins.Count + " " + direction.ToLower() + " pins, not " + n);
        return pins[n - 1];
    }

    public static string PinName(object pin) { return Convert.ToString(TryF(pin, "PrettyName") ?? TryGet(pin, "Label")); }

    public static List<object> Descriptors(object component)
    {
        var gc = Helper.GetMethods(All).First(m => m.Name == "GetControls" && m.GetParameters().Length == 1);
        return L(gc.Invoke(null, new[] { component }));
    }
    public static string Cid(object d) { return (string)F(d, "ControlId"); }

    public static object PlaceFromInventory(object item, string placeableName)
    {
        var pi = L(Get(item, "PlaceableItems")).FirstOrDefault(p => (string)Get(p, "Name") == placeableName);
        if (pi == null)
            throw new Exception("no placeable '" + placeableName + "' on " + Get(item, "Name") + "; has: " +
                string.Join(", ", L(Get(item, "PlaceableItems")).Select(p => Get(p, "Name"))));
        var named = pi.GetType().GetField("_PlaceableComponent", All).GetValue(pi);
        return Call(Get(named, "Creator"), "GetComponent");
    }

    public static System.Windows.Media.Color C(string hex) { return (System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(hex); }
}
