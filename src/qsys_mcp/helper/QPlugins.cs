// Plugins for the headless helper: lists the plugins installed for Designer and places them,
// through Designer's own plugin list and component creators. Those are not a public API and their
// names differ between versions, so they are found by reflection: candidate names are tried and,
// when nothing fits, the error lists the members Designer really has.
//
// A placed plugin is a QSC.QSys.Design.Component with IsPlugin set, ClassName "%PLUGIN%_" + the
// plugin's PluginInfo.Id, and the plugin's code (or, if it is encrypted, a JSON envelope) in its
// plugin_source property. Designer embeds that code itself; nothing here copies or writes it.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

static class QPlugins
{
    public const string Prefix = "%PLUGIN%_";
    // properties Designer fills in from the installed plugin, never from a spec
    public static readonly string[] SetByDesigner = { "plugin_source", "plugin_is_encrypted" };

    class Entry
    {
        public string Name, Id, Version, File;
        public bool Encrypted, Loaded = true;
        public object Native;   // Designer's own object for this plugin (null when only its file was seen)
        public List<string> Aliases = new List<string>();   // the name in its file, if Designer's list names it differently
        public string Class { get { return string.IsNullOrEmpty(Id) ? null : Prefix + Id; } }
        public string Key { get { return ((Id ?? Name) + "|" + Version + "|" + File).ToLowerInvariant(); } }

        public JObject ToJson()
        {
            var o = new JObject { { "name", Name }, { "id", Id }, { "version", Version }, { "class", Class }, { "file", File } };
            if (Encrypted) o["encrypted"] = true;
            if (!Loaded) o["loaded"] = false;
            return o;
        }

        public override string ToString()
        {
            var bits = new List<string>();
            if (!string.IsNullOrEmpty(Id)) bits.Add("id " + Id);
            if (!string.IsNullOrEmpty(Version)) bits.Add(Version);
            if (string.IsNullOrEmpty(Name) && File != null) bits.Add(System.IO.Path.GetFileName(File));
            return (Name ?? "(unnamed plugin)") + (bits.Count > 0 ? " (" + string.Join(", ", bits) + ")" : "");
        }
    }

    // a member (field, property or parameterless Get method) that may hold Designer's plugin list
    class Source { public object Owner; public MemberInfo Member; public string Label; }

    class Attempt { public string Label; public Func<object> Run; }

    static bool _ready, _loadersRun;
    static List<Entry> _entries = new List<Entry>();   // from Designer's own plugin list
    static List<Entry> _files = new List<Entry>();     // plugin files found in the plugin folders
    static List<Source> _sources = new List<Source>();
    static string _from;                               // the member that held Designer's list
    static readonly List<string> _trace = new List<string>();

    static readonly string[] IdNames = { "Id", "ID", "PluginId", "PluginID", "Guid", "UniqueId", "Identifier" };
    static readonly string[] NameNames = { "Name", "PluginName", "FullName", "DisplayName", "PrettyName", "Title" };
    static readonly string[] VersionNames = { "Version", "PluginVersion", "BuildVersion" };
    static readonly string[] FileNames = { "FileName", "FilePath", "FullPath", "Path", "File", "Filename", "SourceFile", "SourcePath",
        "PluginFile", "PluginPath", "Location", "FullFileName" };
    static readonly string[] InfoNames = { "Info", "PluginInfo", "Metadata", "Header", "Definition", "Descriptor", "Manifest", "Details" };
    // methods that may fill Designer's plugin list: these on any owner of a candidate list,
    static readonly string[] LoaderNames = { "LoadPlugins", "LoadAllPlugins", "LoadUserPlugins", "ScanPlugins", "RefreshPlugins", "ReloadPlugins",
        "DiscoverPlugins", "FindPlugins", "LoadPluginsAsync", "ScanPluginsAsync", "RefreshPluginsAsync", "ReloadPluginsAsync" };
    // and these only on types named for plugins (PluginManager.Load, say)
    static readonly string[] GenericLoaderNames = { "Load", "LoadAll", "Scan", "Refresh", "Reload", "Discover", "Initialize", "Init",
        "LoadAsync", "RefreshAsync", "ReloadAsync", "InitializeAsync" };
    static readonly string[] ManagerWords = { "Manager", "Registry", "Library", "Cache", "Loader", "Catalog", "Store", "Database",
        "Service", "Provider", "Repository", "Collection" };
    static readonly string[] FactoryWords = { "Create", "New", "Instantiate", "Make", "Build" };

    // ---- public entry points ----------------------------------------------------------------
    // info: {plugins: [{name, id, version, class, file}], plugins_from, plugin_folders}
    public static void ListInto(JObject res)
    {
        try
        {
            Ensure();
            var list = new JArray();
            foreach (var e in Pool()) list.Add(e.ToJson());
            if (_entries.Count > 0)
                foreach (var f in _files.Where(x => !x.Loaded)) list.Add(f.ToJson());
            res["plugins"] = list;
            res["plugins_from"] = _entries.Count > 0 ? "Designer (" + _from + ")" : "plugin files (Designer's own plugin list was not found)";
            res["plugin_folders"] = new JArray(Folders());
        }
        catch (Exception ex)
        {
            ex = Inner(ex);
            res["plugins_error"] = ex.GetType().Name + ": " + ex.Message;
        }
    }

    public static object Create(JToken spec, JObject props, string where, JArray warnings)
    {
        JObject info;
        return Create(spec, props, where, warnings, out info);
    }

    // A plugin block from a spec's "plugin" ("name or id", or {name, id, version}), placed through
    // Designer, with the spec's properties applied. info is the installed plugin that was used.
    public static object Create(JToken spec, JObject props, string where, JArray warnings, out JObject info)
    {
        var e = Resolve(spec, where, warnings);
        var c = Place(e, where, warnings);
        var flag = Q.TryGet(c, "IsPlugin");
        if (!(flag is bool) || !(bool)flag)
            warnings.Add(where + ": Designer did not flag the block as a plugin (IsPlugin is " + (flag ?? "missing") + ")");
        var embedded = LuaVersion(Prop(c, "plugin_source"));
        if (e.Version != null && embedded != null && CompareVersions(embedded, e.Version) != 0)
            warnings.Add(where + ": Designer embedded version " + embedded + " of " + e.Name + ", not " + e.Version);
        Apply(c, props, where, warnings);
        info = e.ToJson();
        return c;
    }

    // read: what a placed plugin is (null if comp is not a plugin), from its class name and embedded
    // code. Also shortens props' plugin_source, which holds the plugin's whole code.
    public static JObject Describe(object comp, JObject props)
    {
        var cls = Convert.ToString(Q.TryGet(comp, "ClassName"));
        var flag = Q.TryGet(comp, "IsPlugin");
        bool prefixed = cls.StartsWith(Prefix, StringComparison.Ordinal);
        if (!prefixed && !(flag is bool && (bool)flag)) return null;
        var src = props == null ? null : (string)props["plugin_source"];
        bool encrypted = (props != null && string.Equals((string)props["plugin_is_encrypted"], "True", StringComparison.OrdinalIgnoreCase))
            || (src != null && src.TrimStart().StartsWith("{"));
        HideSource(props);
        return new JObject {
            { "name", encrypted ? null : LuaInfo(src, "Name") },
            { "id", prefixed ? cls.Substring(Prefix.Length) : null },
            { "version", encrypted ? null : LuaVersion(src) },
            { "encrypted", encrypted } };
    }

