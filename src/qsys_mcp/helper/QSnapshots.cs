// Snapshot banks for the headless helper: built from the spec's "snapshots" and read back.
//
// Designer keeps them in Document.Snapshots: a GlobalBank first, then one Bank per bank. A Bank lists its
// members (Snapshot.Component, soft-linked to the block, either inclusive, i.e. every control of the block,
// or with one Snapshot.Control per control) and owns a schematic block, the snapshot_controller
// (Snapshot.PlacedComponent: load_N, save_N, ramp_time). Stored snapshots are not objects: the engine writes
// each one as a core file of the document, controlset.<bank id>.snapshot.<n>
// (controlset.global.snapshot.<n> for the global bank).
//
// Designer doesn't document these members, so each lookup tries the likely names and, when none fits,
// fails with the members the type really has.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

static class QSnapshots
{
    public const string ValuesNeedEmulator = "snapshot values need save_snapshots";
    const string Ns = "QSC.QSys.Design.Snapshot.";
    static readonly Regex DataFileName = new Regex(@"^controlset\.(?<id>.+)\.snapshot\.(?<n>\d+)$", RegexOptions.IgnoreCase);
    static readonly Regex SaveLike = new Regex("Save|Store|Write|Capture|Serialize", RegexOptions.IgnoreCase);
    static readonly Regex SnapshotLike = new Regex("ControlSet|Snapshot", RegexOptions.IgnoreCase);
    static readonly string[] SaverNames = { "SaveSnapshot", "StoreSnapshot", "CaptureSnapshot" };
    static readonly List<Made> built = new List<Made>();
    static JArray candidates;
    static string lastCallError;

    class Made { public object Bank; public object Controller; public string Name; }
    class DataFile { public string Id; public int N; public string Name; }

    public static bool IsController(object el) { return el != null && el.GetType().FullName == Ns + "PlacedComponent"; }

    // ---- build ----------------------------------------------------------------------------------------
    // Called once the components exist. blocks maps names to blocks; each bank's controller is added to it,
    // so UCIs can use the controller's buttons. Returns {banks: [...]}, plus designer_candidates when some
    // values could not be stored here (Designer members that look like an offline saving route).
    public static JObject Build(object doc, JArray specs, Dictionary<string, object> blocks, Dictionary<string, object> pages,
        string defaultPage, JArray cache, JArray warnings)
    {
        var report = new JArray();
        var res = new JObject { { "banks", report } };
        built.Clear(); candidates = null; lastCallError = null;
        if (specs == null || specs.Count == 0) return res;
        var banks = Banks(doc);
        if (!banks.Cast<object>().Any(b => b.GetType().Name == "GlobalBank"))
            warnings.Add("the new design has no global snapshot bank (Designer normally makes one)");
        foreach (JObject bs in specs)
        {
            var name = (string)bs["name"];
            if (string.IsNullOrEmpty(name) || bs["count"] == null) throw new Exception("every snapshot bank needs a name and a count");
            if (blocks.ContainsKey(name)) throw new Exception("snapshot bank " + name + ": another block already has that name");
            int count = (int)bs["count"];
            var bank = NewBank(doc, banks, name, count);
            Set(bank, count, "IntCount", "SnapshotCount", "Count");
            int members = AddMembers(bank, bs["members"] as JArray ?? new JArray(), blocks, name);
            var pc = Controller(bank);
            MatchCount(bank, pc, count, name, warnings);
            Place(pc, name, bs["controller"] as JObject ?? new JObject(), pages, defaultPage, warnings);
            blocks[name] = pc;
            built.Add(new Made { Bank = bank, Controller = pc, Name = name });
            var r = new JObject { { "name", name }, { "count", count }, { "members", members }, { "controller", QsysHelper.NameOf(pc) } };
            var values = bs["values"] as JObject;
            if (values != null && values.HasValues)
            {
                var stored = StoreValues(doc, bank, name, values, blocks, cache, warnings);
                r["values"] = stored.Count == values.Count ? "stored" : ValuesNeedEmulator;
                if (stored.Count > 0) r["stored"] = new JArray(stored);
                if (stored.Count < values.Count) res["designer_candidates"] = candidates;
            }
            report.Add(r);
        }
        return res;
    }

