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
public partial class RobloxBridgeWindow : EditorWindow
{
    const string PrefFolder = "RobloxBridge.SourceFolder";
    const string PrefDest = "RobloxBridge.DestFolder";
    const string PrefLast = "RobloxBridge.LastFbx";

    string sourceFolder;
    string destRoot;
    List<string> found = new List<string>();
    int pick;
    bool addToScene = true;
    bool keepEditedMaterials = true;
    string status = "";
    Vector2 scroll;

    // Filled in by the Roblox lighting files, when they are in the package
    partial void OnEnableLighting();
    partial void DrawLightingOptions();
    partial void DrawLightingSection(ref bool shown);
    partial void DrawLightingTools();
    partial void AfterImport(string fbx, GameObject inst, ref bool lit);

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
        OnEnableLighting();
        Scan();
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
        DrawLightingOptions();
        keepEditedMaterials = EditorGUILayout.Toggle("Keep materials I edited", keepEditedMaterials);

        EditorGUILayout.Space();
        using (new EditorGUI.DisabledScope(found.Count == 0))
            if (GUILayout.Button("Import", GUILayout.Height(32))) Import(found[pick]);

        EditorGUILayout.Space();
        bool shown = false;
        DrawLightingSection(ref shown);
        if (!shown)
        {
            EditorGUILayout.LabelField("4. Roblox lighting", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox("Coming soon: Roblox's own lighting (light falloff, colours, tonemapper, sky light and baking). The model's lights are imported as plain Unity lights for now.", MessageType.Info);
        }

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Tools for a model already in the project", EditorStyles.boldLabel);
        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("Rebuild materials of selected")) ForSelected(fbx => status = RobloxBridgeImporter.BuildMaterials(fbx, true) + " materials rebuilt");
            DrawLightingTools();
        }
        if (!string.IsNullOrEmpty(status)) EditorGUILayout.HelpBox(status, MessageType.Info);
        EditorGUILayout.EndScrollView();
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
            bool lit = false;
            AfterImport(fbx, inst, ref lit);
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
