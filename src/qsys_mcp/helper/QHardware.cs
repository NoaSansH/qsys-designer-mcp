// Hardware settings: properties of the Core and of inventory devices, their USB bridge (speakerphone,
// sound cards, video bridge) and the design's own properties (PTP, QoS, MTU, Software Dante). All of it
// goes through Designer's property setters (and, if the USB blocks don't show up, its update methods).
// Names differ between Designer versions, so lookups try known candidates and, when none fits, fail
// with the properties that do exist: one run says which name to use.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json.Linq;

static class QHardware
{
    const BindingFlags Pub = BindingFlags.Public | BindingFlags.Instance;
    public static bool Strict = true;   // build: a failed USB check is an error; probe: a warning
    public static readonly JArray Applied = new JArray(), Warnings = new JArray();

    // ---- spec -------------------------------------------------------------------------------------
    // core.set, core.usb and design.set (inventory items: ApplyItem)
    public static void Apply(object doc, object core, JObject spec)
    {
        var cs = spec["core"] as JObject;
        if (cs != null)
        {
            SetAll(core, cs["set"] as JObject, Label(core));
            ApplyUsb(core, cs["usb"] as JObject);
        }
        var ds = spec["design"] as JObject;
        if (ds == null || ds["set"] == null) return;
        var dp = DesignProps(doc);
        if (dp == null) throw new Exception("design.set: found no DesignProperties on the document; its properties: " + AllNames(doc));
        SetAll(dp, ds["set"] as JObject, "design");
    }

    // an inventory item's set and usb
    public static void ApplyItem(object item, JObject spec)
    {
        SetAll(item, spec["set"] as JObject, Label(item));
        ApplyUsb(item, spec["usb"] as JObject);
    }

    // set: {"Property": value}. Names ignore case, spaces and _; enums take a name, a label or a number.
    public static void SetAll(object o, JObject set, string what)
    {
        if (set == null) return;
        foreach (var kv in set)
        {
            var p = Find(o, null, kv.Key);
            if (p == null) throw new Exception(what + " has no property '" + kv.Key + "'; settable: " + Names(o));
            Assign(o, p, kv.Value, what);
        }
    }

    // one property through its setter, logged; a value that doesn't stick is a warning
    static void Assign(object o, PropertyInfo p, object value, string what)
    {
        var label = what + "." + p.Name;
        if (!p.CanWrite) throw new Exception(label + " is read-only; settable: " + Names(o));
        var tok = value as JToken;
        var v = tok != null ? ToValue(p.PropertyType, tok, label) : value;
        try { p.SetValue(o, v, null); }
        catch (TargetInvocationException e) { throw new Exception(label + " = " + Show(Json(v)) + ": " + (e.InnerException ?? e).Message); }
        var back = Json(Read(o, p));
        Applied.Add(label + " = " + Show(back));
        if (!JToken.DeepEquals(Json(v), back)) Warnings.Add(label + " was set to " + Show(Json(v)) + " but reads back " + Show(back));
    }

    // build: what was set, and its warnings
    public static void Report(JObject res, JArray warnings)
    {
        foreach (var w in Warnings) warnings.Add(w);
        if (Applied.Count > 0) res["hardware"] = Applied;
    }

    // ---- USB ----------------------------------------------------------------------------------------
    // A device's USB settings sit in up to three places: USBBridge* properties on the device (9.13's
    // I/O-USB Bridge: USBBridgeSlotType...), its USBBridge object (Core.UsbBridge: USBCount, USBSlots),
    // or plain properties on the device (9.9: UsbSlots, IsUsbCameraBridge). Each is tried in that order.
    static readonly string[] CountW = { "USBBridgeUsbCount", "USBBridgeCount", "USBBridgeSlotCount" }, CountP = { "USBCount", "UsbSlotCount" },
        SlotsW = { "USBBridgeUsbSlots", "USBBridgeSlots" }, SlotsP = { "USBSlots" },
        TypeW = { "USBBridgeSlotType", "USBBridgeUsbSlotType" }, TypeP = { "UsbSlotType", "SlotType" },
        ModeW = { "USBBridgeSpeakerphoneMode" }, ModeP = { "SpeakerphoneMode" },
        VideoW = { "USBBridgeIsUsbCameraBridge", "USBBridgeVideoBridge", "USBBridgeIsVideoBridge" },
        VideoP = { "VideoBridge", "IsVideoBridge", "IsUsbCameraBridge", "UsbCameraBridge", "VideoBridgeEnabled" };
    // update methods that may rebuild an item's blocks (tried only when the USB blocks are missing)
    static readonly string[] RefreshNames = { "UpdatePlaceableItems", "RefreshPlaceableItems", "UpdatePlaceables", "RefreshPlaceables",
        "UpdateUsbSlots", "UpdateSlots", "UpdateUsbBridge", "UpdateComponents", "RefreshComponents" };