    public static void HideSource(JObject props)
    {
        var src = props == null ? null : (string)props["plugin_source"];
        if (!string.IsNullOrEmpty(src)) props["plugin_source"] = "(" + src.Length + " characters, not shown)";
    }

    // plugin_members: what the search for Designer's plugin list saw, for adapting the names here
    public static JObject Members()
    {
        Ensure();
        var types = new JObject();
        foreach (var t in PluginTypes().OrderBy(x => x.FullName)) types[t.FullName] = new JArray(MemberLines(t, 150));
        var app = new JArray();
        try { foreach (var m in MembersOf(Q.T("QSC.QSys.App"), true, false)) app.Add(m.Name + ": " + TypeName(MemberType(m))); }
        catch (Exception ex) { app.Add(Inner(ex).Message); }
        var res = new JObject {
            { "assemblies", new JArray(Assemblies().Select(a => a.GetName().Name)) },
            { "plugin_types", types },
            { "plugin_list_candidates", new JArray(_sources.Select(s => s.Label)) },
            { "plugins_from", _from },
            { "designer_plugins", new JArray(_entries.Select(e => e.ToJson())) },
            { "plugin_files", new JArray(_files.Select(e => e.ToJson())) },
            { "plugin_folders", new JArray(Folders()) },
            { "app_statics", app },
            { "trace", new JArray(_trace) } };
        var cc = TryT("QSC.QSys.Drag.ComponentCreator`1");
        if (cc != null) res["component_creator"] = new JArray(MemberLines(cc, 150));
        var helper = TryT("QSC.QSys.Design.ComponentHelper");
        if (helper != null) res["component_helper"] = new JArray(MemberLines(helper, 1000).Where(l => Mentions(l) || l.Contains("Create") || l.Contains("Update")));
        return res;
    }

    // ---- Designer's plugin list ----------------------------------------------------------------
    static void Ensure()
    {
        if (_ready) return;
        _ready = true;
        _files = ScanFiles();
        _sources = FindSources();
        Reload();
        if (_entries.Count == 0) RunLoaders(0);
        Fill();
    }

    // what plugins are matched against: Designer's list, or the plugin files when it wasn't found
    static List<Entry> Pool() { return _entries.Count > 0 ? _entries : _files; }

    static void Reload()
    {
        long best = Rank(_entries);
        foreach (var s in _sources)
        {
            List<Entry> found;
            try { found = Entries(Unwrap(Value(s.Member, s.Owner))); }
            catch (Exception ex) { _trace.Add(s.Label + ": " + Inner(ex).Message); continue; }
            var rank = Rank(found);
            if (rank > best) { _entries = found; _from = s.Label; best = rank; }
        }
    }

    // How much a candidate list looks like Designer's plugin list: plugins a plugin file also has,
    // then plugins with an id, then size. A list where nothing has an id or a known name is not it.
    static long Rank(List<Entry> list)
    {
        long known = list.Count(e => _files.Any(f => Same(f.Id, e.Id) || (e.Id == null && Names(f).Any(n => Same(n, e.Name)))));
        long ids = list.Count(e => e.Id != null);
        return known == 0 && ids == 0 ? 0 : known * 1000000L + ids * 1000L + list.Count;
    }

    // Members that may hold the list: static members anywhere in Designer that mention plugins
    // (by name or type), and the collections inside a plugin manager kept in such a member.
    static List<Source> FindSources()
    {
        var list = new List<Source>();
        foreach (var t in AllTypes())
        {
            if (t.ContainsGenericParameters || t.IsInterface || t.IsEnum) continue;
            foreach (var m in MembersOf(t, true, true))
            {
                var mt = MemberType(m);
                if (mt == null || mt == typeof(string) || mt.IsPrimitive || mt.IsEnum || IsPlumbing(mt)) continue;
                var el = ElementType(Held(mt));
                if (!Mentions(m.Name) && !Mentions(mt.FullName) && (el == null || !Mentions(el.FullName))) continue;
                var label = t.FullName + "." + m.Name;
                if (el != null) { list.Add(new Source { Member = m, Label = label }); continue; }
                var owner = Unwrap(SafeValue(m, null)) ?? Instantiate(m, label);
                if (owner != null)
                    list.AddRange(InstanceSources(owner, label).Where(s => !list.Any(x => x.Owner == s.Owner && x.Member == s.Member)));
            }
        }
        _trace.Add(list.Count + " places may hold Designer's plugin list: " + string.Join(", ", list.Select(s => s.Label).Take(40)));
        return list;
    }

    static List<Source> InstanceSources(object owner, string label)
    {
        var list = new List<Source>();
        for (var t = owner.GetType(); t != null && t != typeof(object); t = t.BaseType)
            foreach (var m in MembersOf(t, false, true))
            {
                var mt = MemberType(m);
                if (mt == null || mt == typeof(string) || IsPlumbing(mt)) continue;
                var el = ElementType(Held(mt));
                if (el != null && (Mentions(m.Name) || Mentions(el.FullName)) && !list.Any(s => s.Member.Name == m.Name))
                    list.Add(new Source { Owner = owner, Member = m, Label = label + "." + m.Name });
            }
        return list;
    }

    static Type Held(Type t) { return t.IsGenericType && t.GetGenericTypeDefinition() == typeof(Lazy<>) ? t.GetGenericArguments()[0] : t; }

    // what a Lazy<T> holds (a lazily made singleton or list); anything else as it is
    static object Unwrap(object o)
    {
        if (o == null || !o.GetType().IsGenericType || o.GetType().GetGenericTypeDefinition() != typeof(Lazy<>)) return o;
        try { return o.GetType().GetProperty("Value").GetValue(o, null); }
        catch (Exception ex) { _trace.Add("Lazy<" + TypeName(o.GetType().GetGenericArguments()[0]) + ">: " + Inner(ex).Message); return null; }
    }

    // A plugin manager kept in an empty static member (Designer's startup would have created it):
    // created the way NewDocument creates App.Manager.
    static object Instantiate(MemberInfo m, string label)
    {
        var t = MemberType(m);
        var f = m as FieldInfo; var p = m as PropertyInfo;
        if ((f == null || f.IsInitOnly) && (p == null || p.GetSetMethod(true) == null)) return null;
        if (t.IsAbstract || t.IsInterface || !Mentions(t.Name) || !ManagerWords.Any(w => t.Name.Contains(w))
            || t.GetConstructor(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic, null, Type.EmptyTypes, null) == null) return null;
        try
        {
            var o = Activator.CreateInstance(t, true);
            if (f != null) f.SetValue(null, o); else p.SetValue(null, o, null);
            _trace.Add(label + " was empty: created a " + t.Name);
            return o;
        }
        catch (Exception ex) { _trace.Add(label + ": could not create a " + t.Name + ": " + Inner(ex).Message); return null; }
    }