    static IList Banks(object doc) { return ListOf(doc, "snapshot bank list", "Snapshots", "SnapshotBanks", "_Snapshots"); }

    // the first of these members that is a list
    static IList ListOf(object o, string what, params string[] names)
    {
        foreach (var n in names)
        {
            var list = Value(o, n) as IList;
            if (list != null) return list;
        }
        throw NotFound(o.GetType(), what + " (" + string.Join(" or ", names) + ")");
    }

    // A bank from Designer's own code: a document method if there is one, else the Bank constructor.
    static object NewBank(object doc, IList banks, string name, int count)
    {
        var bankT = Q.T(Ns + "Bank");
        int before = banks.Count;
        object bank = null, r;
        if (TryCall(doc, new[] { "AddSnapshotBank", "CreateSnapshotBank", "NewSnapshotBank" }, new object[] { name, count }, out r))
            bank = bankT.IsInstanceOfType(r) ? r : (banks.Count > before ? banks[banks.Count - 1] : null);
        if (bank == null) bank = Construct(bankT, doc, name, count);
        if (!banks.Contains(bank)) banks.Add(bank);
        if (Convert.ToString(Value(bank, "Name", "_Name")) != name) Set(bank, name, "Name");
        if (Convert.ToString(Value(bank, "Name", "_Name")) != name)
            throw new Exception("snapshot bank " + name + ": Designer named it " + Value(bank, "Name", "_Name"));
        return bank;
    }

    // members: "COMP.control", {"component": "COMP", "controls": [...]} or {"component": "COMP", "all": true}.
    // One bank entry per block, in spec order; "all" (every control of the block) wins over a control list.
    static int AddMembers(object bank, JArray specs, Dictionary<string, object> blocks, string bankName)
    {
        var order = new List<string>();
        var whole = new HashSet<string>();
        var controls = new Dictionary<string, List<string>>();
        foreach (var m in specs)
        {
            string compName;
            var ctls = new List<string>();
            if (m.Type == JTokenType.String)
            {
                var s = (string)m;
                int dot = s.IndexOf('.');
                if (dot <= 0 || dot == s.Length - 1) throw new Exception("snapshot bank " + bankName + ": member '" + s + "' must look like COMPONENT.control");
                compName = s.Substring(0, dot);
                ctls.Add(s.Substring(dot + 1));
            }
            else
            {
                compName = (string)m["component"];
                if ((bool?)m["all"] == true && compName != null) whole.Add(compName);
                foreach (var c in (m["controls"] as JArray ?? new JArray())) ctls.Add((string)c);
            }
            if (compName == null || !blocks.ContainsKey(compName)) throw new Exception("snapshot bank " + bankName + ": no component " + compName);
            if (!controls.ContainsKey(compName)) { order.Add(compName); controls[compName] = new List<string>(); }
            foreach (var c in ctls) if (!controls[compName].Contains(c)) controls[compName].Add(c);
        }
        foreach (var compName in order)
        {
            var comp = blocks[compName];
            var sc = Member(bank, comp);
            bool all = whole.Contains(compName);
            if (Convert.ToBoolean(Value(sc, "IsInclusive", "_IsInclusive") ?? false) != all) Set(sc, all, "IsInclusive");
            if (!all) foreach (var code in controls[compName]) AddControl(bank, sc, comp, compName, code);
        }
        return order.Count;
    }

    // The bank's entry for a block: Designer's Bank method if it has one, else the Snapshot.Component constructor.
    static object Member(object bank, object comp)
    {
        var list = ListOf(bank, "member list", "Components", "_Components");
        var sc = FindMember(list, comp);
        if (sc != null) return sc;
        var scT = Q.T(Ns + "Component");
        if (TryAdd(bank, new[] { "AddComponent", "AddDesignComponent" }, new[] { comp }, () => FindMember(list, comp) != null, list))
            return FindMember(list, comp);
        sc = Construct(scT, comp, bank);
        if (Target(sc) != comp) Set(sc, comp, "DesignComponent");
        if (!list.Contains(sc)) list.Add(sc);
        if (Target(sc) != comp) throw NotFound(scT, "link from a bank member to its block (DesignComponent)");
        return sc;
    }

    static object FindMember(IList members, object comp)
    {
        foreach (var sc in members) if (Target(sc) == comp) return sc;
        return null;
    }