    // usb: {type, count, mode, video_bridge, set, slots} on the Core or a device with a USB bridge
    public static void ApplyUsb(object owner, JObject usb)
    {
        if (usb == null) return;
        var what = Label(owner);
        if (!HasUsb(owner)) throw new Exception(what + " (" + owner.GetType().Name + ") has no USB bridge; see designer_versions -> usb_bridge_types");
        string kind = usb["type"] == null ? null : Kind((string)usb["type"], what);
        int? want = usb["count"] != null ? (int)usb["count"] : kind == "none" ? 0 : (int?)null;
        object typeOn, countOn;
        var typeP = UsbProp(owner, EnumLike("SlotType"), TypeW, TypeP, true, out typeOn);
        var countP = UsbProp(owner, IsWhole, CountW, CountP, false, out countOn);
        if (usb["count"] != null && countP == null) throw Missing(owner, "the USB slot count");
        var member = kind == null || typeP == null ? null : Member(Und(typeP.PropertyType), kind);
        // slots first: a speakerphone or sound card type adds none by itself, so no In/Out blocks
        // (the type alone gave an I/O-USB Bridge only its video bridge and status blocks)
        SetCount(owner, countOn, countP, want, kind);
        if (member != null) Assign(typeOn, typeP, member, Where(owner, typeOn));
        SetCount(owner, countOn, countP, want, kind);   // the type can change the count
        // each slot's own type: set here only where the device has no bridge-wide one (else Check may)
        bool slotTyped = kind != null && kind != "none" && SlotTypes(owner, kind, member == null) > 0;
        if (kind != null && member == null && !slotTyped && !(kind == "none" && countP != null))
            throw Missing(owner, "a USB type with a " + kind + " choice" + (typeP == null ? "" : " (" + typeP.Name + ": " + Choices(Und(typeP.PropertyType), ", ") + ")"));
        object on;
        if (usb["mode"] != null)
        {
            var mp = UsbProp(owner, EnumLike("SpeakerphoneMode"), ModeW, ModeP, true, out on);
            if (mp != null) Assign(on, mp, usb["mode"], Where(owner, on));
            else
            {
                var slots = Slots(owner).Where(s => SlotModeProp(s) != null).ToList();
                if (slots.Count == 0) throw Missing(owner, "a speakerphone mode");
                foreach (var s in slots) Assign(s, SlotModeProp(s), usb["mode"], what + " USB slot");
            }
        }
        if (usb["video_bridge"] != null)
        {
            var vp = UsbProp(owner, IsBool, VideoW, VideoP, false, out on);
            if (vp == null) throw Missing(owner, "a USB video bridge switch");
            Assign(on, vp, usb["video_bridge"], Where(owner, on));
        }
        SetUsb(owner, usb["set"] as JObject);
        var slotSets = usb["slots"] as JArray;
        if (slotSets != null)
        {
            var slots = Slots(owner);
            if (slotSets.Count > slots.Count) throw new Exception(what + ": usb.slots has " + slotSets.Count + " entries for " + slots.Count + " USB slots");
            for (int i = 0; i < slotSets.Count; i++) SetAll(slots[i], slotSets[i] as JObject, what + " USB slot " + (i + 1));
        }
        Check(owner, kind, usb["video_bridge"] == null ? (bool?)null : (bool)usb["video_bridge"]);
    }

    // the slot count: as asked, else at least 1 for a speakerphone or sound card
    static void SetCount(object owner, object on, PropertyInfo p, int? want, string kind)
    {
        if (p == null) return;
        int now = ToInt(Read(on, p));
        int target = want.HasValue ? want.Value : (kind == "speakerphone" || kind == "soundcard") && now == 0 ? 1 : now;
        if (target != now) Assign(on, p, new JValue((long)target), Where(owner, on));
    }

    // usb.set: USBBridge<name> on the device, then <name> on its USBBridge object, then on the device
    static void SetUsb(object owner, JObject set)
    {
        if (set == null) return;
        var b = Bridge(owner);
        foreach (var kv in set)
        {
            object on = owner;
            var p = Find(owner, null, "USBBridge" + kv.Key);
            if (p == null && b != null && (p = Find(b, null, kv.Key)) != null) on = b;
            if (p == null) p = Find(owner, null, kv.Key);
            if (p == null) throw Missing(owner, "USB property '" + kv.Key + "'");
            Assign(on, p, kv.Value, Where(owner, on));
        }
    }

    // each USB slot's own type (9.9 keeps it there): how many slots can take the kind; with set, those that differ are set
    static int SlotTypes(object owner, string kind, bool set)
    {
        int n = 0;
        foreach (var slot in Slots(owner))
        {
            var sp = SlotTypeProp(slot);
            var sm = sp == null ? null : Member(Und(sp.PropertyType), kind);
            if (sm == null) continue;
            n++;
            if (set && KindOf(Convert.ToString(Read(slot, sp))) != kind) Assign(slot, sp, sm, Label(owner) + " USB slot");
        }
        return n;
    }