    // Designer may only fill its list when asked: call its loader (static, or on the manager),
    // parameterless or with a plugin folder, until the list has more than `have` plugins.
    static void RunLoaders(int have)
    {
        if (_loadersRun) return;
        _loadersRun = true;
        bool called = false;
        foreach (var target in Targets())
        {
            var t = target as Type ?? target.GetType();
            var inst = target is Type ? null : target;
            var names = Mentions(t.Name) ? LoaderNames.Concat(GenericLoaderNames) : LoaderNames;
            foreach (var name in names)
                foreach (var mi in SafeMethods(t, inst == null ? BindingFlags.Static : BindingFlags.Static | BindingFlags.Instance))
                {
                    if (mi.Name != name || mi.IsGenericMethodDefinition) continue;
                    var ps = mi.GetParameters();
                    var argSets = new List<object[]>();
                    if (ps.Length == 0) argSets.Add(new object[0]);
                    else if (ps.Length == 1 && ps[0].ParameterType == typeof(string)) foreach (var dir in Folders()) argSets.Add(new object[] { dir });
                    foreach (var args in argSets)
                    {
                        var label = t.FullName + "." + name + "(" + (args.Length > 0 ? "\"" + args[0] + "\"" : "") + ")";
                        try { Settle(mi.Invoke(mi.IsStatic ? null : inst, args)); called = true; _trace.Add("called " + label); }
                        catch (Exception ex) { _trace.Add(label + ": " + Inner(ex).Message); continue; }
                        Reload();
                        if (_entries.Count > have) return;
                    }
                }
        }
        // a loader may finish on another thread or on the dispatcher
        for (int i = 0; called && i < 40 && _entries.Count <= have; i++) { Pump(); System.Threading.Thread.Sleep(50); Reload(); }
    }

    // where loaders may be: the owners of the candidate lists, and plugin manager types
    static List<object> Targets()
    {
        var list = new List<object>();
        foreach (var s in _sources)
        {
            var t = s.Owner ?? s.Member.DeclaringType;
            if (!list.Contains(t)) list.Add(t);
        }
        foreach (var t in PluginTypes())
            if (!t.ContainsGenericParameters && !t.IsInterface && ManagerWords.Any(w => t.Name.Contains(w)) && !list.Contains(t)) list.Add(t);
        return list;
    }

    static void Pump()
    {
        try
        {
            System.Windows.Threading.Dispatcher.CurrentDispatcher.Invoke(System.Windows.Threading.DispatcherPriority.ApplicationIdle, new Action(delegate { }));
        }
        catch { }
    }

    // waits for a loader that returned a Task, and lets queued dispatcher work run
    static void Settle(object result)
    {
        var task = result as System.Threading.Tasks.Task;
        for (int i = 0; task != null && !task.IsCompleted && i < 600; i++) { Pump(); System.Threading.Thread.Sleep(50); }
        Pump();
    }

    static List<Entry> Entries(object coll)
    {
        var list = new List<Entry>();
        foreach (var x in Items(coll))
        {
            Entry e;
            if (x is string) e = FromFile((string)x);
            else if (x is FileInfo) e = FromFile(((FileInfo)x).FullName);
            else e = FromNative(x);
            if (e != null && !list.Any(y => y.Key == e.Key)) list.Add(e);
        }
        return list;
    }

    static Entry FromNative(object o)
    {
        var e = new Entry { Native = o };
        var places = new List<object> { o };
        foreach (var n in InfoNames)
        {
            var sub = Member(o, n);
            if (sub != null && !(sub is string) && !sub.GetType().IsPrimitive) places.Add(sub);
        }
        foreach (var p in places)
        {
            if (e.Id == null) e.Id = Text(p, IdNames);
            if (e.Name == null) e.Name = Text(p, NameNames);
            if (e.Version == null) e.Version = Text(p, VersionNames);
            if (e.File == null) e.File = PathText(p, FileNames);
            var enc = Member(p, "IsEncrypted") ?? Member(p, "Encrypted");
            if (enc is bool && (bool)enc) e.Encrypted = true;
        }
        return e.Id == null && e.Name == null ? null : e;
    }

    // What Designer's list leaves out, from the plugin files: an entry's file, id, name and version
    // (by its file, else by id, else by name), and which files the list lacks.
    static void Fill()
    {
        foreach (var e in _entries)
        {
            var f = _files.FirstOrDefault(x => SamePath(x.File, e.File))
                ?? _files.FirstOrDefault(x => (e.Id != null ? Same(x.Id, e.Id) : Names(x).Any(n => Same(n, e.Name))) && SameVersion(x.Version, e.Version));
            if (f == null) continue;
            if (e.File == null) e.File = f.File;
            if (e.Id == null) e.Id = f.Id;
            if (e.Name == null) e.Name = f.Name;
            if (e.Version == null) e.Version = f.Version;
            if (f.Name != null && !Same(f.Name, e.Name) && !e.Aliases.Any(a => Same(a, f.Name))) e.Aliases.Add(f.Name);
        }
        foreach (var f in _files)
            f.Loaded = _entries.Count == 0 || f.Id == null || _entries.Any(e => SamePath(e.File, f.File) || (Same(e.Id, f.Id) && SameVersion(e.Version, f.Version)));
    }

    // the same version, or one of them unknown
    static bool SameVersion(string a, string b) { return a == null || b == null || CompareVersions(a, b) == 0; }

    // ---- plugin folders and files ----------------------------------------------------------------
    static List<string> _folders;

