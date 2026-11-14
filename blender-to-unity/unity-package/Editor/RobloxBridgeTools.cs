using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace RobloxBridge
{
// Tools > Roblox Bridge: the steps after Import that act on the imported model in the open scene.
//   Lamp grid: Roblox's lamps without URP's per-object light limit (needed on Quest/Android).
//   Bake lamp grid into lightmaps: the same light, baked, so a Quest only reads a texture.
//   Map scale: the map at a fraction of its Roblox size; lights, look and lamp grid follow.
public static class RobloxBridgeTools
{
    // The first root object of the open scene that is an imported Roblox Bridge model.
    public static bool Target(out string fbx, out GameObject inst)
    {
        fbx = null; inst = null;
        foreach (var go in EditorSceneManager.GetActiveScene().GetRootGameObjects())
        {
            var src = PrefabUtility.GetCorrespondingObjectFromOriginalSource(go);
            var p = src != null ? AssetDatabase.GetAssetPath(src) : null;
            if (p != null && p.EndsWith(".fbx") && RobloxBridgeImporter.Load(p) != null) { fbx = p; inst = go; return true; }
        }
        return false;
    }

    static bool TargetOrSay(out string fbx, out GameObject inst)
    {
        if (Target(out fbx, out inst)) return true;
        EditorUtility.DisplayDialog("Roblox Bridge", "No imported Roblox model in the open scene. Import one first (Window > Roblox Bridge).", "OK");
        return false;
    }

    [MenuItem("Tools/Roblox Bridge/Lamp grid/Build")]
    static void BuildGrid()
    {
        if (!TargetOrSay(out _, out var inst)) return;
        Debug.Log("Roblox Bridge lamp grid: " + RobloxLampGridBuilder.Build(inst));
        SceneView.RepaintAll();
    }

    [MenuItem("Tools/Roblox Bridge/Lamp grid/On")] static void GridOn() => SetGrid(true);
    [MenuItem("Tools/Roblox Bridge/Lamp grid/Off")] static void GridOff() => SetGrid(false);

    static void SetGrid(bool on)
    {
        if (!TargetOrSay(out _, out var inst)) return;
        var g = inst.GetComponent<RobloxLampGrid>();
        if (g == null) { EditorUtility.DisplayDialog("Roblox Bridge", "Build the lamp grid first.", "OK"); return; }
        Undo.RecordObject(g, "Lamp grid");
        g.enabled = on;
        EditorSceneManager.MarkSceneDirty(inst.scene);
        SceneView.RepaintAll();
    }

    [MenuItem("Tools/Roblox Bridge/Bake lamp grid into lightmaps")]
    static void GridBake()
    {
        if (!TargetOrSay(out var fbx, out var inst)) return;
        if (!EditorUtility.DisplayDialog("Roblox Bridge", "Bake the lamp grid into lightmaps (100 texels per metre)? " +
                "It runs in the background and saves the scene when it's done.", "Bake", "Cancel")) return;
        var err = RobloxBridgeImporter.BakeGridLighting(fbx, inst, RobloxBridgeWindow.SavedTuning(), 100f);
        if (err != null) EditorUtility.DisplayDialog("Roblox Bridge", err, "OK");
    }

    [MenuItem("Tools/Roblox Bridge/Map scale/100%")] static void Scale100() => ScaleMenu(1f);
    [MenuItem("Tools/Roblox Bridge/Map scale/75%")] static void Scale75() => ScaleMenu(0.75f);
    [MenuItem("Tools/Roblox Bridge/Map scale/50%")] static void Scale50() => ScaleMenu(0.5f);
    [MenuItem("Tools/Roblox Bridge/Map scale/25%")] static void Scale25() => ScaleMenu(0.25f);

    static void ScaleMenu(float s)
    {
        if (!TargetOrSay(out var fbx, out var inst)) return;
        Debug.Log("Roblox Bridge " + ScaleMap(fbx, inst, s));
    }

    // The map at s x its Roblox size (0.5 = half). Reimports it at that scale, scales the lights to match
    // (each keeps its own range/shadow tweaks), re-applies the saved look and rebuilds the lamp grid.
    public static string ScaleMap(string fbx, GameObject inst, float s)
    {
        float ratio = s / RobloxBridgeImporter.MapScale(fbx);
        var before = inst.GetComponentsInChildren<Light>(true).Select(l => new { path = AnimationUtility.CalculateTransformPath(l.transform, inst.transform), l.range, l.shadowNearPlane }).ToList();
        // objects added to the model in the scene keep their local positions: move them with the map
        // (the reflection probes are rebuilt by the apply below)
        var added = inst.GetComponentsInChildren<Transform>(true).Where(x => x != inst.transform && PrefabUtility.IsAddedGameObjectOverride(x.gameObject)
            && !PrefabUtility.IsAddedGameObjectOverride(x.parent.gameObject)).ToList();
        foreach (var x in added) x.localPosition *= ratio;
        // pieces moved by hand in the scene keep their overridden position: scale the overridden axes with the map
        var mods = PrefabUtility.GetPropertyModifications(inst) ?? new PropertyModification[0];
        var moved = new System.Collections.Generic.List<Transform>();
        foreach (var x in inst.GetComponentsInChildren<Transform>(true))
        {
            if (x == inst.transform || PrefabUtility.IsAddedGameObjectOverride(x.gameObject)) continue;
            var src = PrefabUtility.GetCorrespondingObjectFromSource(x);
            var axes = mods.Where(m => m.target == src && m.propertyPath.StartsWith("m_LocalPosition.")).Select(m => m.propertyPath[m.propertyPath.Length - 1]).ToList();
            if (axes.Count == 0) continue;
            var lp = x.localPosition;
            if (axes.Contains('x')) lp.x *= ratio;
            if (axes.Contains('y')) lp.y *= ratio;
            if (axes.Contains('z')) lp.z *= ratio;
            x.localPosition = lp;
            moved.Add(x);
        }
        RobloxBridgeImporter.SetMapScale(fbx, s);
        int scaled = 0;
        foreach (var b in before)
        {
            var t2 = inst.transform.Find(b.path);
            var l = t2 != null ? t2.GetComponent<Light>() : null;
            if (l == null) continue;
            l.range = b.range * ratio;
            l.shadowNearPlane = Mathf.Max(0.1f, b.shadowNearPlane * ratio);
            scaled++;
        }
        RobloxBridgeImporter.ApplySceneLighting(fbx, inst, RobloxBridgeWindow.SavedTuning());
        EditorSceneManager.MarkSceneDirty(inst.scene);
        SceneView.RepaintAll();
        return "map scale " + s + ": " + scaled + " lights, " + added.Count + " added and " + moved.Count + " moved objects scaled; lamp grid: " + RobloxLampGridBuilder.Build(inst);
    }
}
}