    // after usb: the USB In/Out blocks (usb_receiver, usb_transmitter) and the video bridge (usb_uvc) must be on
    // offer. If not, the slots' own types and then Designer's update methods are tried; then it fails (in probe: warns).
    static void Check(object owner, string kind, bool? video)
    {
        var what = Label(owner);
        var problem = UsbProblem(owner, kind, video);
        if (problem != null && (kind == "speakerphone" || kind == "soundcard"))
        {
            int before = Applied.Count;
            SlotTypes(owner, kind, true);
            if (Applied.Count > before && (problem = UsbProblem(owner, kind, video)) == null) Applied.Add(what + ": set its USB slots' own type to show its USB blocks");
        }
        if (problem != null)
        {
            var how = Refresh(owner, () => UsbProblem(owner, kind, video) == null);
            if (how != null) { Applied.Add(what + ": called " + how + " to show its USB blocks"); problem = null; }
        }
        var blocks = BlockList(Blocks(owner, !Strict));
        if (problem == null) { Applied.Add(what + " blocks: " + blocks); return; }
        var b = Bridge(owner);
        var msg = what + " usb: " + problem + ". Its blocks: " + blocks + ". Settable: " + Settables(owner)
            + ". Update methods: " + Methods(owner) + (b == null ? "" : "; " + Methods(b));
        if (Strict) throw new Exception(msg);
        Warnings.Add(msg);
    }

    static string UsbProblem(object owner, string kind, bool? video)
    {
        var blocks = Blocks(owner, !Strict);
        var probs = new List<string>();
        var now = (string)UsbInfo(owner, false, false)["type"];
        if (kind != null && now != kind) probs.Add("its USB type reads back as " + (now ?? "unknown"));
        if (kind == "speakerphone" || kind == "soundcard")
        {
            if (!blocks.Any(x => IsBlock(x, "in"))) probs.Add("no USB input block (usb_receiver)");
            if (!blocks.Any(x => IsBlock(x, "out"))) probs.Add("no USB output block (usb_transmitter)");
        }
        if (video == true && !blocks.Any(x => IsBlock(x, "video"))) probs.Add("no USB video bridge block (usb_uvc)");
        return probs.Count == 0 ? null : string.Join("; ", probs.ToArray());
    }

    // a USB block by its class (usb_receiver, usb_transmitter, usb_uvc and the like) or, when that isn't known, its name
    static bool IsBlock(Block b, string role)
    {
        var c = Norm(b.Class);
        var n = Norm(b.Name);
        if (role == "video") return b.Class != null ? c.Contains("uvc") || (c.Contains("usb") && c.Contains("video")) : n.Contains("video");
        if (b.Class != null) return c.Contains(role == "in" ? "receiver" : "transmitter") && new[] { "usb", "alsa", "speaker", "sound" }.Any(c.Contains);
        return n.EndsWith(role) && (n.Contains("usb") || n.Contains("speaker") || n.Contains("sound"));
    }

    // Designer may only rebuild an item's blocks in an update call: try the likely ones until done() holds
    static string Refresh(object owner, Func<bool> done)
    {
        foreach (var o in new[] { owner, Bridge(owner) })
        {
            if (o == null) continue;
            foreach (var n in RefreshNames)
            {
                var m = o.GetType().GetMethods(Q.All).FirstOrDefault(x => !x.IsStatic && !x.IsGenericMethod && x.GetParameters().Length == 0 && Norm(x.Name) == Norm(n));
                if (m == null) continue;
                try { m.Invoke(o, null); } catch { continue; }
                if (done()) return o.GetType().Name + "." + m.Name + "()";
            }
        }
        return null;
    }

    // the USBBridge object a device keeps its slots in (Designer 10, and 9.13 under the hood), or null
    public static object Bridge(object owner)
    {
        if (owner == null) return null;
        var p = Find(owner, t => !Scalar(t) && t.Name.IndexOf("Bridge", StringComparison.OrdinalIgnoreCase) >= 0, "UsbBridge");
        var b = p == null ? null : Read(owner, p);
        return b ?? FieldUp(owner, "_usbBridge");
    }

    public static bool HasUsb(object owner)
    {
        object on;
        return Bridge(owner) != null || UsbProp(owner, IsList, SlotsW, SlotsP, false, out on) != null || Find(owner, null, TypeW) != null;
    }

    // "bridge" (a USBBridge object), "device" (settings on the device itself, as in 9.9) or "none"
    public static string Api(object owner) { return Bridge(owner) != null ? "bridge" : HasUsb(owner) ? "device" : "none"; }

    public static List<object> Slots(object owner)
    {
        object on;
        var p = UsbProp(owner, IsList, SlotsW, SlotsP, false, out on);
        return p == null ? new List<object>() : Q.L(Read(on, p));
    }

    // a USB setting: USBBridge* on the device, then on its USBBridge object, then plain on the device. fits
    // filters by type; with anyName a property of a fitting type is taken whatever its name (bridge first).
    static PropertyInfo UsbProp(object owner, Func<Type, bool> fits, string[] wrapped, string[] plain, bool anyName, out object on)
    {
        var b = Bridge(owner);
        PropertyInfo p;
        if ((p = Find(owner, fits, wrapped)) != null) { on = owner; return p; }
        if (b != null && (p = Find(b, fits, plain)) != null) { on = b; return p; }
        if ((p = Find(owner, fits, plain)) != null) { on = owner; return p; }
        if (anyName)
        {
            if (b != null && (p = Find(b, fits)) != null) { on = b; return p; }
            if ((p = Find(owner, fits)) != null) { on = owner; return p; }
        }
        on = null;
        return null;
    }