    // The user plugin folder (and Asset Manager's), plus any folder Designer names itself: static
    // string members of its plugin types, and plugin or asset folders in its settings.
    static List<string> Folders()
    {
        if (_folders != null) return _folders;
        _folders = new List<string>();
        var qsc = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "QSC", "Q-Sys Designer");
        AddFolder(Path.Combine(qsc, "Plugins"));
        AddFolder(Path.Combine(qsc, "Assets"));
        foreach (var t in PluginTypes())
        {
            if (t.ContainsGenericParameters) continue;
            foreach (var m in MembersOf(t, true, false))
            {
                var mt = MemberType(m);
                if (mt == typeof(string)) AddFolder(SafeValue(m, null) as string);
                else if (ElementType(mt) == typeof(string)) foreach (var x in Items(SafeValue(m, null))) AddFolder(x as string);
            }
        }
        foreach (var d in SettingsFolders()) AddFolder(d);
        return _folders;
    }

    static void AddFolder(string dir)
    {
        if (string.IsNullOrEmpty(dir)) return;
        try
        {
            if (!Directory.Exists(dir)) return;
            dir = Path.GetFullPath(dir).TrimEnd('\\', '/');
        }
        catch { return; }
        if (!_folders.Any(f => string.Equals(f, dir, StringComparison.OrdinalIgnoreCase))) _folders.Add(dir);
    }

    // folders in Designer's settings (QSC.Common.SettingsManager) whose key names plugins or assets
    // (not a last-used or export folder)
    static List<string> SettingsFolders()
    {
        var list = new List<string>();
        try
        {
            var sm = AllTypes().FirstOrDefault(t => t.FullName == "QSC.Common.SettingsManager");
            var data = sm == null ? null : (StaticMember(sm, "data") ?? StaticMember(sm, "Data"));
            if (data == null) return list;
            var colls = new List<object> { data };
            for (var t = data.GetType(); t != null && t != typeof(object); t = t.BaseType)
                foreach (var m in MembersOf(t, false, false)) colls.Add(SafeValue(m, data));
            foreach (var coll in colls)
                foreach (var item in Items(coll))
                {
                    var key = Text(item, new[] { "key", "Key", "Name" });
                    var val = Text(item, new[] { "data", "Data", "Value", "value" });
                    if (key == null || val == null || Regex.IsMatch(key, "last|recent|export|backup", RegexOptions.IgnoreCase)) continue;
                    if (Mentions(key) || key.IndexOf("asset", StringComparison.OrdinalIgnoreCase) >= 0) list.Add(val);
                }
        }
        catch (Exception ex) { _trace.Add("settings: " + Inner(ex).Message); }
        return list;
    }

    static List<Entry> ScanFiles()
    {
        var list = new List<Entry>();
        foreach (var dir in Folders())
        {
            string[] paths;
            try { paths = Directory.GetFiles(dir, "*.qplug*", SearchOption.AllDirectories); }
            catch (Exception ex) { _trace.Add("scan " + dir + ": " + ex.Message); continue; }
            foreach (var p in paths.OrderBy(x => x, StringComparer.OrdinalIgnoreCase))
            {
                if (!Path.GetExtension(p).StartsWith(".qplug", StringComparison.OrdinalIgnoreCase) || list.Any(x => SamePath(x.File, p))) continue;
                var e = FromFile(p);
                if (e != null) list.Add(e);
            }
        }
        return list;
    }

    // name, id and version from a plugin file's PluginInfo; an encrypted file (.qplugx, JSON) only
    // gives its path, since only Designer can read it
    static Entry FromFile(string path)
    {
        string text;
        try
        {
            if (!File.Exists(path)) return null;
            text = File.ReadAllText(path);
        }
        catch { return null; }
        var e = new Entry { File = Path.GetFullPath(path) };
        if (text.TrimStart().StartsWith("{")) e.Encrypted = true;
        else { e.Name = LuaInfo(text, "Name"); e.Id = LuaInfo(text, "Id"); e.Version = LuaVersion(text); }
        return e;
    }

    // ---- matching a spec to an installed plugin --------------------------------------------------
    static Entry Resolve(JToken spec, string where, JArray warnings)
    {
        string name = null, id = null, version = null;
        if (spec != null && spec.Type == JTokenType.String) name = (string)spec;
        else if (spec != null && spec.Type == JTokenType.Object) { name = (string)spec["name"]; id = (string)spec["id"]; version = (string)spec["version"]; }
        if (string.IsNullOrEmpty(id)) id = null;
        if (string.IsNullOrEmpty(version)) version = null;
        if (string.IsNullOrEmpty(name) && id == null)
            throw new Exception(where + ": plugin must be the plugin's name or id, or {\"name\", \"id\", \"version\"}");
        Ensure();
        var hits = Match(Pool(), name, id, version);
        if (hits.Count == 0 && _entries.Count > 0)
        {
            // Designer's list may hold only what it loads at startup: ask it to load the rest
            RunLoaders(_entries.Count);
            Fill();
            hits = Match(Pool(), name, id, version);
        }
        if (hits.Count == 0) throw new Exception(where + ": " + Missing(name, id, version));
        if (hits.Select(e => (e.Id ?? "").ToLowerInvariant()).Distinct().Count() > 1)
            throw new Exception(where + ": plugin '" + (id ?? name) + "' matches several plugins: " + string.Join("; ", hits.Select(e => e.ToString())) + ". Give its id.");
        var best = hits[0];
        foreach (var e in hits) if (CompareVersions(e.Version, best.Version) > 0) best = e;
        if (hits.Count > 1)
            warnings.Add(where + ": several copies of " + best.Name + " are installed (" + string.Join(", ", hits.Select(e => e.Version ?? "?")) +
                "); using " + (best.Version ?? "the first") + ". Give \"version\" to choose.");
        return best;
    }

    // by id (or class name); else by name: full ("Folder~Name"), without its folder, then also
    // without a trailing version ("Name v1.2"). version "1.4" matches 1.4.x.
    static List<Entry> Match(List<Entry> pool, string name, string id, string version)
    {
        var hits = new List<Entry>();
        if (id != null) hits = pool.Where(e => Same(e.Id, id) || Same(e.Class, id)).ToList();
        else
        {
            var steps = new Func<Entry, bool>[] {
                e => Same(e.Id, name) || Same(e.Class, name),
                e => Names(e).Any(n => Same(n, name)),
                e => Names(e).Any(n => Same(Short(n), Short(name))),
                e => Names(e).Any(n => Same(Bare(n), Bare(name))) };
            foreach (var step in steps)
            {
                hits = pool.Where(step).ToList();
                if (hits.Count > 0) break;
            }
        }
        if (version != null) hits = hits.Where(e => VersionMatches(version, e.Version)).ToList();
        return hits;
    }

    static IEnumerable<string> Names(Entry e) { return new[] { e.Name }.Concat(e.Aliases); }

    static string Missing(string name, string id, string version)
    {
        var what = "no installed plugin matches " + (id != null ? "id '" + id + "'" : "'" + name + "'") + (version != null ? " version " + version : "");
        var pool = Pool();
        if (pool.Count == 0)
            return what + ". No plugins were found (" + (_from == null ? "Designer's own plugin list was not found; " : "") + "plugin folders: " +
                (Folders().Count > 0 ? string.Join(", ", Folders()) : "none") + ").";
        var text = what + ". Installed: " + string.Join("; ", pool.Take(80).Select(e => e.ToString())) +
            (pool.Count > 80 ? "; and " + (pool.Count - 80) + " more (designer_versions lists them all)" : "") + ".";
        if (_entries.Count > 0)
        {
            var f = Match(_files, name, id, version).FirstOrDefault();
            var listed = f == null ? null : _entries.FirstOrDefault(e => Same(e.Id, f.Id));
            if (f != null) text += " " + f.File + (listed == null ? " has it, but Designer's plugin list does not." : " has it; Designer lists it as " + listed + ".");
        }
        if (pool.Any(e => e.Encrypted && e.Id == null)) text += " Encrypted plugin files only show their names once Designer's own plugin list is found.";
        return text;
    }

    static string Short(string name)
    {
        if (name == null) return null;
        var i = name.LastIndexOf('~');
        return i < 0 ? name : name.Substring(i + 1);
    }

    static string Bare(string name)
    {
        var s = Short(name);
        return s == null ? null : Regex.Replace(s, @"[\s_-]*\(?v?\d+(\.\d+)+\)?\s*$", "", RegexOptions.IgnoreCase).Trim();
    }

    static bool Same(string a, string b)
    {
        return !string.IsNullOrEmpty(a) && !string.IsNullOrEmpty(b) && string.Equals(a.Trim(), b.Trim(), StringComparison.OrdinalIgnoreCase);
    }

    static bool SamePath(string a, string b)
    {
        if (string.IsNullOrEmpty(a) || string.IsNullOrEmpty(b)) return false;
        try { return string.Equals(Path.GetFullPath(a), Path.GetFullPath(b), StringComparison.OrdinalIgnoreCase); }
        catch { return string.Equals(a, b, StringComparison.OrdinalIgnoreCase); }
    }

    internal static int[] VersionParts(string v)
    {
        if (v == null) return new int[0];
        return Regex.Matches(v, @"\d+").Cast<Match>().Select(m => { int n; return int.TryParse(m.Value, out n) ? n : int.MaxValue; }).ToArray();
    }

    internal static int CompareVersions(string a, string b)
    {
        var x = VersionParts(a); var y = VersionParts(b);
        for (int i = 0; i < Math.Max(x.Length, y.Length); i++)
        {
            int p = i < x.Length ? x[i] : 0, q = i < y.Length ? y[i] : 0;
            if (p != q) return p.CompareTo(q);
        }
        return 0;
    }

    internal static bool VersionMatches(string want, string have)
    {
        if (Same(want, have)) return true;
        var w = VersionParts(want); var h = VersionParts(have);
        if (w.Length == 0 || h.Length == 0) return false;
        for (int i = 0; i < w.Length; i++) if (w[i] != (i < h.Length ? h[i] : 0)) return false;
        return true;
    }

    // ---- placing a plugin ----------------------------------------------------------------------
    static Type ComponentT { get { return Q.T("QSC.QSys.Design.Component"); } }

    // Tries Designer's ways of making a plugin block, best first. A block counts when it is a
    // Component with the plugin's class name; one whose plugin_source stayed empty is kept only as
    // a last resort, since the plugin's code would be missing from the design.
    static object Place(Entry e, string where, JArray warnings)
    {
        var tried = new List<string>();
        object partial = null; string partialLabel = null;
        foreach (var a in Attempts(e))
        {
            object c;
            try { c = a.Run(); }
            catch (Exception ex) { ex = Inner(ex); tried.Add(a.Label + ": " + ex.GetType().Name + ": " + ex.Message); continue; }
            var score = Score(c, e);
            if (score == 2) { _trace.Add("placed " + e + " with " + a.Label); return c; }
            if (score == 1 && partial == null) { partial = c; partialLabel = a.Label; }
            tried.Add(a.Label + ": " + (score == 1 ? "a plugin block with an empty plugin_source" : "gave " + Show(c)));
        }
        if (partial != null)
        {
            warnings.Add(where + ": " + partialLabel + " placed " + e.Class + " but left plugin_source empty, so the plugin's code is not in the design");
            return partial;
        }
        throw new Exception(where + ": could not place plugin " + e + " through Designer. Tried:\n- " +
            (tried.Count > 0 ? string.Join("\n- ", tried) : "nothing applicable") + "\n" + Dump(e));
    }

    static IEnumerable<Attempt> Attempts(Entry e)
    {
        var comp = ComponentT;
        if (e.Native != null)
        {
            // 1. a creator Designer's plugin object carries (as inventory placeables carry theirs)
            for (var t = e.Native.GetType(); t != null && t != typeof(object); t = t.BaseType)
                foreach (var m in MembersOf(t, false, false))
                {
                    var mt = MemberType(m);
                    if (mt == null || mt.Name.IndexOf("Creator", StringComparison.OrdinalIgnoreCase) < 0) continue;
                    var creator = SafeValue(m, e.Native);
                    if (creator == null) continue;
                    foreach (var a in CreatorAttempts(creator.GetType(), () => creator, e, "plugin." + m.Name, false)) yield return a;
                }
            // 2. a factory method on the plugin object
            foreach (var mi in SafeMethods(e.Native.GetType(), BindingFlags.Instance))
            {
                var args = IsFactory(mi) ? ArgsFor(mi, e) : null;
                if (args == null) continue;
                var m = mi;
                yield return new Attempt { Label = "plugin." + m.Name + "(" + Params(m) + ")", Run = () => m.Invoke(e.Native, args) };
            }
        }
        // 3. a plugin creator or factory type
        foreach (var pt in PluginTypes())
        {
            if (pt.IsAbstract || pt.IsInterface || typeof(System.Windows.DependencyObject).IsAssignableFrom(pt)
                || (pt.Name.IndexOf("Creator", StringComparison.OrdinalIgnoreCase) < 0 && pt.Name.IndexOf("Factory", StringComparison.OrdinalIgnoreCase) < 0)) continue;
            var ct = Close(pt, comp);
            if (ct == null) continue;
            foreach (var ctor in ct.GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
            {
                var cargs = ArgsFor(ctor, e);
                if (cargs == null) continue;
                var k = ctor;
                foreach (var a in CreatorAttempts(ct, () => k.Invoke(cargs), e, TypeName(ct), true)) yield return a;
            }
        }
        // 4. Designer's ComponentCreator by class name, as for any class (with the plugin set on it
        //    if it has a property for one)
        if (e.Class != null)
        {
            var cct = TryT("QSC.QSys.Drag.ComponentCreator`1");
            var cc = cct == null ? null : Close(cct, comp);
            if (cc != null)
                foreach (var a in CreatorAttempts(cc, () => Activator.CreateInstance(cc, true), e, "ComponentCreator<Component>(ClassName=" + e.Class + ")", true))
                    yield return a;
        }
        // 5. static factories on plugin types and ComponentHelper, and factories on the plugin manager
        foreach (var t in PluginTypes().Concat(new[] { TryT("QSC.QSys.Design.ComponentHelper") }).Distinct())
        {
            if (t == null || t.ContainsGenericParameters) continue;
            foreach (var mi in SafeMethods(t, BindingFlags.Static))
            {
                var args = IsFactory(mi) && mi.GetParameters().Length == 1 ? ArgsFor(mi, e) : null;
                if (args == null) continue;
                var m = mi;
                yield return new Attempt { Label = t.FullName + "." + m.Name + "(" + Params(m) + ")", Run = () => m.Invoke(null, args) };
            }
        }
        foreach (var o in Targets().Where(x => !(x is Type)))
            foreach (var mi in SafeMethods(o.GetType(), BindingFlags.Instance))
            {
                var args = IsFactory(mi) && mi.GetParameters().Length == 1 ? ArgsFor(mi, e) : null;
                if (args == null) continue;
                var m = mi; var target = o;
                yield return new Attempt { Label = o.GetType().FullName + "." + m.Name + "(" + Params(m) + ")", Run = () => m.Invoke(target, args) };
            }
    }

    static IEnumerable<Attempt> CreatorAttempts(Type ct, Func<object> make, Entry e, string label, bool inject)
    {
        foreach (var mi in SafeMethods(ct, BindingFlags.Instance))
        {
            if (mi.IsGenericMethodDefinition || !(mi.Name == "Create" || mi.Name == "GetComponent" || mi.Name == "CreateComponent")) continue;
            var args = ArgsFor(mi, e);
            if (args == null) continue;
            var m = mi;
            yield return new Attempt { Label = label + "." + m.Name + "(" + Params(m) + ")", Run = () =>
            {
                var creator = make();
                if (inject) Inject(creator, e);
                return m.Invoke(creator, args);
            } };
        }
    }

    // gives a creator we made the plugin (any property that takes Designer's plugin object) and the class name
    static void Inject(object creator, Entry e)
    {
        for (var t = creator.GetType(); t != null && t != typeof(object); t = t.BaseType)
            foreach (var p in t.GetProperties(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
            {
                if (!p.CanWrite || p.GetIndexParameters().Length > 0) continue;
                try
                {
                    if (e.Native != null && p.PropertyType != typeof(object) && p.PropertyType.IsInstanceOfType(e.Native)) p.SetValue(creator, e.Native, null);
                    else if (p.Name == "ClassName" && p.PropertyType == typeof(string) && e.Class != null) p.SetValue(creator, e.Class, null);
                }
                catch (Exception ex) { _trace.Add("set " + t.Name + "." + p.Name + ": " + Inner(ex).Message); }
            }
    }

    // arguments for a creator or factory taking nothing, a position, Designer's plugin object, or one
    // string (a file path, id, class name or name, going by the parameter's name); null if it takes more
    static object[] ArgsFor(MethodBase mi, Entry e)
    {
        var ps = mi.GetParameters();
        if (ps.Length == 0) return new object[0];
        if (ps.Length != 1) return null;
        var pt = ps[0].ParameterType;
        if (pt == typeof(System.Windows.Point)) return new object[] { new System.Windows.Point(0, 0) };
        if (e.Native != null && pt != typeof(object) && pt.IsInstanceOfType(e.Native)) return new[] { e.Native };
        if (pt != typeof(string)) return null;
        var n = (ps[0].Name ?? "").ToLowerInvariant();
        string v;
        if (n.Contains("path") || n.Contains("file")) v = e.File;
        else if (n.Contains("class")) v = e.Class;
        else if (n == "id" || n.EndsWith("id") || n.Contains("guid")) v = e.Id;
        else if (n.Contains("name")) v = e.Name;
        else v = e.Class;
        return v == null ? null : new object[] { v };
    }

    static bool IsFactory(MethodInfo mi)
    {
        if (mi.IsSpecialName || mi.IsGenericMethodDefinition || !FactoryWords.Any(w => mi.Name.Contains(w))) return false;
        var rt = mi.ReturnType; var comp = ComponentT;
        return comp.IsAssignableFrom(rt) || (rt.FullName != null && rt.FullName.StartsWith("QSC.") && rt.IsAssignableFrom(comp));
    }

    static Type Close(Type t, Type arg)
    {
        if (!t.IsGenericTypeDefinition) return t;
        if (t.GetGenericArguments().Length != 1) return null;
        try { return t.MakeGenericType(arg); } catch { return null; }
    }

    // 2: a plugin block with its code; 1: a plugin block whose plugin_source is empty; 0: not one
    static int Score(object c, Entry e)
    {
        if (c == null || !ComponentT.IsInstanceOfType(c)) return 0;
        var cls = Convert.ToString(Q.TryGet(c, "ClassName"));
        if (!cls.StartsWith(Prefix, StringComparison.Ordinal)) return 0;
        if (e.Class != null && !string.Equals(cls, e.Class, StringComparison.OrdinalIgnoreCase)) return 0;
        return string.IsNullOrEmpty(Prop(c, "plugin_source")) ? 1 : 2;
    }

    static string Show(object c)
    {
        if (c == null) return "null";
        var cls = Q.TryGet(c, "ClassName");
        return c.GetType().FullName + (cls != null ? " (ClassName " + cls + ")" : "");
    }

    // the members a failed placement may need, for adapting the names here
    static string Dump(Entry e)
    {
        var types = new List<Type>();
        if (e.Native != null) types.Add(e.Native.GetType());
        foreach (var t in Targets()) types.Add(t as Type ?? t.GetType());
        types.AddRange(PluginTypes().Where(t => t.Name.Contains("Creator") || t.Name.Contains("Factory")));
        types.Add(TryT("QSC.QSys.Drag.ComponentCreator`1"));
        var sb = new StringBuilder("Designer members (the helper's plugin_members command lists more):");
        foreach (var t in types.Where(x => x != null).Distinct().Take(12)) sb.Append("\n" + t.FullName + ": " + string.Join("; ", MemberLines(t, 40)));
        if (_entries.Count == 0)
        {
            var names = PluginTypes().Select(t => t.FullName).Take(80).ToList();
            sb.Append("\nDesigner's plugin list was not found. Types that mention plugins: " + (names.Count > 0 ? string.Join(", ", names) : "none"));
        }
        return sb.ToString();
    }

    // ---- properties ------------------------------------------------------------------------------
    // One at a time, refreshing after each: a plugin can change its other properties, pins and
    // controls from one property (a model or channel count, say).
    static void Apply(object c, JObject props, string where, JArray warnings)
    {
        foreach (var kv in props ?? new JObject())
        {
            if (Array.IndexOf(SetByDesigner, kv.Key) >= 0) throw new Exception(where + ": " + kv.Key + " comes from the installed plugin and can't be set");
            var key = PropName(c, kv.Key);
            if (key == null) { Q.Refresh(c); key = PropName(c, kv.Key); }
            if (key == null)
                throw new Exception(where + ": the plugin has no property '" + kv.Key + "'; it has: " +
                    string.Join(", ", Q.Props(c).Select(t => t.Item1).Where(n => Array.IndexOf(SetByDesigner, n) < 0)));
            var value = QsysHelper.Scalar(kv.Value);
            Q.SetProp(c, key, value);
            Q.Refresh(c);
            var now = Prop(c, key);
            if (!SameValue(now, value))
                warnings.Add(where + ": property '" + key + "' is '" + now + "' after setting it to '" + Convert.ToString(value) + "' (the plugin rejected or changed it)");
        }
        Q.Refresh(c);
    }

    static string PropName(object c, string key)
    {
        var names = Q.Props(c).Select(t => t.Item1).ToList();
        return names.FirstOrDefault(n => n == key) ?? names.FirstOrDefault(n => string.Equals(n, key, StringComparison.OrdinalIgnoreCase));
    }

    static string Prop(object c, string codeName)
    {
        try
        {
            var hit = Q.Props(c).FirstOrDefault(t => t.Item1 == codeName);
            return hit == null ? null : hit.Item2;
        }
        catch { return null; }
    }

    // Q.Props formats values with the current culture, so compare in it
    static bool SameValue(string now, object value)
    {
        var want = Convert.ToString(value);
        if (string.Equals(now, want, StringComparison.OrdinalIgnoreCase)) return true;
        double a, b;
        return double.TryParse(now, NumberStyles.Float, CultureInfo.CurrentCulture, out a)
            && double.TryParse(want, NumberStyles.Float, CultureInfo.CurrentCulture, out b) && Math.Abs(a - b) < 1e-6;
    }

    // ---- PluginInfo from Lua source -----------------------------------------------------------
    internal static string LuaVersion(string src) { return LuaInfo(src, "Version") ?? LuaInfo(src, "BuildVersion"); }

    // PluginInfo.<key> from a plugin's code, without running it: string literals, numbers, names of
    // top-level string variables (Version = BuildVersion) and '..' between them; null otherwise.
    internal static string LuaInfo(string src, string key)
    {
        if (string.IsNullOrEmpty(src)) return null;
        var m = Regex.Match(src, @"(?m)^[ \t]*PluginInfo[ \t]*=[ \t]*\{");
        if (!m.Success) return null;
        var toks = LuaTable(src, m.Index + m.Length);
        for (int i = 0; i + 2 < toks.Count; i++)
        {
            if (toks[i].Depth != 0 || toks[i].Kind != 'n' || toks[i].Text != key || toks[i + 1].Text != "=") continue;
            if (i > 0 && toks[i - 1].Text != "," && toks[i - 1].Text != ";") continue;
            var sb = new StringBuilder();
            for (int j = i + 2; j < toks.Count; j++)
            {
                var t = toks[j];
                if (t.Kind == 'p' && t.Depth == 0 && (t.Text == "," || t.Text == ";")) break;
                if (t.Kind == 's' || t.Kind == 'd') sb.Append(t.Text);
                else if (t.Kind == 'n')
                {
                    var g = LuaGlobal(src, t.Text);
                    if (g == null) return null;
                    sb.Append(g);
                }
                else if (t.Text != "..") return null;
            }
            return sb.ToString();
        }
        return null;
    }

    class Tok { public char Kind; public string Text; public int Depth; }   // Kind: n name, s string, d number, p punctuation

    // the tokens of a Lua table constructor's body, from just after its '{' to its closing '}'
    static List<Tok> LuaTable(string s, int i)
    {
        var toks = new List<Tok>();
        int depth = 0;
        while (i < s.Length && toks.Count < 4000)
        {
            char c = s[i];
            if (char.IsWhiteSpace(c)) { i++; continue; }
            if (c == '-' && i + 1 < s.Length && s[i + 1] == '-')
            {
                i += 2;
                int level = LongBracket(s, i);
                if (level >= 0) { int close = LongClose(s, i + level + 2, level); i = close < 0 ? s.Length : close + level + 2; }
                else while (i < s.Length && s[i] != '\n') i++;
                continue;
            }
            if (c == '"' || c == '\'')
            {
                var sb = new StringBuilder();
                i++;
                while (i < s.Length && s[i] != c && s[i] != '\n')
                {
                    if (s[i] == '\\' && i + 1 < s.Length) { i++; sb.Append(Unescape(s[i])); }
                    else sb.Append(s[i]);
                    i++;
                }
                i++;
                toks.Add(new Tok { Kind = 's', Text = sb.ToString(), Depth = depth });
                continue;
            }
            if (c == '[')
            {
                int level = LongBracket(s, i);
                if (level >= 0)
                {
                    int open = i + level + 2, close = LongClose(s, open, level);
                    if (close < 0) close = s.Length;
                    toks.Add(new Tok { Kind = 's', Text = s.Substring(open, close - open), Depth = depth });
                    i = Math.Min(s.Length, close + level + 2);
                    continue;
                }
            }
            if (char.IsLetter(c) || c == '_')
            {
                int start = i;
                while (i < s.Length && (char.IsLetterOrDigit(s[i]) || s[i] == '_')) i++;
                toks.Add(new Tok { Kind = 'n', Text = s.Substring(start, i - start), Depth = depth });
                continue;
            }
            if (char.IsDigit(c) || (c == '.' && i + 1 < s.Length && char.IsDigit(s[i + 1])))
            {
                int start = i;
                while (i < s.Length && (char.IsLetterOrDigit(s[i]) || (s[i] == '.' && !(i + 1 < s.Length && s[i + 1] == '.')))) i++;
                toks.Add(new Tok { Kind = 'd', Text = s.Substring(start, i - start), Depth = depth });
                continue;
            }
            if (c == '.' && i + 1 < s.Length && s[i + 1] == '.')
            {
                int n = i + 2 < s.Length && s[i + 2] == '.' ? 3 : 2;
                toks.Add(new Tok { Kind = 'p', Text = s.Substring(i, n), Depth = depth });
                i += n;
                continue;
            }
            if (c == '}' && depth == 0) break;
            if (c == '}') depth--;
            toks.Add(new Tok { Kind = 'p', Text = c.ToString(), Depth = depth });
            if (c == '{') depth++;
            i++;
        }
        return toks;
    }

    // s[i] opens a long bracket ("[[", "[=[", ...): its level (the number of '='), else -1
    static int LongBracket(string s, int i)
    {
        if (i >= s.Length || s[i] != '[') return -1;
        int j = i + 1;
        while (j < s.Length && s[j] == '=') j++;
        return j < s.Length && s[j] == '[' ? j - i - 1 : -1;
    }

    static int LongClose(string s, int from, int level)
    {
        return s.IndexOf("]" + new string('=', level) + "]", Math.Min(from, s.Length), StringComparison.Ordinal);
    }

    static char Unescape(char c) { return c == 'n' ? '\n' : c == 't' ? '\t' : c == 'r' ? '\r' : c; }

    // a top-level string variable, e.g. BuildVersion = "1.2.0.0"
    static string LuaGlobal(string src, string name)
    {
        var m = Regex.Match(src, @"(?m)^[ \t]*(?:local[ \t]+)?" + Regex.Escape(name) + @"[ \t]*=[ \t]*(?:""((?:[^""\\\r\n]|\\.)*)""|'((?:[^'\\\r\n]|\\.)*)')");
        if (!m.Success) return null;
        var raw = m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value;
        return Regex.Replace(raw, @"\\(.)", x => Unescape(x.Groups[1].Value[0]).ToString());
    }

    // ---- reflection ------------------------------------------------------------------------------
    static bool Mentions(string s)
    {
        return s != null && (s.IndexOf("plugin", StringComparison.OrdinalIgnoreCase) >= 0 || s.IndexOf("qplug", StringComparison.OrdinalIgnoreCase) >= 0);
    }

    static Exception Inner(Exception ex) { while (ex.InnerException != null) ex = ex.InnerException; return ex; }

    static Type TryT(string name) { try { return Q.T(name); } catch { return null; } }

    static List<Assembly> _asms;

    // Designer's own code, and the QSC assemblies next to it that it references or has loaded
    static List<Assembly> Assemblies()
    {
        if (_asms != null) return _asms;
        _asms = new List<Assembly> { Q.Asm };
        foreach (var an in Q.Asm.GetReferencedAssemblies())
        {
            if (!Regex.IsMatch(an.Name, "qsc|qsys|plugin", RegexOptions.IgnoreCase)) continue;
            if (!File.Exists(Path.Combine(Q.DesignerDir, an.Name + ".dll")) && !File.Exists(Path.Combine(Q.DesignerDir, an.Name + ".exe"))) continue;
            try
            {
                var a = Assembly.Load(an);
                if (!_asms.Contains(a)) _asms.Add(a);
            }
            catch (Exception ex) { _trace.Add("load " + an.Name + ": " + Inner(ex).Message); }
        }
        var home = Path.GetFullPath(Q.DesignerDir).TrimEnd('\\', '/');
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            if (_asms.Contains(a) || a.IsDynamic || !Regex.IsMatch(a.GetName().Name, "qsc|qsys|plugin", RegexOptions.IgnoreCase)) continue;
            try { if (string.Equals(Path.GetDirectoryName(a.Location), home, StringComparison.OrdinalIgnoreCase)) _asms.Add(a); }
            catch (Exception ex) { _trace.Add("assembly " + a.GetName().Name + ": " + Inner(ex).Message); }
        }
        return _asms;
    }

    static List<Type> _types;
    static List<Type> AllTypes()
    {
        if (_types != null) return _types;
        _types = new List<Type>();
        foreach (var a in Assemblies())
        {
            Type[] ts;
            try { ts = a.GetTypes(); }
            catch (ReflectionTypeLoadException ex) { ts = ex.Types.Where(x => x != null).ToArray(); }
            catch (Exception ex) { _trace.Add("types of " + a.GetName().Name + ": " + Inner(ex).Message); continue; }
            _types.AddRange(ts.Where(t => t.FullName != null && t.Name.IndexOf('<') < 0 && !IsHelper(t)));
        }
        return _types;
    }

    // the helper's own classes, never part of the search
    static bool IsHelper(Type t)
    {
        for (; t != null; t = t.DeclaringType)
            if (t == typeof(QPlugins) || t == typeof(Q) || t == typeof(QsysHelper)) return true;
        return false;
    }

    static IEnumerable<Type> PluginTypes() { return AllTypes().Where(t => Mentions(t.FullName)); }

    // readable properties and fields declared on t (and parameterless Get methods), static or
    // instance. Properties come first, so a lazy getter (Instance) makes its singleton itself.
    static List<MemberInfo> MembersOf(Type t, bool statics, bool getters)
    {
        var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly | (statics ? BindingFlags.Static : BindingFlags.Instance);
        var list = new List<MemberInfo>();
        try
        {
            list.AddRange(t.GetProperties(flags).Where(p => p.CanRead && p.GetIndexParameters().Length == 0));
            list.AddRange(t.GetFields(flags).Where(f => !f.IsLiteral && f.Name.IndexOf('<') < 0));
            if (getters)
                list.AddRange(t.GetMethods(flags).Where(m => m.GetParameters().Length == 0 && !m.IsSpecialName && !m.IsGenericMethodDefinition
                    && m.ReturnType != typeof(void) && m.Name.StartsWith("Get", StringComparison.Ordinal)));
        }
        catch (Exception ex) { _trace.Add("members of " + t.FullName + ": " + Inner(ex).Message); }
        return list;
    }

    static MethodInfo[] SafeMethods(Type t, BindingFlags scope)
    {
        try { return t.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | scope); }
        catch { return new MethodInfo[0]; }
    }

    static Type MemberType(MemberInfo m)
    {
        var f = m as FieldInfo; if (f != null) return f.FieldType;
        var p = m as PropertyInfo; if (p != null) return p.PropertyType;
        var mi = m as MethodInfo; return mi != null ? mi.ReturnType : null;
    }

    static bool IsStatic(PropertyInfo p)
    {
        var g = p.GetGetMethod(true) ?? p.GetSetMethod(true);
        return g != null && g.IsStatic;
    }

    static object Value(MemberInfo m, object owner)
    {
        var f = m as FieldInfo; if (f != null) return f.GetValue(f.IsStatic ? null : owner);
        var p = m as PropertyInfo; if (p != null) return p.GetValue(IsStatic(p) ? null : owner, null);
        var mi = (MethodInfo)m;
        return mi.Invoke(mi.IsStatic ? null : owner, null);
    }

    static object SafeValue(MemberInfo m, object owner)
    {
        try { return Value(m, owner); }
        catch (Exception ex) { _trace.Add("read " + m.DeclaringType.FullName + "." + m.Name + ": " + Inner(ex).Message); return null; }
    }

    static object StaticMember(Type t, string name)
    {
        var m = MembersOf(t, true, false).FirstOrDefault(x => x.Name == name);
        return m == null ? null : SafeValue(m, null);
    }

    // a named property or field of o (any visibility, inherited too), or null
    static object Member(object o, string name)
    {
        if (o == null) return null;
        for (var t = o.GetType(); t != null && t != typeof(object); t = t.BaseType)
        {
            try
            {
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly;
                var p = t.GetProperty(name, flags);
                if (p != null && p.CanRead && p.GetIndexParameters().Length == 0) return p.GetValue(o, null);
                var f = t.GetField(name, flags);
                if (f != null) return f.GetValue(o);
            }
            catch { return null; }
        }
        return null;
    }

    // the first of the named members that has a readable value (not just its type's name)
    static string Text(object o, string[] names)
    {
        foreach (var n in names)
        {
            var v = Member(o, n);
            if (v == null) continue;
            var s = Convert.ToString(v, CultureInfo.InvariantCulture);
            if (!string.IsNullOrEmpty(s) && s != v.GetType().ToString()) return s;
        }
        return null;
    }

    static string PathText(object o, string[] names)
    {
        foreach (var n in names)
        {
            var v = Member(o, n);
            var s = v is FileSystemInfo ? ((FileSystemInfo)v).FullName : v as string;
            if (!string.IsNullOrEmpty(s) && File.Exists(s)) return Path.GetFullPath(s);
        }
        return null;
    }

    // the element type of a collection type (a dictionary's value type), or null if it isn't one
    static Type ElementType(Type t)
    {
        if (t == null || t == typeof(string)) return null;
        if (t.IsArray) return t.GetElementType();
        var all = new[] { t }.Concat(t.GetInterfaces()).Where(i => i.IsGenericType).ToList();
        var dict = all.FirstOrDefault(i => i.GetGenericTypeDefinition() == typeof(IDictionary<,>));
        if (dict != null) return dict.GetGenericArguments()[1];
        var seq = all.FirstOrDefault(i => i.GetGenericTypeDefinition() == typeof(IEnumerable<>));
        if (seq != null)
        {
            var el = seq.GetGenericArguments()[0];
            return el.IsGenericType && el.GetGenericTypeDefinition() == typeof(KeyValuePair<,>) ? el.GetGenericArguments()[1] : el;
        }
        return typeof(IEnumerable).IsAssignableFrom(t) ? typeof(object) : null;
    }

    // a collection's items (a dictionary's values); empty if it isn't one
    static List<object> Items(object coll)
    {
        var list = new List<object>();
        if (coll == null || coll is string) return list;
        var dict = coll as IDictionary;
        var seq = dict != null ? dict.Values : coll as IEnumerable;
        if (seq == null) return list;
        try
        {
            foreach (var x in seq)
            {
                if (x == null) continue;
                var t = x.GetType();
                if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(KeyValuePair<,>)) list.Add(t.GetProperty("Value").GetValue(x, null));
                else list.Add(x);
            }
        }
        catch (Exception ex) { _trace.Add("enumerate " + coll.GetType().Name + ": " + Inner(ex).Message); }
        return list;
    }

    // WPF and reflection plumbing that mentions plugins only in its name (dependency properties,
    // commands), and tasks, whose Result would block
    static bool IsPlumbing(Type t)
    {
        var n = t.FullName ?? "";
        return n.StartsWith("System.Windows.") || n.StartsWith("System.Reflection.") || t == typeof(Type) || typeof(Delegate).IsAssignableFrom(t)
            || typeof(System.Threading.Tasks.Task).IsAssignableFrom(t);
    }

    static string TypeName(Type t)
    {
        if (t == null) return "?";
        if (t.IsArray) return TypeName(t.GetElementType()) + "[]";
        if (!t.IsGenericType) return t.Name;
        var n = t.Name;
        var tick = n.IndexOf('`');
        if (tick > 0) n = n.Substring(0, tick);
        return n + "<" + string.Join(", ", t.GetGenericArguments().Select(TypeName)) + ">";
    }

    static string Params(MethodBase m)
    {
        return string.Join(", ", m.GetParameters().Select(p => TypeName(p.ParameterType) + " " + p.Name));
    }

    static List<string> MemberLines(Type t, int max)
    {
        var lines = new List<string>();
        if (t.IsEnum) { lines.Add("enum: " + string.Join(", ", Enum.GetNames(t))); return lines; }
        if (t.BaseType != null && t.BaseType != typeof(object)) lines.Add("base " + TypeName(t.BaseType));
        var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        try
        {
            foreach (var c in t.GetConstructors(flags)) if (!c.IsStatic) lines.Add("ctor(" + Params(c) + ")");
            foreach (var f in t.GetFields(flags)) if (f.Name.IndexOf('<') < 0) lines.Add((f.IsStatic ? "static " : "") + "field " + f.Name + ": " + TypeName(f.FieldType));
            foreach (var p in t.GetProperties(flags)) lines.Add((IsStatic(p) ? "static " : "") + "prop " + p.Name + ": " + TypeName(p.PropertyType) + (p.CanWrite ? " (settable)" : ""));
            foreach (var m in t.GetMethods(flags))
                if (!m.IsSpecialName && m.Name.IndexOf('<') < 0) lines.Add((m.IsStatic ? "static " : "") + m.Name + "(" + Params(m) + "): " + TypeName(m.ReturnType));
        }
        catch (Exception ex) { lines.Add("(members unreadable: " + Inner(ex).Message + ")"); }
        if (lines.Count > max)
        {
            int more = lines.Count - max;
            lines = lines.Take(max).ToList();
            lines.Add("... " + more + " more");
        }
        return lines;
    }
}