    // the block a bank member points at (its DesignComponent soft link)
    static object Target(object sc) { return Unlink(Value(sc, "DesignComponent", "_Component")); }

    // One control of a member, checked against the block's own controls.
    static void AddControl(object bank, object sc, object comp, string compName, string code)
    {
        var d = Q.Descriptors(comp).FirstOrDefault(x => Q.Cid(x) == code);
        if (d == null) throw new Exception("snapshot member " + compName + "." + code + ": " + compName + " has no control " + code);
        var list = ListOf(sc, "control list", "Controls", "_Controls");
        if (HasControl(list, code)) return;
        var pretty = Convert.ToString(Q.TryF(d, "PrettyName"));
        if (pretty == "") pretty = null;
        // Designer's own methods first (what dropping a control on a bank uses), then the constructor
        Func<bool> added = () => HasControl(list, code);
        if (TryAdd(sc, new[] { "AddControl" }, new object[] { d, code, pretty }, added, list)) return;
        if (TryAdd(bank, new[] { "AddControl" }, new object[] { comp, d, code, pretty }, added, list, ListOf(bank, "member list", "Components", "_Components"))) return;
        var ctlT = Q.T(Ns + "Control");
        var ctl = Construct(ctlT, d, sc, code, pretty);
        if (CodeOf(ctl) != code && !TrySet(ctl, code, "CodeName"))
        {
            list.Remove(ctl);   // in case its constructor added it
            ctl = Construct(ctlT, d, sc, pretty, code);
        }
        if (CodeOf(ctl) != code) { list.Remove(ctl); throw NotFound(ctlT, "way to set a snapshot control's code name (CodeName)"); }
        if (pretty != null && Convert.ToString(Value(ctl, "PrettyName", "_PrettyName")) != pretty) TrySet(ctl, pretty, "PrettyName");
        if (!list.Contains(ctl)) list.Add(ctl);
    }

    static string CodeOf(object ctl) { return Convert.ToString(Value(ctl, "CodeName", "_CodeName")); }

    static bool HasControl(IList list, string code)
    {
        foreach (var c in list) if (CodeOf(c) == code) return true;
        return false;
    }

    // The bank's snapshot_controller block from Designer's own code: the bank's link if it already made one,
    // a bank method, a creator (the bank's own, or its placeable item's, as inventory blocks are made), else
    // the PlacedComponent constructor (taking the bank). The two links (bank to block, block to its owner
    // bank) are set if Designer didn't, and checked once the design is attached.
    static object Controller(object bank)
    {
        var pcT = Q.T(Ns + "PlacedComponent");
        var pc = PlacedOf(bank);
        object r;
        if (pc == null && TryCall(bank, new[] { "CreatePlacedComponent", "CreateController", "CreateComponent" }, new object[0], out r))
            pc = pcT.IsInstanceOfType(r) ? r : PlacedOf(bank);
        var creators = new List<object> { Value(bank, "Creator", "ComponentCreator") };
        foreach (var item in Q.L(Value(bank, "PlaceableItems")))
            creators.Add(Value(Value(item, "_PlaceableComponent", "PlaceableComponent"), "Creator"));
        foreach (var creator in creators)
            if (pc == null && creator != null && TryCall(creator, new[] { "GetComponent", "Create" }, new object[] { new System.Windows.Point(0, 0) }, out r))
                pc = pcT.IsInstanceOfType(r) ? r : PlacedOf(bank);
        if (pc == null) pc = Construct(pcT, bank);
        if (PlacedOf(bank) != pc) TrySet(bank, pc, "Component", "PlacedComponent");
        if (!IsOwner(bank, pc)) TrySet(pc, bank, "Owner", "Bank");
        if (string.IsNullOrEmpty(Convert.ToString(Value(pc, "ClassName")))) Set(pc, "snapshot_controller", "ClassName");
        return pc;
    }

    static object PlacedOf(object bank) { return Unlink(Value(bank, "Component", "PlacedComponent", "_PlacedComponent")); }