    static PropertyInfo SlotTypeProp(object slot) { return Find(slot, IsEnumType, "UsbType") ?? Find(slot, EnumLike("UsbType")); }
    static PropertyInfo SlotModeProp(object slot) { return Find(slot, EnumLike("SpeakerphoneMode"), "SpeakerphoneMode") ?? Find(slot, EnumLike("SpeakerphoneMode")); }
    static string Where(object owner, object on) { return on == owner ? Label(owner) : Label(owner) + " USB bridge"; }

    static string Kind(string s, string what)
    {
        var n = Norm(s);
        if (n == "speakerphone" || n == "soundcard" || n == "none") return n;
        throw new Exception(what + ": usb.type must be speakerphone, soundcard or none, not '" + s + "'");
    }

    // speakerphone / soundcard / none from an enum member's name or label, else null
    static string KindOf(string name)
    {
        var n = Norm(name);
        if (n.Contains("speaker")) return "speakerphone";
        if (n.Contains("sound") || n.Contains("card")) return "soundcard";
        if (n == "none" || n == "off" || n == "disabled") return "none";
        return null;
    }

    // the enum member for a kind (the shortest name, if several fit)
    static object Member(Type et, string kind)
    {
        var f = et.GetFields(BindingFlags.Public | BindingFlags.Static).Where(x => Words(x).Any(w => KindOf(w) == kind)).OrderBy(x => x.Name.Length).FirstOrDefault();
        return f == null ? null : f.GetValue(null);
    }

    // read_design / probe: {api, type, count, slot_type, mode, video_bridge, slots?, properties?}
    public static JObject UsbInfo(object owner, bool withSlots, bool detail)
    {
        object on;
        var o = new JObject { { "api", Api(owner) } };
        var found = new JObject();
        var lp = UsbProp(owner, IsList, SlotsW, SlotsP, false, out on);
        if (lp != null) found["slots"] = on.GetType().Name + "." + lp.Name;
        var slots = lp == null ? new List<object>() : Q.L(Read(on, lp));
        var cp = UsbProp(owner, IsWhole, CountW, CountP, false, out on);
        if (cp != null) found["count"] = on.GetType().Name + "." + cp.Name;
        int count = cp != null ? ToInt(Read(on, cp)) : slots.Count;
        var tp = UsbProp(owner, EnumLike("SlotType"), TypeW, TypeP, true, out on);
        if (tp != null) found["type"] = on.GetType().Name + "." + tp.Name;
        var bridgeType = tp == null ? null : Convert.ToString(Read(on, tp));
        string slotType = null;
        foreach (var s in slots) { var sp = SlotTypeProp(s); if (sp != null) { slotType = Convert.ToString(Read(s, sp)); break; } }
        // no count and no slot list found: the type alone says what it is
        bool counted = cp != null || lp != null;
        o["type"] = counted && count == 0 ? "none" : KindOf(bridgeType) ?? KindOf(slotType) ?? bridgeType ?? slotType;
        if (counted) o["count"] = count;
        if (bridgeType != null) o["slot_type"] = bridgeType;
        var mp = UsbProp(owner, EnumLike("SpeakerphoneMode"), ModeW, ModeP, true, out on);
        if (mp != null) { o["mode"] = Convert.ToString(Read(on, mp)); found["mode"] = on.GetType().Name + "." + mp.Name; }
        var vp = UsbProp(owner, IsBool, VideoW, VideoP, false, out on);
        if (vp != null) { o["video_bridge"] = Json(Read(on, vp)); found["video_bridge"] = on.GetType().Name + "." + vp.Name; }
        if (withSlots) o["slots"] = SlotInfo(owner);
        if (detail) o["properties"] = found;
        return o;
    }

    public static JArray SlotInfo(object owner)
    {
        var arr = new JArray();
        foreach (var slot in Slots(owner))
        {
            var o = new JObject { { "name", Convert.ToString(Q.TryGet(slot, "Name")) }, { "type", Convert.ToString(Q.TryGet(slot, "UsbType")) },
                { "inputs", Convert.ToString(Q.TryGet(slot, "InputCount")) }, { "outputs", Convert.ToString(Q.TryGet(slot, "OutputCount")) } };
            var mp = SlotModeProp(slot);
            if (mp != null) o["mode"] = Convert.ToString(Read(slot, mp));
            arr.Add(o);
        }
        return arr;
    }

    // inventory types with a USB bridge (USBBridge object, USB slots or USBBridge* properties)
    public static IEnumerable<Type> UsbBridgeTypes(IEnumerable<Type> types)
    {
        return types.Where(t => Norm(t.Name) != "usbbridge" && Norm(t.Name).IndexOf("slot", StringComparison.Ordinal) < 0 && HasUsbMember(t));
    }

