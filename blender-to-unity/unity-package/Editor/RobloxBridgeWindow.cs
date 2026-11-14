// Roblox Bridge Importer window: Window > Roblox Bridge.
// Open an export folder (made by Blender's "Export FBX for Unity"), pick the model, Import.
#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace RobloxBridge
{
public class RobloxBridgeWindow : EditorWindow
{
    const string PrefFolder = "RobloxBridge.SourceFolder";
    const string PrefDest = "RobloxBridge.DestFolder";

    string sourceFolder;
    string destRoot;
    List<string> found = new List<string>();
    int pick;
    bool addToScene = true;
    bool applyLighting = true;
    bool keepEditedMaterials = true;
    string status = "";
    RobloxBridgeImporter.Tuning tune = new RobloxBridgeImporter.Tuning();
    const string PrefTune = "RobloxBridge.Tuning";
    const string PrefLast = "RobloxBridge.LastFbx";
    Vector2 scroll;

    [MenuItem("Window/Roblox Bridge")]
    public static void Open()
    {
        var w = GetWindow<RobloxBridgeWindow>("Roblox Bridge");
        w.minSize = new Vector2(380, 300);
        w.Show();
    }

    void OnEnable()
    {
        sourceFolder = EditorPrefs.GetString(PrefFolder,
            Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile), "RobloxExports", "Unity"));
        destRoot = EditorPrefs.GetString(PrefDest, "Assets/Roblox");
        tune = SavedTuning();
        Shader.SetGlobalFloat("_RbxFalloffPower", tune.falloff);   // reset by script reloads
        Shader.SetGlobalFloat("_RbxFalloffK", RobloxBridgeImporter.FalloffK(tune, RobloxBridgeImporter.StudScale(EditorPrefs.GetString(PrefLast, ""))));
        Scan();
    }

    /// The settings this window last saved (the look the scene was given)
    public static RobloxBridgeImporter.Tuning SavedTuning()
    {
        var tune = new RobloxBridgeImporter.Tuning();
        var tv = EditorPrefs.GetString(PrefTune, "1,1,1,1,1,1").Split(',');
        float f;
        if (tv.Length >= 6)
        {
            if (float.TryParse(tv[0], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.light = f;
            if (float.TryParse(tv[1], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.range = f;
            if (float.TryParse(tv[2], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.sun = f;
            if (float.TryParse(tv[3], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.ambient = f;
            if (float.TryParse(tv[4], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.haze = f;
            tune.post = tv[5] != "0";
            if (tv.Length >= 7) tune.useSun = tv[6] != "0";
            if (tv.Length >= 8) tune.bakeLights = tv[7] != "0";
            if (tv.Length >= 9) tune.robloxStyle = tv[8] != "0";
            if (tv.Length >= 10 && float.TryParse(tv[9], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.falloff = f;
            int ti;
            if (tv.Length >= 11 && int.TryParse(tv[10], out ti)) tune.tonemap = ti;
            if (tv.Length >= 12 && float.TryParse(tv[11], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.saturation = f;
            if (tv.Length >= 13 && float.TryParse(tv[12], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.bigRange = f;
            if (tv.Length >= 14 && float.TryParse(tv[13], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.sky = f;
            if (tv.Length >= 15) tune.exact = tv[14] != "0";
            if (tv.Length >= 16 && float.TryParse(tv[15], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out f)) tune.skyLeak = f;
        }
        return tune;
    }

    // every <name>.fbx that has a <name>.roblox.json next to it (folder itself + one level down)
    void Scan()
    {
        found.Clear();
        if (string.IsNullOrEmpty(sourceFolder) || !Directory.Exists(sourceFolder)) return;
        var dirs = new List<string> { sourceFolder };
        try { dirs.AddRange(Directory.GetDirectories(sourceFolder)); } catch { }
        foreach (var d in dirs)
            foreach (var json in Directory.GetFiles(d, "*.roblox.json"))
            {
                var fbx = json.Substring(0, json.Length - ".roblox.json".Length) + ".fbx";
                if (File.Exists(fbx)) found.Add(fbx);
            }
        found.Sort();
        pick = Mathf.Clamp(pick, 0, Mathf.Max(0, found.Count - 1));
    }

    void OnGUI()
    {
        scroll = EditorGUILayout.BeginScrollView(scroll);
        EditorGUILayout.LabelField("1. Export folder", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox("The folder Blender's \"Export FBX for Unity\" saved to (it holds <name>.fbx, <name>.roblox.json and <name>_Textures).", MessageType.None);
        using (new EditorGUILayout.HorizontalScope())
        {
            var nf = EditorGUILayout.TextField(sourceFolder);
            if (nf != sourceFolder) { sourceFolder = nf; EditorPrefs.SetString(PrefFolder, sourceFolder); Scan(); }
            if (GUILayout.Button("Browse…", GUILayout.Width(80)))
            {
                var f = EditorUtility.OpenFolderPanel("Roblox Bridge export folder", Directory.Exists(sourceFolder) ? sourceFolder : "", "");
                if (!string.IsNullOrEmpty(f)) { sourceFolder = f; EditorPrefs.SetString(PrefFolder, f); Scan(); }
            }
            if (GUILayout.Button("↻", GUILayout.Width(26))) Scan();
        }

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("2. Model", EditorStyles.boldLabel);
        if (found.Count == 0)
            EditorGUILayout.HelpBox("No export found in that folder. Pick the folder that contains the .fbx and .roblox.json.", MessageType.Warning);
        else
        {
            var names = found.ConvertAll(p => Path.GetFileNameWithoutExtension(p) + "   (" + Path.GetFileName(Path.GetDirectoryName(p)) + ")");
            pick = EditorGUILayout.Popup(pick, names.ToArray());
        }

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("3. Options", EditorStyles.boldLabel);
        var nd = EditorGUILayout.TextField("Project folder", destRoot);
        if (nd != destRoot) { destRoot = nd; EditorPrefs.SetString(PrefDest, destRoot); }
        addToScene = EditorGUILayout.Toggle("Add model to open scene", addToScene);
        applyLighting = EditorGUILayout.Toggle("Apply Roblox sun/ambient/fog", applyLighting);
        keepEditedMaterials = EditorGUILayout.Toggle("Keep materials I edited", keepEditedMaterials);

        EditorGUILayout.Space();
        using (new EditorGUI.DisabledScope(found.Count == 0))
            if (GUILayout.Button("Import", GUILayout.Height(32))) Import(found[pick]);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("4. Match Studio's look (live)", EditorStyles.boldLabel);
        EditorGUI.BeginChangeCheck();
        bool wasStyle = tune.robloxStyle, wasSun = tune.useSun, wasExact = tune.exact;
        tune.robloxStyle = EditorGUILayout.Toggle(new GUIContent("Roblox style", "Flat Roblox look: lights stay bright out to their range, hard shadows, no ambient occlusion, bounce light or camera effects. Off = Unity's realistic look."), tune.robloxStyle);
        using (new EditorGUI.DisabledScope(!tune.robloxStyle))
            tune.exact = EditorGUILayout.Toggle(new GUIContent("Exact Roblox formulas", "Roblox Future's lighting, measured in Studio: lights at full brightness to 72% of Range then fading to 0 at Range, Roblox's spotlight cone, and colours decoded with gamma 2. Off = the adjustable falloff curve below."), tune.exact);
        using (new EditorGUI.DisabledScope(!tune.robloxStyle || tune.exact))
            tune.falloff = EditorGUILayout.Slider(new GUIContent("Light falloff", "How fast light fades towards its Range (Roblox style). Higher = darker away from the light."), tune.falloff, 0.5f, 6f);
        tune.light = EditorGUILayout.Slider("Light brightness ×", tune.light, 0f, 10f);
        tune.range = EditorGUILayout.Slider("Light range ×", tune.range, 0.25f, 4f);
        tune.bigRange = EditorGUILayout.Slider(new GUIContent("Big light range ×", "Extra range for big lights only (Roblox Range over ~21 studs, mostly outdoor lamps). Indoor fixtures are not affected."), tune.bigRange, 0.5f, 4f);
        tune.useSun = EditorGUILayout.Toggle("Use sun", tune.useSun);
        using (new EditorGUI.DisabledScope(!tune.useSun))
            tune.sun = EditorGUILayout.Slider("Sun brightness ×", tune.sun, 0f, 4f);
        tune.ambient = EditorGUILayout.Slider("Ambient ×", tune.ambient, 0f, 4f);
        using (new EditorGUI.DisabledScope(!tune.robloxStyle))
            tune.sky = EditorGUILayout.Slider(new GUIContent("Sky light ×", "Light from the sky on places that can see it (outside). Shows after \"Bake sky lighting\"; re-bake after changing it."), tune.sky, 0f, 4f);
        using (new EditorGUI.DisabledScope(!tune.robloxStyle || !tune.exact))
            tune.skyLeak = EditorGUILayout.Slider(new GUIContent("Sky leak indoors", "Extra sky light let into rooms. 0 = only where Unity's bake sees the sky (Studio's interiors don't change with the sky; 1 tints closed rooms and elevators blue). Needs the place's .ambient.json calibration."), tune.skyLeak, 0f, 1f);
        tune.haze = EditorGUILayout.Slider("Haze ×", tune.haze, 0f, 4f);
        tune.post = EditorGUILayout.Toggle("Post effects (bloom, exposure, colour)", tune.post);
        using (new EditorGUI.DisabledScope(!tune.post))
        {
            using (new EditorGUI.DisabledScope(!tune.robloxStyle))
                tune.tonemap = EditorGUILayout.Popup(new GUIContent("Colour curve", "Tone mapping. Roblox = Roblox's own curve (measured in Studio). ACES is filmic, Neutral softer and paler, None clips bright areas."), tune.tonemap, new[] { "None", "Neutral", "ACES", "Roblox" });
            tune.saturation = EditorGUILayout.Slider("Saturation", tune.saturation, -100f, 100f);
        }
        if (EditorGUI.EndChangeCheck())
        {
            SaveTune();
            ApplyTuning(tune.robloxStyle != wasStyle || tune.useSun != wasSun || tune.exact != wasExact);   // toggles do a full apply (project/post assets, shadows)
        }
        if (GUILayout.Button("Apply look to scene")) ApplyTuning(true);
        EditorGUILayout.HelpBox("Baking puts the sky light only where the sky is visible (like Roblox) and, with \"Bake lights too\", makes every light show at any distance. Save the scene first; it takes a few minutes.", MessageType.None);
        var bl = EditorGUILayout.Toggle("Bake lights too", tune.bakeLights);
        if (bl != tune.bakeLights) { tune.bakeLights = bl; SaveTune(); }
        using (new EditorGUI.DisabledScope(Lightmapping.isRunning))
            if (GUILayout.Button(Lightmapping.isRunning ? "Baking…" : "Bake sky lighting"))
            {
                string fbx; GameObject inst;
                if (!Target(out fbx, out inst)) status = "Import a model first (or select it in the scene).";
                else status = RobloxBridgeImporter.BakeSkyLighting(fbx, inst, tune) ?? "Baking sky lighting… (progress bar at the bottom right of Unity)";
            }

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Tools for a model already in the project", EditorStyles.boldLabel);
        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("Rebuild materials of selected")) ForSelected(fbx => status = RobloxBridgeImporter.BuildMaterials(fbx, true) + " materials rebuilt");
            if (GUILayout.Button("Apply lighting of selected")) ForSelected(fbx => status = RobloxBridgeImporter.ApplySceneLighting(fbx, Selection.activeGameObject != null && Selection.activeGameObject.scene.IsValid() ? Selection.activeGameObject : null) ? "Lighting applied" : "No lighting in this export");
        }
        if (!string.IsNullOrEmpty(status)) EditorGUILayout.HelpBox(status, MessageType.Info);
        EditorGUILayout.EndScrollView();
    }

    /// Replace this window's settings (used by the remote control), save and apply them.
    public static void SetTuning(RobloxBridgeImporter.Tuning t)
    {
        EditorPrefs.SetString(PrefTune, TuneCsv(t));
        foreach (var w in Resources.FindObjectsOfTypeAll<RobloxBridgeWindow>()) { w.tune = t; w.Repaint(); }
    }

    void SaveTune()
    {
        EditorPrefs.SetString(PrefTune, TuneCsv(tune));
    }

    static string TuneCsv(RobloxBridgeImporter.Tuning tune)
    {
        return string.Join(",", new[] { tune.light, tune.range, tune.sun, tune.ambient, tune.haze }
            .Select(x => x.ToString(System.Globalization.CultureInfo.InvariantCulture))) + "," + (tune.post ? "1" : "0") + "," + (tune.useSun ? "1" : "0") + "," + (tune.bakeLights ? "1" : "0") + "," + (tune.robloxStyle ? "1" : "0")
            + "," + tune.falloff.ToString(System.Globalization.CultureInfo.InvariantCulture)
            + "," + tune.tonemap + "," + tune.saturation.ToString(System.Globalization.CultureInfo.InvariantCulture)
            + "," + tune.bigRange.ToString(System.Globalization.CultureInfo.InvariantCulture)
            + "," + tune.sky.ToString(System.Globalization.CultureInfo.InvariantCulture)
            + "," + (tune.exact ? "1" : "0")
            + "," + tune.skyLeak.ToString(System.Globalization.CultureInfo.InvariantCulture);
    }

    // the model to tune: the selected one, else the last imported one found in the scene
    bool Target(out string fbx, out GameObject inst)
    {
        fbx = null; inst = null;
        var go = Selection.activeGameObject;
        if (go != null && go.scene.IsValid())
        {
            var root = PrefabUtility.GetOutermostPrefabInstanceRoot(go);
            if (root == null) root = go;
            var src = PrefabUtility.GetCorrespondingObjectFromOriginalSource(root);
            if (src != null && RobloxBridgeImporter.Load(AssetDatabase.GetAssetPath(src)) != null) { fbx = AssetDatabase.GetAssetPath(src); inst = root; return true; }
        }
        fbx = EditorPrefs.GetString(PrefLast, "");
        if (RobloxBridgeImporter.Load(fbx) == null) return false;
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(fbx);
        foreach (var r in UnityEngine.SceneManagement.SceneManager.GetActiveScene().GetRootGameObjects())
            if (prefab != null && PrefabUtility.GetCorrespondingObjectFromOriginalSource(r) == prefab) { inst = r; break; }
        return inst != null;
    }

    void ApplyTuning(bool loud)
    {
        string fbx; GameObject inst;
        if (!Target(out fbx, out inst)) { if (loud) status = "Import a model first (or select it in the scene)."; return; }
        tune.quick = !loud;
        int n = RobloxBridgeImporter.TuneLights(fbx, inst, tune);
        bool lit = RobloxBridgeImporter.ApplySceneLighting(fbx, inst, tune);
        tune.quick = false;
        status = string.Format("Look applied to {0}: {1} lights{2}", inst.name, n, lit ? ", sun/sky/ambient/haze/post" : "");
        SceneView.RepaintAll();
    }

    void ForSelected(System.Action<string> act)
    {
        string fbx = null;
        var go = Selection.activeGameObject;
        if (go != null && go.scene.IsValid())
        {
            var src = PrefabUtility.GetCorrespondingObjectFromOriginalSource(go);
            if (src != null) fbx = AssetDatabase.GetAssetPath(src);
        }
        else if (Selection.activeObject != null) fbx = AssetDatabase.GetAssetPath(Selection.activeObject);
        if (RobloxBridgeImporter.Load(fbx) == null) { status = "Select a model imported with Roblox Bridge (in the scene or the Project window)."; return; }
        act(fbx);
    }

    static void CopyFile(string src, string dst)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(dst));
        File.Copy(src, dst, true);
    }

    /// Import an exported FBX (with its sidecar and textures next to it) as the Import button does (remote control)
    public static string ImportFrom(string srcFbx)
    {
        var w = GetWindow<RobloxBridgeWindow>();
        w.Import(srcFbx);
        return w.status;
    }

    void Import(string srcFbx)
    {
        string stem = Path.GetFileNameWithoutExtension(srcFbx);
        string srcDir = Path.GetDirectoryName(srcFbx);
        string root = destRoot.Replace('\\', '/').TrimEnd('/');
        if (!root.StartsWith("Assets")) { status = "Project folder must start with Assets"; return; }
        string dst = root + "/" + stem;
        string dstAbs = Path.GetFullPath(dst);
        try
        {
            EditorUtility.DisplayProgressBar("Roblox Bridge", "Copying " + stem, 0.1f);
            CopyFile(srcFbx, Path.Combine(dstAbs, stem + ".fbx"));
            CopyFile(Path.Combine(srcDir, stem + ".roblox.json"), Path.Combine(dstAbs, stem + ".roblox.json"));
            string tex = Path.Combine(srcDir, stem + "_Textures");
            if (Directory.Exists(tex))
            {
                string[] files = Directory.GetFiles(tex);
                for (int i = 0; i < files.Length; i++)
                {
                    if (i % 25 == 0) EditorUtility.DisplayProgressBar("Roblox Bridge", "Copying textures", 0.1f + 0.3f * i / files.Length);
                    CopyFile(files[i], Path.Combine(dstAbs, stem + "_Textures", Path.GetFileName(files[i])));
                }
            }

            EditorUtility.DisplayProgressBar("Roblox Bridge", "Importing (this can take a while)", 0.45f);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            string fbx = dst + "/" + stem + ".fbx";
            AssetDatabase.ImportAsset(fbx, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);

            EditorUtility.DisplayProgressBar("Roblox Bridge", "Building materials", 0.75f);
            int n = RobloxBridgeImporter.BuildMaterials(fbx, !keepEditedMaterials);

            GameObject inst = null;
            if (addToScene)
            {
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(fbx);
                if (prefab != null)
                {
                    var old = GameObject.Find(stem);
                    if (old != null && PrefabUtility.GetCorrespondingObjectFromOriginalSource(old) == prefab) inst = old;
                    else
                    {
                        inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
                        Undo.RegisterCreatedObjectUndo(inst, "Import " + stem);
                    }
                    Selection.activeGameObject = inst;
                }
            }
            EditorPrefs.SetString(PrefLast, fbx);
            if (inst != null) RobloxBridgeImporter.TuneLights(fbx, inst, tune);
            bool lit = applyLighting && RobloxBridgeImporter.ApplySceneLighting(fbx, inst, tune);
            var side = RobloxBridgeImporter.Load(fbx);
            int lights = side != null && side.lights != null ? side.lights.Length : 0;
            status = string.Format("Imported {0}: {1} materials, {2} lights{3}. Files in {4}", stem, n, lights, lit ? ", scene lighting applied" : "", dst);
            Debug.Log("RobloxBridge: " + status);
        }
        catch (System.Exception e)
        {
            status = "Import failed: " + e.Message;
            Debug.LogException(e);
        }
        finally
        {
            EditorUtility.ClearProgressBar();
        }
    }
}
}
#endif