    // whether a controller block's owner is this bank (any member that could hold it, at any level)
    static bool IsOwner(object bank, object pc)
    {
        foreach (var n in new[] { "Owner", "Bank", "_Owner" })
            for (var c = pc.GetType(); c != null; c = c.BaseType)
            {
                var p = c.GetProperty(n, Q.All | BindingFlags.DeclaredOnly);
                if (p != null && p.GetIndexParameters().Length == 0 && p.GetGetMethod(true) != null)
                {
                    try { if (Unlink(p.GetValue(pc, null)) == bank) return true; }
                    catch (TargetInvocationException) { }
                }
                var f = c.GetField(n, Q.All | BindingFlags.DeclaredOnly);
                if (f != null && Unlink(f.GetValue(pc)) == bank) return true;
            }
        return false;
    }

    // On its page like any block: code name = bank name, optional label (Designer's own otherwise), Script Access.
    static void Place(object pc, string name, JObject cs, Dictionary<string, object> pages, string defaultPage, JArray warnings)
    {
        var pageName = (string)cs["page"] ?? defaultPage;
        if (!pages.ContainsKey(pageName)) throw new Exception("snapshot bank " + name + ": no page '" + pageName + "' (declare it in spec.pages)");
        Set(pc, new System.Windows.Point((double?)cs["x"] ?? 0, (double?)cs["y"] ?? 0), "Position");
        Q.Call(Q.L(Q.Get(pages[pageName], "Layers"))[0], "AddChild", pc);
        // Designer may derive these from the bank: set them where it lets us, and say when the result differs
        if (QsysHelper.NameOf(pc) != name) TrySet(pc, name, "CodeName");
        var label = (string)cs["label"];
        if (label != null && !TrySet(pc, label, "UserLabel")) warnings.Add("snapshot bank " + name + ": its controller's label can't be set");
        QsysHelper.SetAccess(pc, (string)cs["script_access"] ?? "None");
        if (QsysHelper.NameOf(pc) != name) warnings.Add("snapshot bank " + name + ": Designer named its controller " + QsysHelper.NameOf(pc));
    }

    // The controller's dataset_count (its number of load/save buttons) has to match the bank's count. Its
    // properties and controls come from its class (UpdateComponent), before it's named and placed, as for
    // any new block.
    static void MatchCount(object bank, object pc, int count, string name, JArray warnings)
    {
        var ids = new List<string>();
        try
        {
            Q.Refresh(pc);
            if (Convert.ToString(Q.GetProp(pc, "dataset_count")) != count.ToString()) { Q.SetProp(pc, "dataset_count", count); Q.Refresh(pc); }
            ids = Q.Descriptors(pc).Select(Q.Cid).ToList();
        }
        catch (Exception e) { warnings.Add("snapshot bank " + name + ": could not set up its controller's dataset_count and controls: " + (e.InnerException ?? e).Message); }
        var n = Value(bank, "IntCount", "_IntCount");
        if (n != null && Convert.ToInt32(n) != count) warnings.Add("snapshot bank " + name + " has " + n + " snapshots, not " + count);
        if (!ids.Contains("load_" + count) || !ids.Contains("save_" + count))
            warnings.Add("snapshot bank " + name + ": its controller has no load_" + count + "/save_" + count + " controls");
    }

    // Attaches the new banks and their members if Designer left them detached, then checks that each new
    // bank and its controller point at each other (Designer may only make that link on attach).
    public static void Attach(object doc)
    {
        foreach (var m in built)
        {
            AttachOne(m.Bank);
            foreach (var sc in Q.L(Value(m.Bank, "Components", "_Components")))
            {
                AttachOne(sc);
                foreach (var c in Q.L(Value(sc, "Controls", "_Controls"))) AttachOne(c);
            }
            if (PlacedOf(m.Bank) != m.Controller || !IsOwner(m.Bank, m.Controller))
                throw new Exception("snapshot bank " + m.Name + ": the bank and its controller block don't point at each other. "
                    + Describe(m.Bank.GetType()) + " / " + Describe(m.Controller.GetType()));
        }
    }

    static void AttachOne(object el)
    {
        var attached = Value(el, "IsAttached");
        if (attached is bool && !(bool)attached) Q.Call(el, "Attach");
    }