    static bool HasUsbMember(Type t)
    {
        try
        {
            for (var x = t; x != null && x != typeof(object); x = x.BaseType)
                foreach (var m in x.GetMembers(Q.All | BindingFlags.DeclaredOnly))
                {
                    if (m.MemberType != MemberTypes.Property && m.MemberType != MemberTypes.Field) continue;
                    var n = Norm(m.Name);
                    if (n == "usbbridge" || n == "usbslots" || n == "usbbridgeslottype") return true;
                }
        }
        catch { }
        return false;
    }

    // ---- blocks (placeables) ----------------------------------------------------------------------
    sealed class Block { public object Owner; public string Name, Class; }

    // the blocks an item offers, its USB bridge's and slots' included. A block's class comes from its
    // creator; create also asks Designer for the block (which creates it), for classes not known otherwise.
    static List<Block> Blocks(object owner, bool create)
    {
        var res = new List<Block>();
        foreach (var o in WithParts(owner))
            foreach (var pi in Q.L(Q.TryGet(o, "PlaceableItems")))
            {
                var b = new Block { Owner = o, Name = Convert.ToString(Q.TryGet(pi, "Name")), Class = ClassOf(pi) };
                if (b.Class == null && create)
                {
                    try { b.Class = Convert.ToString(Q.Get(Q.PlaceFromInventory(o, b.Name), "ClassName")); } catch { }
                }
                if (!res.Any(x => x.Name == b.Name)) res.Add(b);
            }
        return res;
    }

    // an item, then the parts that may offer blocks of their own: its USB bridge and slots
    public static IEnumerable<object> WithParts(object owner)
    {
        yield return owner;
        var b = Bridge(owner);
        if (b != null) yield return b;
        foreach (var s in Slots(owner)) yield return s;
    }

    static string ClassOf(object placeable)
    {
        try
        {
            var named = placeable.GetType().GetField("_PlaceableComponent", Q.All).GetValue(placeable);
            var cls = Convert.ToString(Q.TryGet(Q.TryGet(named, "Creator"), "ClassName"));
            return string.IsNullOrEmpty(cls) ? null : cls;
        }
        catch { return null; }
    }

    static string BlockList(List<Block> blocks)
    {
        return blocks.Count == 0 ? "(none)" : string.Join(", ", blocks.Select(b => b.Class == null ? b.Name : b.Name + " [" + b.Class + "]").ToArray());
    }

    // the block to place from an inventory item: by placeable name (its USB slots' too), by class, or its only one
    public static object Place(object owner, string placeable, string cls)
    {
        if (placeable != null)
        {
            var from = owner;
            if (!Q.L(Q.TryGet(owner, "PlaceableItems")).Any(p => Convert.ToString(Q.TryGet(p, "Name")) == placeable))
            {
                var named = Blocks(owner, false).FirstOrDefault(b => b.Name == placeable);
                if (named == null) throw new Exception("no placeable '" + placeable + "' on " + Label(owner) + "; has: " + BlockList(Blocks(owner, false)));
                from = named.Owner;
            }
            var c = Q.PlaceFromInventory(from, placeable);
            if (cls != null && Convert.ToString(Q.Get(c, "ClassName")) != cls)
                throw new Exception("placeable '" + placeable + "' on " + Label(owner) + " is a " + Q.Get(c, "ClassName") + ", not " + cls);
            return c;
        }
        var hits = Blocks(owner, false).Where(b => cls == null || b.Class == cls).ToList();
        if (cls != null && hits.Count == 0) hits = Blocks(owner, true).Where(b => b.Class == cls).ToList();   // classes not known up front
        if (hits.Count != 1)
            throw new Exception(Label(owner) + " has " + (hits.Count == 0 ? "no" : hits.Count.ToString()) + " blocks" + (cls == null ? "" : " of class " + cls)
                + "; name one with \"placeable\": " + BlockList(Blocks(owner, cls != null)));
        return Q.PlaceFromInventory(hits[0].Owner, hits[0].Name);
    }

    // ---- design properties --------------------------------------------------------------------------
    public static object DesignProps(object doc)
    {
        Func<Type, bool> isDp = t => t.Name == "DesignProperties";
        var p = Find(doc, isDp, "Properties", "DesignProperties") ?? Find(doc, isDp);
        var v = p == null ? null : Read(doc, p);
        return v ?? FieldUp(doc, "_Properties");
    }

    // ---- reports ------------------------------------------------------------------------------------
    // info: the feature flag the Python side checks, where the Core keeps its USB settings, USB bridge types
    public static void AddInfo(JObject res, object core, IEnumerable<Type> types)
    {
        var features = res["features"] as JArray ?? new JArray();
        features.Add("hardware");
        res["features"] = features;
        res["usb_api"] = Api(core);
        res["usb_bridge_types"] = new JArray(UsbBridgeTypes(types).Select(t => t.Name).ToArray());
    }

    // probe hardware=true: what can be set on the Core, its USB bridge and slot, and the design
    public static JObject Discover(object doc, object core, IEnumerable<Type> types)
    {
        var o = new JObject { { "core", Settable(core) } };
        AddUsb(o, core);
        var dp = DesignProps(doc);
        o["design"] = dp != null ? (JToken)Settable(dp) : (JToken)("found no DesignProperties; document properties: " + AllNames(doc));
        o["usb_bridge_types"] = new JArray(UsbBridgeTypes(types).Select(t => t.Name).ToArray());
        return o;
    }

    // probe: an inventory item's settable properties (and its USB bridge's)
    public static JObject ItemProbe(object item)
    {
        var o = new JObject { { "type", item.GetType().Name }, { "name", Label(item) }, { "settable", Settable(item) } };
        AddUsb(o, item);
        return o;
    }

    static void AddUsb(JObject o, object owner)
    {
        if (!HasUsb(owner)) return;
        o["usb"] = UsbInfo(owner, true, true);
        var b = Bridge(owner);
        if (b != null) o["usb_bridge"] = Settable(b);
        var slots = Slots(owner);
        o["usb_slot"] = slots.Count > 0 ? (JToken)Settable(slots[0]) : (JToken)"no USB slots yet: give a usb type or count to see one";
    }

    // read_design: core.usb, core.settings and design.settings
    public static void Describe(object doc, object core, JObject res)
    {
        var c = (JObject)res["core"];
        if (HasUsb(core)) c["usb"] = UsbInfo(core, false, false);
        c["settings"] = Values(core);
        var dp = DesignProps(doc);
        if (dp != null) res["design"] = new JObject { { "settings", Values(dp) } };
    }

    // read_design: an inventory item with its settings and USB (the Core's are under core)
    public static JObject ItemInfo(object item, object core)
    {
        var o = new JObject { { "type", item.GetType().Name }, { "name", Convert.ToString(Q.TryGet(item, "Name")) } };
        if (item == core) return o;
        o["settings"] = Values(item);
        if (HasUsb(item)) o["usb"] = UsbInfo(item, true, false);
        return o;
    }

    // settable properties and their values; for probe, enums also list their choices: "Value (A|B|C)"
    public static JObject Settable(object o)
    {
        var res = new JObject();
        foreach (var p in Writable(o))
        {
            object v;
            if (!TryRead(o, p, out v)) continue;
            var t = Und(p.PropertyType);
            res[p.Name] = t.IsEnum ? (JToken)(Convert.ToString(v) + " (" + Choices(t, "|") + ")") : Json(v);
        }
        return res;
    }

    public static JObject Values(object o)
    {
        var res = new JObject();
        foreach (var p in Writable(o)) { object v; if (TryRead(o, p, out v)) res[p.Name] = Json(v); }
        return res;
    }

    // ---- inventory types ----------------------------------------------------------------------------
    // by class name: exact, then ignoring case and punctuation, then the only one containing the name
    public static Type FindType(IEnumerable<Type> types, string name)
    {
        var all = types.ToList();
        var key = Norm(name);
        var t = all.FirstOrDefault(x => x.Name == name) ?? all.FirstOrDefault(x => Norm(x.Name) == key);
        if (t != null) return t;
        var near = key.Length < 3 ? new List<Type>() : all.Where(x => Norm(x.Name).Contains(key)).ToList();
        if (near.Count == 1) { Warnings.Add("inventory type '" + name + "' taken as " + near[0].Name); return near[0]; }
        throw new Exception("unknown inventory type '" + name + "' (see info.inventory_types)"
            + (near.Count > 0 ? "; did you mean " + string.Join(", ", near.Select(x => x.Name).ToArray()) : "")
            + "; types with a USB bridge: " + string.Join(", ", UsbBridgeTypes(all).Select(x => x.Name).ToArray()));
    }

    // ---- values -------------------------------------------------------------------------------------
    static object ToValue(Type type, JToken v, string what)
    {
        var t = Und(type);
        if (v == null || v.Type == JTokenType.Null)
        {
            if (!type.IsValueType || Nullable.GetUnderlyingType(type) != null) return null;
            throw new Exception(what + " can't be null");
        }
        if (t.IsEnum) return EnumValue(t, v, what);
        if (t == typeof(string)) return v.Type == JTokenType.String ? (string)v : v.ToString();
        if (v.Type != JTokenType.Integer && v.Type != JTokenType.Float && v.Type != JTokenType.Boolean && v.Type != JTokenType.String)
            throw new Exception(what + " takes a single value, not " + v.Type);
        if (t == typeof(bool))
        {
            bool b;
            if (v.Type == JTokenType.Boolean) return (bool)v;
            if (v.Type == JTokenType.String && bool.TryParse(((string)v).Trim(), out b)) return b;
            if (v.Type == JTokenType.Integer && ((long)v == 0 || (long)v == 1)) return (long)v == 1;
            throw new Exception(what + " takes true or false, not " + v);
        }
        if (t.IsPrimitive || t == typeof(decimal))
        {
            object raw = v.Type == JTokenType.Integer ? (object)(long)v : v.Type == JTokenType.Float ? (object)(double)v
                : v.Type == JTokenType.Boolean ? (object)((bool)v ? 1 : 0) : (object)((string)v).Trim();
            if (raw is double && IsWhole(t) && Math.Floor((double)raw) != (double)raw) throw new Exception(what + " takes a whole number, not " + v);
            try { return Convert.ChangeType(raw, t, CultureInfo.InvariantCulture); }
            catch (Exception e) { throw new Exception(what + " (" + t.Name + ") can't take " + v + ": " + e.Message); }
        }
        throw new Exception(what + " is a " + t.Name + "; only numbers, booleans, strings and enums can be set");
    }