    // ---- stored values ----------------------------------------------------------------------------------
    // Snapshot data is written by the engine. A Designer that could also save a snapshot offline would do it
    // with a bank method taking the snapshot number and reading the members' current values; that is the only
    // route used here, and a snapshot counts as stored only if its core file then exists. Values are set the
    // way the build stores any control value (each snapshot starts from the design's own values) and put
    // back afterwards. If that method fails, the build goes on without it. Returns the snapshot numbers
    // stored; the rest need save_snapshots.
    static List<int> StoreValues(object doc, object bank, string name, JObject values, Dictionary<string, object> blocks,
        JArray cache, JArray warnings)
    {
        var done = new List<int>();
        var save = bank.GetType().GetMethods(Q.All).FirstOrDefault(m => !m.IsStatic && Array.IndexOf(SaverNames, m.Name) >= 0
            && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType == typeof(int));
        if (save == null)
        {
            if (candidates == null) candidates = Candidates();
            return done;
        }
        var ids = Ids(bank);
        var before = new List<Tuple<object, string, object>>();   // block, control, a copy of its stored value (or null)
        foreach (var snap in values.Properties())
            foreach (var kv in (JObject)snap.Value)
            {
                var comp = BlockOf(kv.Key, blocks);
                var ctl = kv.Key.Substring(kv.Key.IndexOf('.') + 1);
                if (before.Any(b => b.Item1 == comp && b.Item2 == ctl)) continue;
                var cached = (IDictionary)Q.Get(comp, "CachedControlValues");
                before.Add(Tuple.Create(comp, ctl, cached.Contains(ctl) ? CopyOf(cached[ctl]) : null));
            }
        try
        {
            foreach (var snap in values.Properties())
            {
                int n;
                if (!int.TryParse(snap.Name, out n)) throw new Exception("snapshot values: '" + snap.Name + "' is not a snapshot number");
                PutBack(before);
                foreach (var kv in (JObject)snap.Value)
                    QsysHelper.StoreValue(BlockOf(kv.Key, blocks), kv.Key.Substring(kv.Key.IndexOf('.') + 1), kv.Value, cache);
                try { save.Invoke(bank, new object[] { n }); }
                catch (TargetInvocationException e)
                {
                    warnings.Add("snapshot bank " + name + ": Designer's " + save.DeclaringType.Name + "." + save.Name + "(" + n + ") failed: "
                        + (e.InnerException ?? e).Message);
                    break;
                }
                if (DataFiles(doc).Any(f => f.N == n && ids.Contains(f.Id))) done.Add(n);
            }
        }
        finally { PutBack(before); }
        if (done.Count < values.Count && candidates == null) candidates = Candidates();
        return done;
    }