    // an enum member from a name or a label (e.g. a [Description]), case, spaces and punctuation ignored,
    // or a number. A near miss that only one member fits ("12x80" for "NC-12x80") is taken, with a warning.
    public static object EnumValue(Type et, JToken v, string what)
    {
        var fields = et.GetFields(BindingFlags.Public | BindingFlags.Static);
        var text = v.Type == JTokenType.String ? (string)v : v.ToString();
        var key = Norm(text);
        if (v.Type == JTokenType.String)
        {
            foreach (var f in fields) if (Norm(f.Name) == key) return f.GetValue(null);
            foreach (var f in fields) foreach (var l in Labels(f)) if (Norm(l) == key) return f.GetValue(null);
        }
        long n = 0;
        bool number = v.Type == JTokenType.Integer;
        if (number) n = (long)v;
        else if (v.Type == JTokenType.Float && Math.Floor((double)v) == (double)v) { number = true; n = (long)(double)v; }
        else if (v.Type == JTokenType.String) number = long.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n);
        if (number)
        {
            try
            {
                var raw = Convert.ChangeType(n, Enum.GetUnderlyingType(et), CultureInfo.InvariantCulture);
                if (Enum.IsDefined(et, raw) || et.IsDefined(typeof(FlagsAttribute), false)) return Enum.ToObject(et, raw);
            }
            catch (OverflowException) { }
            throw new Exception(what + " has no value " + n + "; choices: " + string.Join(", ", fields.Select(f => f.Name + "=" + Convert.ToInt64(f.GetValue(null))).ToArray()));
        }
        FieldInfo hit = null;
        int best = 0;
        bool tie = false;
        foreach (var f in fields)
        {
            int s = Words(f).Max(w => Lcs(Norm(w), key));
            if (s > best) { best = s; hit = f; tie = false; }
            else if (s == best) tie = true;
        }
        if (hit != null && !tie && best >= Math.Max(4, (key.Length * 2 + 2) / 3))
        {
            Warnings.Add(what + ": took '" + text + "' as " + hit.Name);
            return hit.GetValue(null);
        }
        throw new Exception(what + " has no value '" + text + "'; choices: " + Choices(et, ", "));
    }

    // "Name" or "Name 'label'" for each member
    static string Choices(Type et, string sep)
    {
        return string.Join(sep, et.GetFields(BindingFlags.Public | BindingFlags.Static).Select(f =>
        {
            var l = Labels(f).FirstOrDefault(x => x.Trim().Length > 0 && Norm(x) != Norm(f.Name));
            return l == null ? f.Name : f.Name + " '" + l + "'";
        }).ToArray());
    }

    // strings in a member's attributes ([Description("PTZ-12x72")] and the like)
    static List<string> Labels(FieldInfo f)
    {
        var res = new List<string>();
        try
        {
            foreach (var a in f.GetCustomAttributesData())
            {
                if (a.Constructor.DeclaringType.Name.StartsWith("Obsolete")) continue;
                foreach (var arg in a.ConstructorArguments) if (arg.Value is string) res.Add((string)arg.Value);
                foreach (var arg in a.NamedArguments) if (arg.TypedValue.Value is string) res.Add((string)arg.TypedValue.Value);
            }
        }
        catch { }
        return res;
    }

    static List<string> Words(FieldInfo f) { var w = Labels(f); w.Insert(0, f.Name); return w; }

    // longest common substring
    static int Lcs(string a, string b)
    {
        int best = 0;
        var prev = new int[b.Length + 1];
        for (int i = 1; i <= a.Length; i++)
        {
            var cur = new int[b.Length + 1];
            for (int j = 1; j <= b.Length; j++)
                if (a[i - 1] == b[j - 1]) { cur[j] = prev[j - 1] + 1; if (cur[j] > best) best = cur[j]; }
            prev = cur;
        }
        return best;
    }

    // lower case, letters and digits only: "NC-12x80" -> "nc12x80"
    static string Norm(string s)
    {
        var sb = new StringBuilder();
        foreach (var ch in s ?? "") if (char.IsLetterOrDigit(ch)) sb.Append(char.ToLowerInvariant(ch));
        return sb.ToString();
    }

    static JToken Json(object v)
    {
        if (v == null) return new JValue((object)null);
        if (v is Enum) return new JValue(v.ToString());
        var s = v as string;
        if (s != null) return new JValue(s.Length > 300 ? s.Substring(0, 300) + "... (" + s.Length + " chars)" : s);
        try { return new JValue(v); } catch { return new JValue(Convert.ToString(v, CultureInfo.InvariantCulture)); }
    }

    static string Show(JToken t) { return t.Type == JTokenType.String ? (string)t : t.ToString(Newtonsoft.Json.Formatting.None); }

    // ---- reflection -----------------------------------------------------------------------------------
    // a property by name (case, spaces and _ ignored), public first; with no names, the first whose type fits
    static PropertyInfo Find(object o, Func<Type, bool> fits, params string[] names)
    {
        if (o == null) return null;
        foreach (var flags in new[] { Pub, BindingFlags.NonPublic | BindingFlags.Instance })
        {
            var props = o.GetType().GetProperties(flags).Where(p => p.GetIndexParameters().Length == 0 && (fits == null || fits(p.PropertyType))).ToList();
            if (names.Length == 0) { if (props.Count > 0) return props[0]; continue; }
            foreach (var n in names)
            {
                var key = Norm(n);
                var hit = props.FirstOrDefault(p => p.Name == n) ?? props.FirstOrDefault(p => Norm(p.Name) == key);
                if (hit != null) return hit;
            }
        }
        return null;
    }

    // a field declared on the object's class or a base class (private ones included)
    static object FieldUp(object o, string name)
    {
        for (var t = o.GetType(); t != null; t = t.BaseType)
        {
            var f = t.GetField(name, Q.All | BindingFlags.DeclaredOnly);
            if (f != null) return f.GetValue(o);
        }
        return null;
    }

    // public properties a spec can set: a setter, no index, a number, boolean, string or enum
    static List<PropertyInfo> Writable(object o)
    {
        if (o == null) return new List<PropertyInfo>();
        return o.GetType().GetProperties(Pub).Where(p => p.CanWrite && p.GetIndexParameters().Length == 0 && Scalar(p.PropertyType))
            .GroupBy(p => p.Name).Select(g => g.First()).OrderBy(p => p.Name).ToList();
    }

    static object Read(object o, PropertyInfo p) { try { return p.GetValue(o, null); } catch { return null; } }
    static bool TryRead(object o, PropertyInfo p, out object v) { try { v = p.GetValue(o, null); return true; } catch { v = null; return false; } }
    static int ToInt(object v) { try { return v == null ? 0 : Convert.ToInt32(v, CultureInfo.InvariantCulture); } catch { return 0; } }
    static string Label(object o) { var n = Convert.ToString(Q.TryGet(o, "Name")); return string.IsNullOrEmpty(n) ? o.GetType().Name : n; }

    static Type Und(Type t) { return Nullable.GetUnderlyingType(t) ?? t; }
    static bool Scalar(Type t) { t = Und(t); return t.IsEnum || t == typeof(string) || t == typeof(decimal) || (t.IsPrimitive && t != typeof(char) && t != typeof(IntPtr) && t != typeof(UIntPtr)); }
    static bool IsBool(Type t) { return Und(t) == typeof(bool); }
    static bool IsEnumType(Type t) { return Und(t).IsEnum; }
    static bool IsList(Type t) { return !Scalar(t) && typeof(IEnumerable).IsAssignableFrom(t); }
    static bool IsWhole(Type t)
    {
        t = Und(t);
        return t == typeof(int) || t == typeof(long) || t == typeof(short) || t == typeof(byte) || t == typeof(uint) || t == typeof(ulong) || t == typeof(ushort) || t == typeof(sbyte);
    }
    static Func<Type, bool> EnumLike(string fragment) { return t => Und(t).IsEnum && Und(t).Name.IndexOf(fragment, StringComparison.OrdinalIgnoreCase) >= 0; }

    // "Type: Name (Int32), ..." of what an object can set, for error messages
    static string Names(object o)
    {
        if (o == null) return "(none)";
        return o.GetType().Name + ": " + string.Join(", ", Writable(o).Select(p => p.Name + " (" + Und(p.PropertyType).Name + ")").ToArray());
    }

    static string AllNames(object o)
    {
        return string.Join(", ", o.GetType().GetProperties(Pub).Select(p => p.Name + " (" + p.PropertyType.Name + ")").ToArray());
    }

    // an object's update-like methods without arguments, for the error when the USB blocks don't show
    static string Methods(object o)
    {
        var keys = new[] { "update", "refresh", "rebuild", "usb", "slot", "placeable" };
        var names = o.GetType().GetMethods(Q.All).Where(m => !m.IsStatic && !m.IsSpecialName && m.GetParameters().Length == 0
            && keys.Any(k => m.Name.ToLowerInvariant().Contains(k))).Select(m => m.Name).Distinct().OrderBy(x => x).ToArray();
        return o.GetType().Name + ": " + (names.Length == 0 ? "(none)" : string.Join(", ", names));
    }

    // an error naming what wasn't found, with what can be set, so one run shows the right names
    static Exception Missing(object owner, string thing) { return new Exception(Label(owner) + ": found no " + thing + ". Settable: " + Settables(owner)); }

    // what the device, its USB bridge and its first USB slot can set
    static string Settables(object owner)
    {
        var b = Bridge(owner);
        var slots = Slots(owner);
        return Names(owner) + (b == null ? "" : "; USB bridge " + Names(b)) + (slots.Count == 0 ? "" : "; USB slot " + Names(slots[0]));
    }
}