    // a stored value (CachedControlValue) as it is now, so changing the block's value can't change it
    static object CopyOf(object cv)
    {
        if (cv == null || cv.GetType().IsValueType) return cv;
        var copy = Activator.CreateInstance(cv.GetType(), true);
        foreach (var f in cv.GetType().GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)) f.SetValue(copy, f.GetValue(cv));
        return copy;
    }

    static object BlockOf(string key, Dictionary<string, object> blocks)
    {
        int dot = key.IndexOf('.');
        object comp;
        if (dot <= 0 || !blocks.TryGetValue(key.Substring(0, dot), out comp)) throw new Exception("snapshot value " + key + ": no component " + (dot > 0 ? key.Substring(0, dot) : key));
        return comp;
    }

    static void PutBack(List<Tuple<object, string, object>> before)
    {
        foreach (var b in before)
        {
            if (b.Item3 != null) Q.Call(b.Item1, "UpdateControlValue", b.Item2, b.Item3);
            else ((IDictionary)Q.Get(b.Item1, "CachedControlValues")).Remove(b.Item2);
        }
    }

    // Designer methods whose names look like saving snapshots or control sets: what an offline route would use.
    static JArray Candidates()
    {
        var hits = new List<string>();
        const BindingFlags declared = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] types;
            try
            {
                if (asm.IsDynamic || !asm.Location.StartsWith(Q.DesignerDir, StringComparison.OrdinalIgnoreCase)) continue;
                types = asm.GetTypes();
            }
            catch (ReflectionTypeLoadException e) { types = e.Types.Where(x => x != null).ToArray(); }
            catch (Exception) { continue; }
            foreach (var t in types)
            {
                bool related = t.Name.IndexOf("ControlSet", StringComparison.OrdinalIgnoreCase) >= 0 || (t.Namespace ?? "").EndsWith(".Snapshot");
                MethodInfo[] methods;
                try { methods = t.GetMethods(declared); } catch (Exception) { continue; }
                foreach (var m in methods)
                    if (!m.IsSpecialName && !m.Name.StartsWith("<") && SaveLike.IsMatch(m.Name) && (related || SnapshotLike.IsMatch(m.Name)))
                        hits.Add(t.FullName + "." + m.Name + "(" + Params(m) + ")");
            }
        }
        return new JArray(hits.Distinct().OrderBy(h => h).Take(80));
    }

    // ---- read -------------------------------------------------------------------------------------------
    // {banks: [{name, id, count, members: [{component, all, controls}], controller: {name, label, page, x, y,
    //  script_access}, stored: [n...]}], global: {count, stored}, other_data: [core file names]}.
    // "stored" lists the snapshots that hold data (their core files exist). A problem here never fails the
    // whole read: it comes back as {error}.
    public static JObject Read(object doc)
    {
        try { return ReadBanks(doc); }
        catch (Exception e)
        {
            while (e.InnerException != null) e = e.InnerException;
            return new JObject { { "error", e.GetType().Name + ": " + e.Message } };
        }
    }

    // snapshot controllers on the schematic pages (for UCIs that use their buttons)
    public static List<object> Controllers(object doc) { return Placed(doc).Keys.ToList(); }

    static JObject ReadBanks(object doc)
    {
        var files = DataFiles(doc);
        var placed = Placed(doc);
        var claimed = new HashSet<string>();
        var banksOut = new JArray();
        var res = new JObject { { "banks", banksOut } };
        foreach (var bank in Banks(doc))
        {
            bool global = bank.GetType().Name == "GlobalBank";
            var ids = Ids(bank);
            if (global) ids.Add("global");
            var mine = files.Where(f => ids.Contains(f.Id)).ToList();
            foreach (var f in mine) claimed.Add(f.Name);
            var stored = new JArray(mine.Select(f => f.N).Distinct().OrderBy(n => n));
            var countValue = Value(bank, "IntCount", "_IntCount");
            JToken count = countValue == null ? null : (JToken)Convert.ToInt32(countValue);
            if (global)
            {
                res["global"] = new JObject { { "count", count }, { "stored", stored } };
                continue;
            }
            var members = new JArray();
            foreach (var sc in Q.L(Value(bank, "Components", "_Components")))
            {
                var target = Target(sc);
                members.Add(new JObject {
                    { "component", target == null ? null : QsysHelper.NameOf(target) },
                    { "all", Convert.ToBoolean(Value(sc, "IsInclusive", "_IsInclusive") ?? false) },
                    { "controls", new JArray(Q.L(Value(sc, "Controls", "_Controls")).Select(c => CodeOf(c))) } });
            }
            var pc = PlacedOf(bank) ?? placed.Keys.FirstOrDefault(k => IsOwner(bank, k));
            JObject controller = null;
            if (pc != null)
            {
                string page;
                placed.TryGetValue(pc, out page);
                controller = new JObject { { "name", QsysHelper.NameOf(pc) }, { "label", Convert.ToString(Value(pc, "Label")) }, { "page", page },
                    { "script_access", Convert.ToString(Value(pc, "ScriptAccess")) } };
                var pos = Value(pc, "Position");
                if (pos is System.Windows.Point) { controller["x"] = ((System.Windows.Point)pos).X; controller["y"] = ((System.Windows.Point)pos).Y; }
            }
            banksOut.Add(new JObject { { "name", Convert.ToString(Value(bank, "Name", "_Name")) }, { "id", IdText(bank) }, { "count", count },
                { "members", members }, { "controller", controller }, { "stored", stored } });
        }
        var other = files.Where(f => !claimed.Contains(f.Name)).Select(f => f.Name).ToList();
        if (other.Count > 0) res["other_data"] = new JArray(other);
        return res;
    }

    static Dictionary<object, string> Placed(object doc)
    {
        var map = new Dictionary<object, string>();
        foreach (var page in Q.L(Q.Get(doc, "Pages")))
            foreach (var k in Q.L(Q.Get(page, "AllChildren")))
                if (IsController(k)) map[k] = Convert.ToString(Q.Get(page, "Title"));
        return map;
    }

    // the document's core files that hold stored snapshots
    static List<DataFile> DataFiles(object doc)
    {
        var list = new List<DataFile>();
        foreach (var cf in Q.L(Value(doc, "CoreFiles", "_CoreFiles")))
        {
            var name = Convert.ToString(Value(cf, "FileName", "Name"));
            var m = DataFileName.Match(name);
            int n;
            if (m.Success && int.TryParse(m.Groups["n"].Value, out n)) list.Add(new DataFile { Id = m.Groups["id"].Value, N = n, Name = name });
        }
        return list;
    }

    // the bank id as core file names are likely to spell it: the Guid a UniqueId wraps, else the id's own text
    static string IdText(object bank)
    {
        var id = Value(bank, "Id", "_Id");
        if (id == null) return null;
        if (id is Guid) return ((Guid)id).ToString("D");
        foreach (var f in id.GetType().GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
            if (f.FieldType == typeof(Guid)) return ((Guid)f.GetValue(id)).ToString("D");
        return Convert.ToString(id);
    }

    static HashSet<string> Ids(object bank)
    {
        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var id = Value(bank, "Id", "_Id");
        if (id != null) ids.Add(Convert.ToString(id));
        var text = IdText(bank);
        Guid g;
        if (text != null && Guid.TryParse(text, out g)) { ids.Add(g.ToString("D")); ids.Add(g.ToString("N")); ids.Add(g.ToString("B")); }
        return ids;
    }

    // ---- tolerant reflection ----------------------------------------------------------------------------
    static PropertyInfo PropOf(Type t, string name)
    {
        for (var c = t; c != null; c = c.BaseType)
        {
            var p = c.GetProperties(Q.All | BindingFlags.DeclaredOnly).FirstOrDefault(x => x.Name == name && x.GetIndexParameters().Length == 0);
            if (p != null) return p;
        }
        return null;
    }

    static FieldInfo FieldOf(Type t, string name)
    {
        for (var c = t; c != null; c = c.BaseType)
        {
            var f = c.GetField(name, Q.All | BindingFlags.DeclaredOnly);
            if (f != null) return f;
        }
        return null;
    }

    // The value of the first of these properties or fields the object has (null if none).
    static object Value(object o, params string[] names)
    {
        if (o == null) return null;
        foreach (var n in names)
        {
            var p = PropOf(o.GetType(), n);
            if (p != null && p.GetGetMethod(true) != null)
            {
                try { return p.GetValue(o, null); }
                catch (TargetInvocationException) { continue; }
            }
            var f = FieldOf(o.GetType(), n);
            if (f != null) return f.GetValue(o);
        }
        return null;
    }

    // Sets the first of these properties that takes the value; false if none does.
    static bool TrySet(object o, object value, params string[] names)
    {
        foreach (var n in names)
        {
            var p = PropOf(o.GetType(), n);
            object v = value;
            if (p == null || p.GetSetMethod(true) == null || (p.PropertyType != typeof(object) && !Fit(value, p.PropertyType, out v))) continue;
            p.SetValue(o, v, null);
            return true;
        }
        return false;
    }

    static void Set(object o, object value, params string[] names)
    {
        if (!TrySet(o, value, names)) throw NotFound(o.GetType(), "writable property " + string.Join(" or ", names) + " for " + value);
    }

    // value as type t, when that needs no guessing (no enums, no object parameters)
    static bool Fit(object value, Type t, out object result)
    {
        result = value;
        var u = Nullable.GetUnderlyingType(t) ?? t;
        if (value == null) return !t.IsValueType || u != t;
        if (u.IsInstanceOfType(value)) return u != typeof(object);
        if (u.IsEnum || !(value is IConvertible) || !(u.IsPrimitive || u == typeof(string))) return false;
        try { result = Convert.ChangeType(value, u); return true; }
        catch (Exception) { return false; }
    }

    // Arguments for these parameters taken from pool by type, each used once; null if one is missing.
    static object[] Fill(ParameterInfo[] ps, object[] pool)
    {
        var args = new object[ps.Length];
        var used = new bool[pool.Length];
        for (int i = 0; i < ps.Length; i++)
        {
            var t = ps[i].ParameterType;
            if (t == typeof(object) || t.IsByRef || t.IsPointer) return null;
            int k = -1;
            for (int j = 0; j < pool.Length && k < 0; j++)
                if (!used[j] && pool[j] != null && t.IsInstanceOfType(pool[j])) k = j;
            if (k >= 0) { used[k] = true; args[i] = pool[k]; continue; }
            if (ps[i].IsOptional && !(ps[i].DefaultValue is DBNull) && ps[i].DefaultValue != Missing.Value) { args[i] = ps[i].DefaultValue; continue; }
            return null;
        }
        return args;
    }

    // Calls the first of these methods (by name order) whose parameters all come from args; of same-named
    // overloads, the one using most of them. A call that throws counts as not made (the error is kept for
    // the message if nothing else works).
    static bool TryCall(object target, string[] names, object[] args, out object result)
    {
        result = null;
        foreach (var n in names)
        {
            MethodInfo best = null;
            object[] bestArgs = null;
            foreach (var m in target.GetType().GetMethods(Q.All))
            {
                if (m.Name != n || m.IsStatic || m.IsGenericMethodDefinition || m.IsAbstract) continue;
                var a = Fill(m.GetParameters(), args);
                if (a != null && (best == null || a.Length > bestArgs.Length)) { best = m; bestArgs = a; }
            }
            if (best == null) continue;
            try { result = best.Invoke(target, bestArgs); return true; }
            catch (TargetInvocationException e) { lastCallError = best.DeclaringType.Name + "." + best.Name + ": " + (e.InnerException ?? e).Message; }
        }
        return false;
    }

    // TryCall for a method that adds to these lists: it counts only if check then holds, and otherwise
    // whatever it added to them is taken out again.
    static bool TryAdd(object target, string[] names, object[] args, Func<bool> check, params IList[] lists)
    {
        var had = lists.Select(l => l.Cast<object>().ToList()).ToList();
        object r;
        if (TryCall(target, names, args, out r) && check()) return true;
        for (int k = 0; k < lists.Length; k++)
            for (int i = lists[k].Count - 1; i >= 0; i--)
                if (!had[k].Contains(lists[k][i])) lists[k].RemoveAt(i);
        return false;
    }

    // An object from Designer's own constructor for t: the one taking the most of args (by type).
    static object Construct(Type t, params object[] args)
    {
        ConstructorInfo best = null;
        object[] bestArgs = null;
        foreach (var c in t.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
        {
            var a = Fill(c.GetParameters(), args);
            if (a != null && (best == null || a.Length > bestArgs.Length)) { best = c; bestArgs = a; }
        }
        if (best == null)
            throw NotFound(t, "constructor taking " + (args.Length == 0 ? "nothing" : "any of (" + string.Join(", ", args.Where(x => x != null).Select(x => x.GetType().Name)) + ")"));
        return best.Invoke(bestArgs);
    }

    // what a SoftLink or HardLink points at (the first of its members that holds something); other values as they are
    static object Unlink(object v)
    {
        if (v == null) return null;
        var n = v.GetType().Name;
        if (!n.StartsWith("SoftLink") && !n.StartsWith("HardLink")) return v;
        foreach (var m in new[] { "Link", "Target", "_Link" })
        {
            var x = Value(v, m);
            if (x != null) return x;
        }
        return null;
    }

    static Exception NotFound(Type t, string what)
    {
        return new Exception(t.FullName + " has no " + what + (lastCallError != null ? " (a call failed: " + lastCallError + ")" : "")
            + ". What it has: " + Describe(t));
    }

    // A type's constructors, properties and methods (and its base type's), for errors when no guess fits.
    static string Describe(Type t)
    {
        const BindingFlags declared = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        var parts = new List<string>();
        for (var c = t; c != null && c != typeof(object) && parts.Count < 2; c = c.BaseType)
        {
            var items = c.GetConstructors(declared).Where(x => !x.IsStatic).Select(x => "new(" + Params(x) + ")")
                .Concat(c.GetProperties(declared).Select(x => x.Name + ": " + x.PropertyType.Name))
                .Concat(c.GetMethods(declared).Where(x => !x.IsSpecialName && !x.Name.StartsWith("<")).Select(x => x.Name + "(" + Params(x) + ")"))
                .Distinct().Take(200);
            parts.Add(c.FullName + " { " + string.Join("; ", items) + " }");
        }
        return string.Join(" : ", parts);
    }

    static string Params(MethodBase m) { return string.Join(", ", m.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name)); }
}
