// Roblox Bridge Importer - core (import settings, lights, materials, scene lighting).
// The Roblox Bridge window (Window > Roblox Bridge) drives it; nothing here runs on its own
// except Unity's import settings/lights for FBX files that come with a .roblox.json.
#if UNITY_EDITOR
using System.Collections.Generic;
using System.Linq;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using GI = UnityEngine.Experimental.GlobalIllumination;

namespace RobloxBridge
{
public class RobloxBridgeImporter : AssetPostprocessor
{
    [System.Serializable]
    public class MatInfo
    {
        public string name;
        public string mode;
        public float metallic;
        public float smoothness = 0.5f;
        public string metallicSmoothness;
        public float[] emission;
        public float alpha = 1f;
        public string emissionMap;
        public float emissionStrength;
        public string baseMap;
        public string normalMap;
        public float[] color;
        public float[] tint;   // shared texture x this colour (linear)
    }

    [System.Serializable]
    public class LightInfo
    {
        public string marker;
        public string kind;
        public string name;
        public bool enabled = true;
        public float[] color;
        public float brightness = 1f;
        public float range = 2f;
        public float angle = 90f;
        public bool shadows;
        public string owner;   // object name of the part the light belongs to
        public string path;    // Roblox full name, e.g. Workspace.Lobby.Lamp.PointLight
        public string face;    // Spot/SurfaceLight Face
        public float[] faceSize;   // SurfaceLight: lit face width, height (metres)
        public PropInfo[] props;   // every Roblox property + attributes (JSON text values)
    }

    [System.Serializable] public class PropInfo { public string name, value; }
    [System.Serializable] public class EffectInfo { public string @class, name, parent; public PropInfo[] props; }

    [System.Serializable] public class SkyMaps { public string bk, dn, ft, lf, rt, up; }
    [System.Serializable] public class BloomInfo { public float intensity = 1f, size = 24f, threshold = 2f; }
    [System.Serializable] public class ColorCorrectionInfo { public float brightness, contrast, saturation; public float[] tint; }
    [System.Serializable] public class AtmosphereInfo { public float density, haze; public float[] color; }

    /// Multipliers you can tune in the window to match Studio by eye.
    public class Tuning
    {
        public float light = 1f, range = 1f, sun = 1f, ambient = 1f, haze = 1f;
        public bool post = true;
        public bool useSun = true;
        public bool bakeLights = true;   // bake the Roblox lights into lightmaps (visible at any distance)
        public float lampTexelsPerMetre = 0f;     // lamp bake: lightmap density override (0 = LampTexelsPerMetre)
        public bool lampDirectional = false;     // lamp bake: directional lightmaps (normal maps keep their lamp shading)
        public bool bakeLampShadows = true;      // lamp bake: shadows on (test switch: off to compare the light maths alone)
        public bool outdoorLampShadows = false;   // Roblox style: lamps with nothing overhead cast no shadows
        public bool robloxStyle = true;  // Roblox's flat look: even light falloff, hard shadows, no AO/bounce/camera effects
        public float falloff = 2f;       // Roblox Lit curve (1 - (d/range)^2)^falloff
        public bool exact = true;        // Roblox style: Roblox Future's own falloff + gamma-2 colours instead of the falloff curve
        public float skyLeak = 0f;       // exact + calibration: extra sky let into rooms (0..1). Studio's interiors don't change with the sky, so 0 (1 tinted closed rooms blue)
        public float sky = 1f;           // Roblox style: sky light (EnvironmentDiffuseScale) x, baked so it's occluded
        public float bigRange = 1f;      // extra range x for big lights (Roblox Range > BigLightRange, mostly outdoor)
        public int tonemap = 3;          // 0 None, 1 Neutral, 2 ACES, 3 Roblox (its own curve, Shaders/RobloxTonemap.shader)
        public float saturation;         // added to Roblox's ColorCorrection saturation (-100..100)
        [System.NonSerialized] public bool quick;  // slider drag: don't rebuild sky/post assets
    }

    [System.Serializable]
    public class LightingInfo
    {
        public float[] sunDirection;
        public float brightness = 2f;
        public float[] ambient;
        public float[] outdoorAmbient;
        public float[] colorShiftTop;
        public float[] fogColor;
        public float fogStart;
        public float fogEnd = 100000f;
        public float studScale = 0.28f;
        public bool globalShadows = true;
        public float exposure;
        public float environmentDiffuseScale;
        public float environmentSpecularScale;
        public SkyMaps skyMaps;
        public BloomInfo bloom;
        public ColorCorrectionInfo colorCorrection;
        public AtmosphereInfo atmosphere;
        public float[] colorShiftBottom;
        public float shadowSoftness = 0.2f;
        public string tonemapper;          // ColorGradingEffect preset: Default / Retro
        public PropInfo[] props;           // the whole Lighting service as exported
        public EffectInfo[] effects;       // Sky, Atmosphere, Bloom, ColorCorrection, SunRays, DoF, Clouds...
    }

    [System.Serializable]
    public class Sidecar
    {
        public int version;
        public string source;
        public MatInfo[] materials;
        public LightInfo[] lights;
        public LightingInfo lighting;
        public string[] noShadow;   // objects whose Roblox part has CastShadow off
    }

    // Roblox Brightness -> Unity intensity (Roblox default light Brightness 1, sun Brightness 2)
    const float LightIntensityScale = 1.0f;
    const float SunIntensityScale = 0.5f;

    static Color C(float[] c, Color fallback)
    {
        return (c != null && c.Length >= 3) ? new Color(c[0], c[1], c[2]) : fallback;
    }

    public static string Dir(string p) { return Path.GetDirectoryName(p).Replace('\\', '/'); }
    public static string Stem(string p) { return Path.GetFileNameWithoutExtension(p); }
    public static string SidecarPath(string fbx) { return Dir(fbx) + "/" + Stem(fbx) + ".roblox.json"; }

    public static Sidecar Load(string fbx)
    {
        if (string.IsNullOrEmpty(fbx) || !fbx.EndsWith(".fbx", System.StringComparison.OrdinalIgnoreCase)) return null;
        var p = SidecarPath(fbx);
        if (!File.Exists(p)) return null;
        try { return JsonUtility.FromJson<Sidecar>(File.ReadAllText(p)); }
        catch (System.Exception e) { Debug.LogWarning("RobloxBridge: bad sidecar " + p + ": " + e.Message); return null; }
    }

    // ------------------------------------------------------------- model / textures
    void OnPreprocessModel()
    {
        if (Load(assetPath) == null) return;
        var mi = (ModelImporter)assetImporter;
        mi.globalScale = MapScale(assetPath);   // the whole map, lights included (see MapScale)
        mi.useFileScale = true;
        mi.bakeAxisConversion = true;
        mi.importNormals = ModelImporterNormals.Import;
        mi.importTangents = ModelImporterTangents.CalculateMikk;
        mi.generateSecondaryUV = false;   // Blender's UVLightmap (Unity's own with calculated margins tested: 0.101 vs 0.087)
        // userData "lightmapUV=unity" (maps baked for Quest): Unity's lightmap UVs with margins worked out for the
        // bake's texel density. Blender's islands are ~1% apart (about a texel), so filtering bled each face's light
        // into the next face's edge: a dark line along every part edge of a baked map.
        float lmTexels = LightmapUVTexels(assetPath);
        if (lmTexels > 0f)
        {
            mi.generateSecondaryUV = true;
            mi.secondaryUVMarginMethod = ModelImporterSecondaryUVMarginMethod.Calculate;
            mi.secondaryUVMinLightmapResolution = lmTexels;
            mi.secondaryUVMinObjectScale = 1f;
            mi.secondaryUVHardAngle = 66f;
        }
        mi.weldVertices = false;
        mi.meshCompression = ModelImporterMeshCompression.Off;
        mi.materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
        mi.importAnimation = false;
        mi.animationType = ModelImporterAnimationType.None;
        mi.importCameras = false;
        mi.importLights = false;
        mi.importBlendShapes = false;
    }

    void OnPreprocessTexture()
    {
        if (!assetPath.Contains("_Textures/")) return;
        var ti = (TextureImporter)assetImporter;
        var n = Path.GetFileNameWithoutExtension(assetPath);
        ti.maxTextureSize = 4096;
        ti.wrapMode = TextureWrapMode.Repeat;
        ti.textureCompression = TextureImporterCompression.CompressedHQ;
        if (n.EndsWith("_Normal")) ti.textureType = TextureImporterType.NormalMap;
        else if (n.EndsWith("_MetallicSmoothness")) ti.sRGBTexture = false;
        else if (n.EndsWith("_BaseColor")) ti.alphaIsTransparency = true;
        // decals are small textures stretched over big faces and often seen at an angle (signs): without
        // anisotropic filtering the mip chain blurs their lettering
        if (n.StartsWith("Decal_")) ti.anisoLevel = 8;
        else if (n.EndsWith("_Sky")) { ti.wrapMode = TextureWrapMode.Clamp; ti.mipmapEnabled = false; ti.maxTextureSize = 2048; }
    }

    // ------------------------------------------------------------- lights
    // Marker empties from the FBX (RBXL_<n> + RBXL_<n>_aim) become real Lights in the model.
    void OnPostprocessModel(GameObject root)
    {
        var side = Load(assetPath);
        if (side == null) return;
        var byName = new Dictionary<string, Transform>();
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
            if (!byName.ContainsKey(t.name)) byName[t.name] = t;

        if (side.lights != null)
        {
            foreach (var info in side.lights)
            {
                Transform t, aim;
                if (string.IsNullOrEmpty(info.marker) || !byName.TryGetValue(info.marker, out t)) continue;
                if (byName.TryGetValue(info.marker + "_aim", out aim))
                {
                    var dir = aim.position - t.position;
                    if (dir.sqrMagnitude > 1e-8f) t.rotation = Quaternion.LookRotation(dir.normalized);
                    Object.DestroyImmediate(aim.gameObject);
                }
                // keep the marker id in the name: the window maps lights back to the export with it
                t.gameObject.name = info.marker + " " + (string.IsNullOrEmpty(info.name) ? "Light" : info.name) + " (" + info.kind + ")";
                var l = t.gameObject.AddComponent<Light>();
                l.type = info.kind == "PointLight" ? LightType.Point : LightType.Spot;
                l.color = C(info.color, Color.white);
                l.intensity = Mathf.Max(0f, info.brightness) * LightIntensityScale;
                l.range = Mathf.Max(0.01f, info.range * MapScale(assetPath));
                if (l.type == LightType.Spot)
                {
                    l.spotAngle = Mathf.Clamp(info.angle, 1f, 179f);
                    l.innerSpotAngle = l.spotAngle * 0.8f;
                }
                l.shadows = info.shadows ? LightShadows.Soft : LightShadows.None;
                l.enabled = info.enabled;
                var data = t.gameObject.AddComponent<RobloxBridge.RobloxData>();
                data.entries.Add(Entry(info.kind, info.name, info.path, info.props));
                // Roblox lights aren't blocked by the part they sit in; Unity's would be
                Transform owner;
                if (!string.IsNullOrEmpty(info.owner) && byName.TryGetValue(info.owner, out owner))
                    foreach (var r in owner.GetComponents<Renderer>()) r.shadowCastingMode = ShadowCastingMode.Off;
            }
        }

        foreach (var r in root.GetComponentsInChildren<MeshRenderer>(true))
            if (IsDecal(r)) r.shadowCastingMode = ShadowCastingMode.Off;
        if (side.noShadow != null)
            foreach (var nm in side.noShadow)
            {
                Transform t;
                if (byName.TryGetValue(nm, out t))
                    foreach (var r in t.GetComponents<Renderer>()) r.shadowCastingMode = ShadowCastingMode.Off;
            }
        // Roblox gives plugins no union geometry, so unions arrive as their bounding box (UNION_*). A box covers
        // the union's holes: the lobby's facade union (behind the brick) hid every upper window as lit cobblestone.
        // Hidden until the union is exported as a mesh from Studio (see the export report).
        foreach (var r in root.GetComponentsInChildren<MeshRenderer>(true))
            if (r.name.StartsWith("UNION_") && !ShowUnionBoxes)
            {
                r.enabled = false;
                r.shadowCastingMode = ShadowCastingMode.Off;
                Debug.LogWarning("RobloxBridge: " + r.name + " is a union exported as a box; hidden. Export it from Studio as a mesh to show it.");
            }

        Transform sun, sunAim;
        if (byName.TryGetValue("RBXSun", out sun) && byName.TryGetValue("RBXSun_aim", out sunAim))
        {
            var toSun = sunAim.position - sun.position;
            if (toSun.sqrMagnitude > 1e-8f) sun.rotation = Quaternion.LookRotation(-toSun.normalized); // light travels away from the sun
            Object.DestroyImmediate(sunAim.gameObject);
            sun.gameObject.name = "RBXSun";
            var L = side.lighting;
            if (L != null && (L.props != null || L.effects != null))
            {
                var data = sun.gameObject.AddComponent<RobloxBridge.RobloxData>();
                data.entries.Add(Entry("Lighting", "Lighting", "Lighting", L.props));
                if (L.effects != null)
                    foreach (var e in L.effects)
                        data.entries.Add(Entry(e.@class, e.name, e.parent + "." + e.name, e.props));
            }
        }
    }

    static RobloxBridge.RobloxData.Entry Entry(string cls, string name, string path, PropInfo[] props)
    {
        var e = new RobloxBridge.RobloxData.Entry { className = cls, name = name, path = path };
        if (props != null)
            foreach (var p in props) e.props.Add(new RobloxBridge.RobloxData.Prop { name = p.name, value = p.value });
        return e;
    }

    // ------------------------------------------------------------- materials
    static Shader PickShader()
    {
        Shader s = null;
        if (GraphicsSettings.currentRenderPipeline != null) s = Shader.Find("Universal Render Pipeline/Lit");
        return s ? s : Shader.Find("Standard");
    }

    static Texture2D Tex(string fbx, string rel)
    {
        if (string.IsNullOrEmpty(rel)) return null;
        return AssetDatabase.LoadAssetAtPath<Texture2D>(Dir(fbx) + "/" + rel);
    }

    /// Creates/updates <name>_Materials/*.mat from the sidecar and remaps the FBX to them.
    /// overwrite=false keeps materials you already edited. Returns the number of materials.
    public static int BuildMaterials(string fbx, bool overwrite)
    {
        var side = Load(fbx);
        var mi = AssetImporter.GetAtPath(fbx) as ModelImporter;
        if (side == null || side.materials == null || mi == null) return 0;
        string matDir = Dir(fbx) + "/" + Stem(fbx) + "_Materials";
        if (!AssetDatabase.IsValidFolder(matDir)) AssetDatabase.CreateFolder(Dir(fbx), Stem(fbx) + "_Materials");
        bool urp = GraphicsSettings.currentRenderPipeline != null;
        var shader = PickShader();
        var existing = mi.GetExternalObjectMap();
        bool remapChanged = false;
        int n = 0;
        foreach (var info in side.materials)
        {
            string mp = matDir + "/" + Safe(info.name) + ".mat";
            var m = AssetDatabase.LoadAssetAtPath<Material>(mp);
            if (m == null)
            {
                m = new Material(shader) { name = info.name };
                Apply(m, info, fbx, urp);
                AssetDatabase.CreateAsset(m, mp);
            }
            else if (overwrite)
            {
                m.shader = shader;
                Apply(m, info, fbx, urp);
                EditorUtility.SetDirty(m);
            }
            var id = new AssetImporter.SourceAssetIdentifier(typeof(Material), info.name);
            Object cur;
            if (!existing.TryGetValue(id, out cur) || cur != m) { mi.AddRemap(id, m); remapChanged = true; }
            n++;
        }
        AssetDatabase.SaveAssets();
        if (remapChanged) mi.SaveAndReimport();
        return n;
    }

    // ------------------------------------------------------------- scene lighting
    /// Sun ("Roblox Sun" directional light), ambient and fog from the export's Roblox Lighting.
    /// instance = the model in the scene (for the sun direction); may be null.
    static Transform FindChild(GameObject src, string name)
    {
        if (src == null) return null;
        foreach (var t in src.GetComponentsInChildren<Transform>(true)) if (t.name == name) return t;
        return null;
    }

    /// Re-applies the export's light values x tuning to the lights of a model in the scene.
    public static int TuneLights(string fbx, GameObject instance, Tuning tune)
    {
        var side = Load(fbx);
        if (side == null || side.lights == null || instance == null) return 0;
        var byMarker = new Dictionary<string, LightInfo>();
        foreach (var li in side.lights) if (!string.IsNullOrEmpty(li.marker)) byMarker[li.marker] = li;
        int n = 0, outdoor = 0;
        float S = StudScale(fbx);
        var rends = tune.robloxStyle ? instance.GetComponentsInChildren<MeshRenderer>(true) : new MeshRenderer[0];
        foreach (var l in instance.GetComponentsInChildren<Light>(true))
        {
            // Roblox lights aren't blocked by their own fixture (shade, housing, bulb); Unity's would be.
            // Small parts right at the light stop casting shadows (they still receive light).
            if (tune.robloxStyle && l.shadows != LightShadows.None)
            {
                var lp = l.transform.position;
                foreach (var r in rends)
                {
                    var b = r.bounds;
                    if (b.SqrDistance(lp) < (FixtureRadiusStuds * S) * (FixtureRadiusStuds * S) && b.size.magnitude < FixtureSizeStuds * S && r.shadowCastingMode != ShadowCastingMode.Off
                        && !(IsTransparent(r) && !IsNearlyInvisible(r)))   // glass casts in Roblox (alpha >= 0.252), even next to a light
                    {
                        Undo.RecordObject(r, "Tune Roblox lights");
                        r.shadowCastingMode = ShadowCastingMode.Off;
                    }
                }
            }
            var key = l.gameObject.name.Split(' ')[0];
            LightInfo li;
            if (!byMarker.TryGetValue(key, out li)) continue;
            Undo.RecordObject(l, "Tune Roblox lights");
            // Unity spots stop at 179 deg and their shadow map degenerates near that, so wide Roblox
            // SpotLights/SurfaceLights (Angle 180 = a half-sphere) light like a point light instead
            // Roblox's cone is saturate((cos - c) / (1 - c))^2 with c = cos(angle/2), except c = 0 means no
            // cone at all (its shader special-cases it): a 180 deg Spot/SurfaceLight lights like a point
            // light (checked against Studio: hallway walls only match that way).
            bool wide = tune.robloxStyle && li.kind != "PointLight" && li.angle >= 170f;
            l.type = (li.kind == "PointLight" || wide) ? LightType.Point : LightType.Spot;
            if (l.type == LightType.Spot)
            {
                l.spotAngle = Mathf.Clamp(li.angle, 1f, 179f);
                // Roblox's cone is saturate((cos - cos(angle/2)) / (1 - cos(angle/2)))^2 = URP's with inner 0
                l.innerSpotAngle = Exact(tune) ? 0f : l.spotAngle * 0.8f;
            }
            var lc = C(li.color, Color.white);
            l.color = Exact(tune) ? Gamma2(lc).gamma : lc;   // Unity linearises with sRGB; this gives c^2
            l.intensity = LightIntensity(li, tune, l.lightmapBakeType == LightmapBakeType.Baked);
            l.range = Mathf.Max(0.01f, li.range * MapScale(fbx) * tune.range * (li.range > BigLightRange ? tune.bigRange : 1f));
            l.bounceIntensity = tune.robloxStyle ? 0f : 1f;   // Roblox has no bounce light
            if (li.shadows) l.shadows = tune.robloxStyle ? LightShadows.Hard : LightShadows.Soft;
            // outdoor lamps (nothing overhead): in Studio they wash their wall evenly, while Unity's shadows
            // of brackets, awnings and window frames cut that into narrow pools, so they don't cast in Roblox style
            if (tune.robloxStyle && !tune.outdoorLampShadows && l.shadows != LightShadows.None && IsOutdoor(l.transform.position, rends, S))
            {
                l.shadows = LightShadows.None;
                outdoor++;
            }
            // Roblox's light shadows ignore geometry right next to the light (lamp housings, shades);
            // Unity's would block it, so start the shadow ~2 studs out
            l.shadowNearPlane = tune.robloxStyle ? Mathf.Clamp(ShadowNearStuds * StudScale(fbx), 0.1f, 10f) : 0.2f;
            SetShadowTier(l, tune.robloxStyle ? 0 : 1);       // low-res tiles so ~200 shadowed lights fit
            n++;
        }
        // URP shadows ~40 point lights per frame at most: the budget gives them to the lights nearest the camera
        var budget = instance.GetComponent<RobloxShadowBudget>();
        if (tune.robloxStyle && LampsBaked(instance))
        {
            // lamps baked into the lightmaps and switched off: no real-time shadows to share out
            if (budget != null) Undo.DestroyObjectImmediate(budget);
        }
        else if (tune.robloxStyle)
        {
            if (budget == null) budget = Undo.AddComponent<RobloxShadowBudget>(instance);
            budget.shadowed = instance.GetComponentsInChildren<Light>(true).Where(x => x.type != LightType.Directional && x.shadows != LightShadows.None).ToArray();
            EditorUtility.SetDirty(budget);
        }
        else if (budget != null) Undo.DestroyObjectImmediate(budget);
        return n;
    }

    /// Open sky straight up or 45 degrees up in any of 8 directions within OutdoorCheckStuds
    /// (wall lanterns under an eave still see the sky outwards); the light's own fixture parts are ignored
    static bool IsOutdoor(Vector3 p, MeshRenderer[] rends, float S)
    {
        return OutdoorDirection(p, rends, S) != null;
    }

    /// the first open-sky direction from p (see IsOutdoor), or null
    public static Vector3? OutdoorDirection(Vector3 p, MeshRenderer[] rends, float S)
    {
        var dirs = new List<Vector3> { Vector3.up };
        for (int i = 0; i < 8; i++)
        {
            float a = i * Mathf.PI / 4f;
            dirs.Add(new Vector3(Mathf.Cos(a), 1f, Mathf.Sin(a)).normalized);
        }
        foreach (var dir in dirs)
        {
            var ray = new Ray(p + dir * (FixtureRadiusStuds * S), dir);
            bool blocked = false;
            foreach (var r in rends)
            {
                var b = r.bounds;
                if (IsDecal(r) || IsNearlyInvisible(r)) continue;   // glass counts: it shadows in Roblox
                // the lamp's own housing doesn't count (small and right at the light); other small parts do
                if (b.size.magnitude < FixtureSizeStuds * S && b.SqrDistance(p) < (FixtureRadiusStuds * S) * (FixtureRadiusStuds * S)) continue;
                float d;
                if (b.IntersectRay(ray, out d) && d < OutdoorCheckStuds * S) { blocked = true; break; }
            }
            if (!blocked) return dir;
        }
        return null;
    }

    public static float OutdoorCheckStuds = 40f;

    // Roblox lights stay almost evenly bright out to their range; Unity's fall off inverse-square.
    // Roblox style fixes the curve instead of the intensity: realtime in the Roblox Lit shader,
    // baked with linear falloff (InstallFalloff).
    static float LightIntensity(LightInfo li, Tuning tune, bool baked)
    {
        return Mathf.Max(0f, li.brightness) * (Exact(tune) ? ExactBrightnessScale : LightIntensityScale) * tune.light;
    }

    // Roblox Brightness -> Unity intensity in exact mode, fitted over 9 Studio views of the Lobby (black sky,
    // no sun, Ambient as is): 1.6 brings every view's average within ~10%. (The earlier "1 = Ambient 1"
    // calibration compared against an Ambient read 2.6x too low, with the sun accidentally on.)
    public static float ExactBrightnessScale = 1.6f;

    public const float BigLightRange = 6f;
    public static float ShadowNearStuds = 2f;
    public static float FixtureRadiusStuds = 1.5f, FixtureSizeStuds = 4f;   // a light's own fixture: casts no shadow
    public static bool ShowUnionBoxes = false;
    // built-in material -> (normal map strength, texture mip bias). Brick: Studio's is flat and soft; full-strength
    // normals and sharp mips made it twice as grainy (measured 2026-11-04).
    public static readonly Dictionary<string, Vector2> BuiltinLook = new Dictionary<string, Vector2> { { "Brick", new Vector2(0.5f, 0.25f) } };   // draw UNION_* placeholder boxes (unions without exported geometry)

    internal static float StudScale(string fbx)
    {
        var side = Load(fbx);
        return (side != null && side.lighting != null && side.lighting.studScale > 0f ? side.lighting.studScale : 0.28f) * MapScale(fbx);
    }

    /// Map scale: the whole map smaller or bigger than its Roblox size (0.5 = half), for player-sized maps.
    /// Scales the geometry on import and everything measured in studs (light ranges, falloff, shadows, fog).
    /// Kept in the model's .meta (userData "mapScale=0.5"), so a new export from Roblox keeps it.
    public static float MapScale(string fbx)
    {
        var imp = string.IsNullOrEmpty(fbx) ? null : AssetImporter.GetAtPath(fbx);
        if (imp == null || string.IsNullOrEmpty(imp.userData)) return 1f;
        foreach (var kv in imp.userData.Split(';'))
            if (kv.StartsWith("mapScale=") && float.TryParse(kv.Substring(9), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var s) && s > 0f) return s;
        return 1f;
    }

    /// userData "lightmapUV=<texels per metre>": Unity generates the lightmap UVs for that density (0 = Blender's)
    public static float LightmapUVTexels(string fbx)
    {
        var imp = string.IsNullOrEmpty(fbx) ? null : AssetImporter.GetAtPath(fbx);
        if (imp == null || string.IsNullOrEmpty(imp.userData)) return 0f;
        foreach (var kv in imp.userData.Split(';'))
            if (kv.StartsWith("lightmapUV=") && float.TryParse(kv.Substring(11), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var s) && s > 0f) return s;
        return 0f;
    }

    public static void SetMapScale(string fbx, float s)
    {
        var imp = AssetImporter.GetAtPath(fbx);
        if (imp == null) return;
        var rest = (imp.userData ?? "").Split(';').Where(kv => kv.Length > 0 && !kv.StartsWith("mapScale="));
        imp.userData = string.Join(";", rest.Concat(Mathf.Approximately(s, 1f) ? new string[0] : new[] { "mapScale=" + s.ToString(System.Globalization.CultureInfo.InvariantCulture) }));
        imp.SaveAndReimport();
    }   // metres (~21 studs): indoor fixtures are below, outdoor lamps above

    public const string RobloxShaderName = "Roblox Bridge/Roblox Lit";
    const string RobloxShaderPath = "Assets/Roblox/Shaders/RobloxLit.shader";

    /// URP's Complex Lit (forward-only Lit) with the Roblox light falloff (Shaders/RobloxLighting.hlsl).
    /// Generated from the installed URP so it always matches its version. Null if not URP or it fails.
    // Roblox Ambient for the Roblox Lit shader. Exact mode: Lighting.Ambient as is, NOT decoded like part
    // colours. Fitted over 9 Studio views of the Lobby (black sky, no sun): the even light Studio adds is
    // 2.2-2.6x the old gamma-2 / closed-box value (0.104), i.e. the raw 0.2745 of Ambient [70,70,70].
    static Color RobloxAmbient(LightingInfo L, Tuning tune, AmbientCal cal = null)
    {
        var c = C(L.ambient, new Color(0.27f, 0.27f, 0.27f));
        return (Exact(tune) ? c : c.linear) * tune.ambient;
    }

    /// Light measured in the place itself with tools/calibrate_ambient.lua + fit_ambient.py
    /// (<model>.ambient.json next to the FBX): scene-linear light on a white surface.
    [System.Serializable]
    public class AmbientCal
    {
        public float[] indoorUp, indoorSide, openUp, openSide, openUpSun, openSideSun;
        // sky on walls facing Roblox +X/-X/+Z/-Z (sun as in the place); openSideSun is the +Z one
        public float[] openSidePX, openSideNX, openSidePZ, openSideNZ;
        // fit_ambient --sidecar: those walls split into sky (same every way) + direct sun x N.L (linear rgb)
        public float[] skySide, sunDirect;
        // the game's own look (set by hand in the .ambient.json): no sun, and/or a black sky (no sky light)
        public bool noSun, blackSky;
    }

    static AmbientCal LoadCal(string fbx)
    {
        var p = Dir(fbx) + "/" + Stem(fbx) + ".ambient.json";
        if (!File.Exists(p)) return null;
        var c = JsonUtility.FromJson<AmbientCal>(File.ReadAllText(p));
        return c != null && c.indoorUp != null && c.openUp != null ? c : null;
    }

    // materials that are a shared colour map x the part colour (built-in materials, MaterialVariants)
    static HashSet<string> TintMaskMaterials(string fbx)
    {
        var set = new HashSet<string>();
        var side = Load(fbx);
        if (side != null && side.materials != null)
            foreach (var mi in side.materials)
                if (mi.tint != null && mi.tint.Length >= 3 && !string.IsNullOrEmpty(mi.baseMap)) set.Add(mi.name);
        return set;
    }

    static Color V(float[] a) { return a != null && a.Length >= 3 ? new Color(a[0], a[1], a[2]) : Color.black; }

    static Color Pos(Color c) { return new Color(Mathf.Max(0f, c.r), Mathf.Max(0f, c.g), Mathf.Max(0f, c.b)); }

    // measured sky light (outdoor minus indoor): up-facing and side-facing, with or without the sun
    static void CalSky(AmbientCal cal, bool sun, out Color up, out Color side)
    {
        if (cal.blackSky)
        {
            up = Color.black; side = Color.black;   // a black Roblox sky gives no sky light, only Ambient
            return;
        }
        if (cal.sunDirect != null && cal.skySide != null)
        {
            // the sun doesn't brighten Roblox's sky; it's a directional light of its own (sunDirect)
            up = Pos(V(cal.openUp) - V(cal.indoorUp));
            side = Pos(V(cal.skySide) - V(cal.indoorSide));
            return;
        }
        up = Pos(V(sun && cal.openUpSun != null ? cal.openUpSun : cal.openUp) - V(cal.indoorUp));
        side = Pos(V(sun && cal.openSideSun != null ? cal.openSideSun : cal.openSide) - V(cal.indoorSide));
    }

    // measured direct sun: what the sun adds facing it, minus what it adds everywhere (brighter sky)
    static float CalSunIntensity(AmbientCal cal, LightingInfo L)
    {
        if (cal.sunDirect != null) return V(cal.sunDirect).maxColorComponent;
        if (cal.openUpSun == null || cal.openSideSun == null) return -1f;
        float upGain = (V(cal.openUpSun) - V(cal.openUp)).grayscale;
        float sideGain = (V(cal.openSideSun) - V(cal.openSide)).grayscale;
        float sy = L.sunDirection != null && L.sunDirection.Length >= 2 ? L.sunDirection[1] : 1f;
        return Mathf.Max(0f, upGain - sideGain) / Mathf.Max(0.1f, sy);
    }

    const string SkyReflectionPath = "Assets/Roblox/Shaders/RobloxSkyReflection.cubemap";

    // a tiny cubemap of the measured sky: up face = up light, sides = side light, down = half of it
    static Cubemap SkyReflection(Color up, Color side)
    {
        const int n = 8;
        var cm = AssetDatabase.LoadAssetAtPath<Cubemap>(SkyReflectionPath);
        if (cm == null)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(SkyReflectionPath));
            cm = new Cubemap(n, TextureFormat.RGBAHalf, false);
            AssetDatabase.CreateAsset(cm, SkyReflectionPath);
        }
        foreach (CubemapFace f in new[] { CubemapFace.PositiveX, CubemapFace.NegativeX, CubemapFace.PositiveY, CubemapFace.NegativeY, CubemapFace.PositiveZ, CubemapFace.NegativeZ })
        {
            var c = f == CubemapFace.PositiveY ? up : f == CubemapFace.NegativeY ? side * 0.5f : side;
            c.a = 1f;
            var px = new Color[n * n];
            for (int i = 0; i < px.Length; i++) px[i] = c;
            cm.SetPixels(px, f);
        }
        cm.Apply();
        EditorUtility.SetDirty(cm);
        return cm;
    }

    // Unity directions of Roblox +X and -Z (from the FBX's axis markers; set before SkyCube is used)
    static Vector3 s_robloxX = Vector3.left, s_robloxNegZ = Vector3.back;

    // Lamps baked into the lightmaps ("Bake lights too", the Quest setup): then the lightmap holds light,
    // not sky visibility, and the Roblox Lit shader adds it as is (sky cube off). Set from the lights.
    static bool s_lampsBaked;

    static bool LampsBaked(GameObject instance)
    {
        var lb = instance != null ? instance.GetComponent<RobloxLampBake>() : null;
        return lb != null && lb.baked;
    }

    static void ReadRobloxAxes(GameObject src)
    {
        s_robloxX = Vector3.left; s_robloxNegZ = Vector3.back;   // what the axis conversion gives by default
        var o = FindChild(src, "RBXSun");
        var ax = FindChild(src, "RBXAxis_rt");
        var az = FindChild(src, "RBXAxis_ft");
        if (o != null && ax != null) s_robloxX = (ax.position - o.position).normalized;
        if (o != null && az != null) s_robloxNegZ = (az.position - o.position).normalized;
    }

    /// Measured sky cube for the Roblox Lit shader: [0] = up (w 1 = on), [1] = side (w = leak),
    /// [2..5] = per-direction side scale for walls facing Unity +X, -X, +Z, -Z (rgb, 1 = same as side). Off = zeros.
    static Vector4[] SkyCube(LightingInfo L, Tuning tune, AmbientCal cal)
    {
        var one = new Vector4(1f, 1f, 1f, 1f);
        if (cal == null || !Exact(tune) || s_lampsBaked) return new[] { Vector4.zero, Vector4.zero, one, one, one, one };
        Color up, side;
        CalSky(cal, tune.useSun, out up, out side);
        up *= tune.sky; side *= tune.sky;
        var res = new[] { new Vector4(up.r, up.g, up.b, 1f), new Vector4(side.r, side.g, side.b, Mathf.Clamp01(tune.skyLeak)), one, one, one, one };
        if (cal.sunDirect == null && cal.openSidePZ != null && cal.openSidePX != null && cal.openSideNX != null && cal.openSideNZ != null)
        {
            // each measured wall direction relative to the +Z one (which CalSky's side is), per channel
            Color pz = Pos(V(cal.openSidePZ) - V(cal.indoorSide));
            System.Func<float[], Vector4> ratio = f =>
            {
                Color c = Pos(V(f) - V(cal.indoorSide));
                return new Vector4(c.r / Mathf.Max(1e-3f, pz.r), c.g / Mathf.Max(1e-3f, pz.g), c.b / Mathf.Max(1e-3f, pz.b), 1f);
            };
            var faces = new[] { new KeyValuePair<Vector3, Vector4>(s_robloxX, ratio(cal.openSidePX)), new KeyValuePair<Vector3, Vector4>(-s_robloxX, ratio(cal.openSideNX)),
                                new KeyValuePair<Vector3, Vector4>(-s_robloxNegZ, ratio(cal.openSidePZ)), new KeyValuePair<Vector3, Vector4>(s_robloxNegZ, ratio(cal.openSideNZ)) };
            var axes = new[] { Vector3.right, Vector3.left, Vector3.forward, Vector3.back };
            for (int i = 0; i < 4; i++)
            {
                float best = -2f;
                foreach (var f in faces)
                {
                    float d = Vector3.Dot(f.Key, axes[i]);
                    if (d > best) { best = d; res[2 + i] = f.Value; }
                }
            }
        }
        return res;
    }

    /// Roblox style environment. With a calibration the sky is a measured Trilight gradient, baked so
    /// walls and roofs block it (black before baking); otherwise the skybox x EnvironmentDiffuseScale.
    static void SetStyleEnvironment(LightingInfo L, Tuning tune, AmbientCal cal, bool baking)
    {
        SetRobloxAmbient(L, tune, cal);
        bool on = baking || IsBaked();
        var sky = SkyCube(L, tune, cal);
        Shader.SetGlobalVector("_RbxSkyUp", sky[0]);
        Shader.SetGlobalVector("_RbxSkySide", sky[1]);
        Shader.SetGlobalVectorArray("_RbxSkyDir", new[] { sky[2], sky[3], sky[4], sky[5] });
        if (sky[0].w > 0f)
        {
            // the bake holds only sky visibility: a white sky (and no bounces, see BakeSkyLighting);
            // the Roblox Lit shader turns it into light with the measured cube. Before baking: none.
            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = baking ? Color.white : Color.black;
            // reflections (metal, shiny parts): the same measured sky, not Unity's (often missing) skybox
            var cm = SkyReflection(new Color(sky[0].x, sky[0].y, sky[0].z) / Mathf.Max(0.01f, tune.sky), new Color(sky[1].x, sky[1].y, sky[1].z) / Mathf.Max(0.01f, tune.sky));
            if (cm != null)
            {
                RenderSettings.defaultReflectionMode = DefaultReflectionMode.Custom;
                RenderSettings.customReflectionTexture = cm;
                DynamicGI.UpdateEnvironment();
            }
            return;
        }
        RenderSettings.ambientMode = AmbientMode.Skybox;
        RenderSettings.ambientIntensity = on ? Mathf.Max(0f, L.environmentDiffuseScale) * tune.sky : 0f;
    }

    static bool Exact(Tuning tune) { return tune.robloxStyle && tune.exact; }

    // Roblox decodes colours with gamma 2 (c^2), Unity with the sRGB curve
    static Color Gamma2(Color c) { return new Color(c.r * c.r, c.g * c.g, c.b * c.b, c.a); }

    /// Roblox's light falloff constant (20 studs^2, measured) in m^2 for the shader, or -1 for the falloff curve
    public static float FalloffK(Tuning tune, float studScale)
    {
        return Exact(tune) ? 20f * studScale * studScale : -1f;
    }

    static void SetRobloxAmbient(LightingInfo L, Tuning tune, AmbientCal cal = null)
    {
        var a = RobloxAmbient(L, tune, cal);
        Shader.SetGlobalVector("_RbxAmbient", new Vector4(a.r, a.g, a.b, 1f));   // live in the editor
    }

    static Shader RobloxShader(float falloff, Color ambient, float k, Vector4[] sky = null)
    {
        if (GraphicsSettings.currentRenderPipeline == null) return null;
        var src = Path.GetFullPath("Packages/com.unity.render-pipelines.universal/Shaders/ComplexLit.shader");
        if (!File.Exists(src)) return null;
        var s = File.ReadAllText(src);
        const string fwd = "#include \"Packages/com.unity.render-pipelines.universal/Shaders/LitForwardPass.hlsl\"";
        int nameAt = s.IndexOf("Shader \"Universal Render Pipeline/Complex Lit\"");
        if (nameAt < 0 || s.IndexOf(fwd) < 0) return null;
        s = s.Replace("Shader \"Universal Render Pipeline/Complex Lit\"", "Shader \"" + RobloxShaderName + "\"");
        System.Func<float, string> F = x => x.ToString("0.#####", System.Globalization.CultureInfo.InvariantCulture);
        s = s.Replace(fwd, "#define RBX_AMBIENT float3(" + F(ambient.r) + ", " + F(ambient.g) + ", " + F(ambient.b) + ")\n            "
            + "#define RBX_FALLOFF_POWER " + falloff.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture)
            + "\n            #define RBX_FALLOFF_K " + F(k)
            + "\n            #pragma shader_feature_local _RBX_TINTMASK"
            + "\n            #pragma multi_compile_fragment _ RBX_LAMP_GRID"
            + (s_lampsBaked ? "\n            #define RBX_LAMPS_BAKED 1" : "")
            // features the Roblox look never uses (hard shadows, no SSAO/cookies/light layers/decal buffers/
            // realtime GI/shadowmask/clear coat/parallax/detail maps, non-directional lightmaps, no XR paths):
            // fewer variants to compile, ship and keep in memory
            + "\n            #pragma skip_variants META_QUEST_LIGHTUNROLL META_QUEST_ORTHO_PROJ META_QUEST_NO_SPOTLIGHTS_LIGHT_LOOP"
            + "\n            #pragma skip_variants _ADDITIONAL_LIGHTS_VERTEX EVALUATE_SH_VERTEX _LIGHT_LAYERS _MAIN_LIGHT_SHADOWS_SCREEN"
            + "\n            #pragma skip_variants _SHADOWS_SOFT _SHADOWS_SOFT_LOW _SHADOWS_SOFT_MEDIUM _SHADOWS_SOFT_HIGH"
            + "\n            #pragma skip_variants _SCREEN_SPACE_OCCLUSION _SCREEN_SPACE_IRRADIANCE _DBUFFER_MRT1 _DBUFFER_MRT2 _DBUFFER_MRT3 _LIGHT_COOKIES"
            + "\n            #pragma skip_variants DYNAMICLIGHTMAP_ON LIGHTMAP_SHADOW_MIXING SHADOWS_SHADOWMASK USE_LEGACY_LIGHTMAPS"
            + "\n            #pragma skip_variants _CLEARCOAT _CLEARCOATMAP _PARALLAXMAP _DETAIL_MULX2 _DETAIL_SCALED _SPECULAR_SETUP LOD_FADE_CROSSFADE"
            + (sky != null && sky[0].w > 0f ? "\n            #define RBX_SKY_UP float4(" + F(sky[0].x) + ", " + F(sky[0].y) + ", " + F(sky[0].z) + ", 1)"
                + "\n            #define RBX_SKY_SIDE float4(" + F(sky[1].x) + ", " + F(sky[1].y) + ", " + F(sky[1].z) + ", " + F(sky[1].w) + ")"
                + "\n            #define RBX_SKY_DIR_PX half3(" + F(sky[2].x) + ", " + F(sky[2].y) + ", " + F(sky[2].z) + ")"
                + "\n            #define RBX_SKY_DIR_NX half3(" + F(sky[3].x) + ", " + F(sky[3].y) + ", " + F(sky[3].z) + ")"
                + "\n            #define RBX_SKY_DIR_PZ half3(" + F(sky[4].x) + ", " + F(sky[4].y) + ", " + F(sky[4].z) + ")"
                + "\n            #define RBX_SKY_DIR_NZ half3(" + F(sky[5].x) + ", " + F(sky[5].y) + ", " + F(sky[5].z) + ")" : "")
            + "\n            #include \"Packages/com.robloxbridge.importer/Shaders/RobloxLighting.hlsl\"\n            " + fwd);
        // URP's forward pass with the lightmap UV interpolated at the centroid: with MSAA, edge pixels are shaded at
        // the pixel centre, which can lie outside the triangle, and the lightmap UV then lands in a neighbouring
        // island: a dark outline on every part edge of a baked map (live lighting computes from position, no outline)
        var fwdSrc = Path.GetFullPath("Packages/com.unity.render-pipelines.universal/Shaders/LitForwardPass.hlsl");
        // and Roblox's tonemap at the end when _RbxShaderTonemap is set (Quest: no HDR buffer or full-screen pass)
        const string lmDecl = "DECLARE_LIGHTMAP_OR_SH(staticLightmapUV, vertexSH, 8);";
        const string fogLine = "color.rgb = MixFog(color.rgb, inputData.fogCoord);";
        if (File.Exists(fwdSrc))
        {
            var fs = File.ReadAllText(fwdSrc);
            if (fs.Contains(lmDecl) && fs.Contains(fogLine))
            {
                fs = "// Generated by Roblox Bridge from URP LitForwardPass.hlsl (centroid lightmap UV, in-shader tonemap). Do not edit.\n"
                    + System.Text.RegularExpressions.Regex.Replace(fs, "#include \"(?!Packages/)", "#include \"Packages/com.unity.render-pipelines.universal/Shaders/").Replace(lmDecl, "#if defined(LIGHTMAP_ON)\n    centroid float2 staticLightmapUV : TEXCOORD8;\n#else\n    " + lmDecl + "\n#endif")
                    .Replace(fogLine, fogLine + "\n    color.rgb = RbxShaderTonemap(color.rgb);");
                const string fwdCopy = "Assets/Roblox/Shaders/RobloxLitForwardPass.hlsl";
                if (!File.Exists(fwdCopy) || File.ReadAllText(fwdCopy) != fs) { File.WriteAllText(fwdCopy, fs); AssetDatabase.ImportAsset(fwdCopy, ImportAssetOptions.ForceSynchronousImport); }
                s = s.Replace(fwd, "#include \"" + fwdCopy + "\"");
            }
        }
        // (its GBuffer pass only fills the G-buffer; Deferred lights Complex Lit in its forward pass)
        s = "// Generated by Roblox Bridge from URP Complex Lit. Do not edit (rewritten on Apply look).\n" + s;
        Directory.CreateDirectory(Path.GetDirectoryName(RobloxShaderPath));
        if (!File.Exists(RobloxShaderPath) || File.ReadAllText(RobloxShaderPath) != s)
        {
            File.WriteAllText(RobloxShaderPath, s);
            AssetDatabase.ImportAsset(RobloxShaderPath, ImportAssetOptions.ForceSynchronousImport);
        }
        var sh = AssetDatabase.LoadAssetAtPath<Shader>(RobloxShaderPath);
        if (sh == null || ShaderUtil.ShaderHasError(sh))
        {
            Debug.LogWarning("RobloxBridge: Roblox Lit shader failed to compile, using URP Lit (see the shader's Inspector).");
            return null;
        }
        return sh;
    }

    // Roblox parts block light with both sides; Unity's shadow pass skips back faces, so a one-sided
    // roof lets the sun through. Only touches renderers that cast normally (On <-> TwoSided), so
    // ones you set to Off (and light-owner parts) stay off.
    static void SetTwoSidedShadows(GameObject instance, bool style, float S = 0.28f)
    {
        if (instance == null) return;
        var from = style ? ShadowCastingMode.On : ShadowCastingMode.TwoSided;
        var to = style ? ShadowCastingMode.TwoSided : ShadowCastingMode.On;
        foreach (var r in instance.GetComponentsInChildren<MeshRenderer>(true))
        {
            // glass (see-through but alpha >= 0.252): an earlier version switched all of it off; start
            // again from the model (CastShadow from Studio)
            if (style && IsTransparent(r) && !IsNearlyInvisible(r) && r.shadowCastingMode == ShadowCastingMode.Off)
            {
                var src = PrefabUtility.GetCorrespondingObjectFromSource(r) as Renderer;
                if (src != null && src.shadowCastingMode != ShadowCastingMode.Off) r.shadowCastingMode = ShadowCastingMode.On;
            }
            // Roblox Decals/Textures never cast shadows; in Unity the decal plane, a hair in front of its
            // wall, would shadow that wall from every light
            if (IsDecal(r)) { r.shadowCastingMode = ShadowCastingMode.Off; continue; }
            // Roblox's shadow pass drops a pixel when texture alpha x (1 - Transparency) < 0.252 and casts a full
            // shadow otherwise: glass up to Transparency ~0.75 blocks light (lobby light stays inside), only
            // nearly invisible parts don't. (A lantern's own panes are handled by the fixture rule in TuneLights.)
            if (style && IsNearlyInvisible(r)) { r.shadowCastingMode = ShadowCastingMode.Off; continue; }
            if (r.shadowCastingMode == from) r.shadowCastingMode = to;
        }
    }

    internal static bool IsTransparent(Renderer r)
    {
        foreach (var m in r.sharedMaterials) if (m != null && m.renderQueue >= 2500 && !m.name.StartsWith("Decal_")) return true;
        return false;
    }

    public const float RobloxShadowAlphaCutoff = 0.252f;   // Roblox's shadow pass alpha test

    internal static bool IsNearlyInvisible(Renderer r)
    {
        foreach (var m in r.sharedMaterials)
        {
            if (m == null) continue;
            float a = m.HasProperty("_BaseColor") ? m.GetColor("_BaseColor").a : 1f;
            if (m.renderQueue < 2500 || a >= RobloxShadowAlphaCutoff) return false;
        }
        return true;
    }

    static bool IsDecal(Renderer r)
    {
        foreach (var m in r.sharedMaterials) if (m != null && m.name.StartsWith("Decal_")) return true;
        return false;
    }

    // Roblox style: every material of the model on Roblox Lit (same properties as URP Lit), else back to Lit
    static void SetModelShader(GameObject instance, bool style, float falloff, Color ambient, float k, HashSet<string> tintMask = null, Vector4[] sky = null)
    {
        if (instance == null) return;
        var target = style ? RobloxShader(falloff, ambient, k, sky) : null;
        if (target == null) target = PickShader();
        if (target == null) return;
        var done = new HashSet<Material>();
        foreach (var r in instance.GetComponentsInChildren<Renderer>(true))
            foreach (var m in r.sharedMaterials)
            {
                if (m == null || !done.Add(m)) continue;
                // Roblox built-in materials / MaterialVariants: the colour map's alpha says where the part
                // colour applies (tex * lerp(1, colour, a)); the Roblox Lit shader does that with this keyword
                bool tm = tintMask != null && tintMask.Contains(m.name);
                if (tm != m.IsKeywordEnabled("_RBX_TINTMASK"))
                {
                    if (tm) m.EnableKeyword("_RBX_TINTMASK"); else m.DisableKeyword("_RBX_TINTMASK");
                    EditorUtility.SetDirty(m);
                }
                if (m.shader == target) continue;
                var n = m.shader != null ? m.shader.name : "";
                if (n != "Universal Render Pipeline/Lit" && n != RobloxShaderName) continue;   // leave other shaders alone
                int queue = m.renderQueue;
                m.shader = target;
                m.renderQueue = queue;
                EditorUtility.SetDirty(m);
            }
        AssetDatabase.SaveAssets();
    }

    // URP's per-light shadow resolution tier (0 low, 1 medium, 2 high), set through serialization
    static void SetShadowTier(Light l, int tier)
    {
        var t = System.Type.GetType("UnityEngine.Rendering.Universal.UniversalAdditionalLightData, Unity.RenderPipelines.Universal.Runtime");
        if (t == null) return;
        var d = l.GetComponent(t);
        if (d == null) d = l.gameObject.AddComponent(t);
        var so = new SerializedObject(d);
        var p = so.FindProperty("m_AdditionalLightsShadowResolutionTier");
        if (p != null && p.intValue != tier) { p.intValue = tier; so.ApplyModifiedProperties(); }
    }

    // Roblox has no ambient occlusion, and URP's default shadow atlas only fits a few dozen
    // shadowed lights (the Lobby has ~200). Changes the active URP asset and its renderers.
    static void ApplyProjectStyle(bool style)
    {
        foreach (var rp in AllPipelines())
        {
        var so = new SerializedObject(rp);
        var atlas = so.FindProperty("m_AdditionalLightsShadowmapResolution");
        var low = so.FindProperty("m_AdditionalLightsShadowResolutionTierLow");
        if (atlas != null) atlas.intValue = style ? 4096 : 2048;
        if (low != null) low.intValue = style ? 128 : 256;   // 4096/128 = 1024 shadow tiles
        var soft = so.FindProperty("m_SoftShadowsSupported");   // Roblox style shadows are hard (and soft variants are skipped)
        if (soft != null && style) soft.boolValue = false;
        var dist = so.FindProperty("m_ShadowDistance");      // Roblox draws shadows far out; URP default 50 m
        if (dist != null && style && dist.floatValue < 250f) dist.floatValue = 250f;
        so.ApplyModifiedProperties();
        var list = so.FindProperty("m_RendererDataList");
        for (int i = 0; list != null && i < list.arraySize; i++)
        {
            var rd = list.GetArrayElementAtIndex(i).objectReferenceValue;
            if (rd == null) continue;
            var feats = new SerializedObject(rd).FindProperty("m_RendererFeatures");
            for (int j = 0; feats != null && j < feats.arraySize; j++)
            {
                var f = feats.GetArrayElementAtIndex(j).objectReferenceValue;
                if (f == null || f.GetType().Name != "ScreenSpaceAmbientOcclusion") continue;
                var fso = new SerializedObject(f);
                var a = fso.FindProperty("m_Active");
                if (a != null && a.boolValue == style) { a.boolValue = !style; fso.ApplyModifiedProperties(); }
            }
        }
        }
        AssetDatabase.SaveAssets();
    }

    // Bakes Roblox lights (RBXL_*) with linear falloff, closer to Roblox than inverse-square.
    // Wraps URP's own delegate, so every other light bakes exactly as before.
    static void InstallFalloff(bool linear)
    {
        var urpT = System.Type.GetType("UnityEngine.Rendering.Universal.UniversalRenderPipeline, Unity.RenderPipelines.Universal.Runtime");
        var f = urpT != null ? urpT.GetField("lightsDelegate", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static) : null;
        var urp = f != null ? f.GetValue(null) as GI.Lightmapping.RequestLightsDelegate : null;
        if (urp == null) return;
        if (!linear) { GI.Lightmapping.SetDelegate(urp); return; }
        GI.Lightmapping.SetDelegate((requests, output) =>
        {
            urp(requests, output);
            for (int i = 0; i < requests.Length; i++)
            {
                var l = requests[i];
                if (l == null || l.type == LightType.Directional || !l.name.StartsWith("RBXL_")) continue;
                var ld = output[i];
                ld.falloff = GI.FalloffType.Linear;
                output[i] = ld;
            }
        });
    }

    /// Sun, sky, ambient, reflections, fog/haze and post effects from the export's Roblox Lighting.
    /// instance = the model in the scene (for the sun/sky directions); may be null.
    public static bool ApplySceneLighting(string fbx, GameObject instance, Tuning tune = null)
    {
        tune = tune ?? new Tuning();
        var side = Load(fbx);
        if (side == null || side.lighting == null || side.lighting.sunDirection == null || side.lighting.sunDirection.Length < 3) return false;
        var L = side.lighting;
        GameObject src = instance != null ? instance : AssetDatabase.LoadAssetAtPath<GameObject>(fbx);
        Transform sunT = FindChild(src, "RBXSun");
        ReadRobloxAxes(src);
        s_lampsBaked = LampsBaked(instance);
        var cal = LoadCal(fbx);   // measured in the place (tools/calibrate_ambient.lua), if present

        // --- sun (optional)
        var sunGo = GameObject.Find("Roblox Sun");
        if (!tune.useSun || (cal != null && cal.noSun))
        {
            if (sunGo != null) Undo.DestroyObjectImmediate(sunGo);
            sunGo = null;
        }
        else
        {
            if (sunGo == null) { sunGo = new GameObject("Roblox Sun"); Undo.RegisterCreatedObjectUndo(sunGo, "Roblox Sun"); }
            Undo.RecordObject(sunGo.transform, "Roblox lighting");
            var sl = sunGo.GetComponent<Light>();
            if (sl == null) sl = Undo.AddComponent<Light>(sunGo);
            Undo.RecordObject(sl, "Roblox lighting");
            sl.type = LightType.Directional;
            float calSun = cal != null && Exact(tune) ? CalSunIntensity(cal, L) : -1f;
            sl.intensity = (calSun >= 0f ? calSun : Mathf.Max(0f, L.brightness) * SunIntensityScale) * tune.sun;
            sl.color = calSun > 0f && cal.sunDirect != null ? new Color(cal.sunDirect[0] / calSun, cal.sunDirect[1] / calSun, cal.sunDirect[2] / calSun).gamma
                : calSun >= 0f ? Color.white : Color.Lerp(Color.white, C(L.colorShiftTop, Color.white), 0.5f);
            sl.shadows = !L.globalShadows ? LightShadows.None : tune.robloxStyle ? LightShadows.Hard : LightShadows.Soft;
            sl.bounceIntensity = tune.robloxStyle ? 0f : 1f;
            if (sunT != null) sunGo.transform.rotation = sunT.rotation;
            RenderSettings.sun = sl;
        }

        // --- sky (Roblox Sky object -> 6-sided skybox, each image on the matching side)
        if (!tune.quick)
        {
            Material skyMat = cal != null && cal.blackSky ? BlackSky() : BuildSky(fbx, L, src);
            if (skyMat != null) RenderSettings.skybox = skyMat;
        }

        // --- ambient. Roblox lights open areas with the sky and enclosed ones only with
        // Ambient; Unity's realtime ambient can't tell inside from outside, so we use the
        // indoor Ambient here and add sky light by baking (BakeSkyLighting) - occluded, like Roblox.
        // Roblox style: Ambient everywhere comes from the Roblox Lit shader (_RbxAmbient), and the sky
        // (skybox x EnvironmentDiffuseScale) only from the bake, where walls and roofs block it.
        // Before a bake there's no sky light at all, so enclosed rooms stay right.
        if (tune.robloxStyle)
        {
            SetStyleEnvironment(L, tune, cal, false);
        }
        else if (!IsBaked())
        {
            Shader.SetGlobalVector("_RbxAmbient", Vector4.zero);
            RenderSettings.ambientMode = AmbientMode.Flat;
            var indoor = C(L.ambient, new Color(0.27f, 0.27f, 0.27f));
            RenderSettings.ambientLight = indoor * tune.ambient;
        }
        else
        {
            RenderSettings.ambientMode = AmbientMode.Skybox;
            RenderSettings.ambientIntensity = Mathf.Max(0.05f, L.environmentDiffuseScale) * tune.ambient;
        }
        // Roblox style with a calibration reflects the measured sky cubemap (set above); everything else the skybox
        if (!(tune.robloxStyle && SkyCube(L, tune, cal)[0].w > 0f))
            RenderSettings.defaultReflectionMode = DefaultReflectionMode.Skybox;
        RenderSettings.reflectionIntensity = Mathf.Clamp01(L.environmentSpecularScale > 0f ? L.environmentSpecularScale : 0.5f);

        // --- fog: classic Fog if set, otherwise Atmosphere haze
        bool linearFog = L.fogEnd > 0f && L.fogEnd < 10000f;
        bool haze = !linearFog && L.atmosphere != null && L.atmosphere.density > 0.001f && tune.haze > 0f;
        RenderSettings.fog = linearFog || haze;
        if (linearFog)
        {
            RenderSettings.fogMode = FogMode.Linear;
            RenderSettings.fogStartDistance = L.fogStart * StudScale(fbx);
            RenderSettings.fogEndDistance = L.fogEnd * StudScale(fbx);
            RenderSettings.fogColor = C(L.fogColor, Color.gray);
        }
        else if (haze)
        {
            RenderSettings.fogMode = FogMode.ExponentialSquared;
            RenderSettings.fogDensity = L.atmosphere.density * 0.012f * tune.haze;
            RenderSettings.fogColor = C(L.atmosphere.color, new Color(0.78f, 0.78f, 0.78f));
        }

        // --- post effects (URP Volume): exposure, colour correction, bloom
        Shader.SetGlobalFloat("_RbxFalloffPower", tune.falloff);   // live in the editor; builds use the compiled value
        float k = FalloffK(tune, StudScale(fbx));
        Shader.SetGlobalFloat("_RbxFalloffK", k);
        if (!tune.quick) { ApplyProjectStyle(tune.robloxStyle); SetModelShader(instance, tune.robloxStyle, tune.falloff, RobloxAmbient(L, tune, cal), k, TintMaskMaterials(fbx), SkyCube(L, tune, cal)); SetTwoSidedShadows(instance, tune.robloxStyle, StudScale(fbx)); ApplyColorMatch(fbx, Exact(tune)); }
        if (tune.post) TryPostVolume(fbx, L, tune);   // cheap: also live while dragging
        else
        {
            SetRobloxTonemap(false, 1f, tune.quick);
            var pf = GameObject.Find("Roblox PostFX"); if (pf != null) Undo.DestroyObjectImmediate(pf);
        }

        DynamicGI.UpdateEnvironment();
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(UnityEngine.SceneManagement.SceneManager.GetActiveScene());
        return true;
    }

    const string ProbeRootName = "Roblox Reflection Probes";
    public static float ProbeSpacingStuds = 40f;
    public static float LampTexelsPerMetre = 60f, MinTexelsAcross = 24f;

    /// Roblox reflects a captured indoor environment, blended to the sky by skylight (Future shader:
    /// mix(indoorEnv, skyEnv, skylight)). Unity's equivalent: a grid of baked reflection probes over
    /// the model, captured against the measured sky colour. They bake with the lightmaps.
    static void PlaceReflectionProbes(GameObject instance, Vector4[] sky, float stud)
    {
        var old = instance.transform.Find(ProbeRootName);
        if (old != null) Object.DestroyImmediate(old.gameObject);
        if (sky[0].w <= 0f) return;
        var rends = instance.GetComponentsInChildren<MeshRenderer>(true);
        if (rends.Length == 0) return;
        var b = rends[0].bounds;
        foreach (var r in rends) b.Encapsulate(r.bounds);
        float step = Mathf.Max(4f, ProbeSpacingStuds * stud);
        // at most ~200 probes: widen the spacing for big maps
        while (Mathf.Ceil(b.size.x / step) * Mathf.Ceil(b.size.y / step) * Mathf.Ceil(b.size.z / step) > 200f) step *= 1.25f;
        int nx = Mathf.Max(1, Mathf.CeilToInt(b.size.x / step)), ny = Mathf.Max(1, Mathf.CeilToInt(b.size.y / step)), nz = Mathf.Max(1, Mathf.CeilToInt(b.size.z / step));
        var root = new GameObject(ProbeRootName);
        root.transform.SetParent(instance.transform, false);
        var bg = new Color(sky[1].x, sky[1].y, sky[1].z, 1f);
        for (int x = 0; x < nx; x++)
        for (int y = 0; y < ny; y++)
        for (int z = 0; z < nz; z++)
        {
            var go = new GameObject("Probe " + x + "_" + y + "_" + z);
            go.transform.SetParent(root.transform, false);
            go.transform.position = b.min + new Vector3((x + 0.5f) * b.size.x / nx, (y + 0.5f) * b.size.y / ny, (z + 0.5f) * b.size.z / nz);
            var p = go.AddComponent<ReflectionProbe>();
            p.mode = ReflectionProbeMode.Baked;
            p.size = new Vector3(b.size.x / nx, b.size.y / ny, b.size.z / nz) + Vector3.one * step * 0.25f;
            p.blendDistance = step * 0.125f;
            p.boxProjection = false;
            p.resolution = 64;
            p.hdr = true;
            p.clearFlags = ReflectionProbeClearFlags.SolidColor;
            p.backgroundColor = bg;
            p.nearClipPlane = 0.05f;
            p.farClipPlane = Mathf.Max(100f, b.size.magnitude);
        }
        Debug.Log("Roblox Bridge: " + (nx * ny * nz) + " reflection probes (" + step.ToString("F1") + " m apart)");
    }

    static bool IsBaked()
    {
        return Lightmapping.lightingDataAsset != null && LightmapSettings.lightmaps != null && LightmapSettings.lightmaps.Length > 0;
    }

    /// Bakes the Roblox sky (skybox x EnvironmentDiffuseScale) + Neon glow into lightmaps, so open
    /// areas get sky light and enclosed rooms (elevators...) stay dark, like Roblox. Lights stay realtime.
    /// Returns an error message, or null when the bake started.
    public static string BakeSkyLighting(string fbx, GameObject instance, Tuning tune)
    {
        var side = Load(fbx);
        if (side == null || instance == null) return "Import the model into the scene first.";
        var scene = instance.scene;
        if (string.IsNullOrEmpty(scene.path)) return "Save the scene first (File > Save), then bake.";
        tune = tune ?? new Tuning();
        // Unity's baker can't run the Roblox Lit shader, so it never bakes the lamps: they stay real-time here.
        // "Bake lights too" bakes them afterwards with the shader's own lamp maths (RobloxLampBaker) and then
        // switches them off. A bake without it switches them back on.
        var lampBake = instance.GetComponent<RobloxLampBake>();
        if (lampBake != null && lampBake.baked)
        {
            Undo.RecordObject(lampBake, "Bake Roblox lights");
            foreach (var l in lampBake.lamps) if (l != null) { Undo.RecordObject(l, "Bake Roblox lights"); l.enabled = true; }
            lampBake.baked = false;
        }
        foreach (var l in instance.GetComponentsInChildren<Light>(true))
        {
            Undo.RecordObject(l, "Bake Roblox lights");
            l.lightmapBakeType = LightmapBakeType.Realtime;
        }
        ApplySceneLighting(fbx, instance, tune);  // skybox, sun, fog

        foreach (var r in instance.GetComponentsInChildren<MeshRenderer>(true))
        {
            var go = r.gameObject;
            var flags = GameObjectUtility.GetStaticEditorFlags(go) | StaticEditorFlags.ContributeGI | StaticEditorFlags.ReflectionProbeStatic;
            GameObjectUtility.SetStaticEditorFlags(go, flags);
            r.receiveGI = ReceiveGI.Lightmaps;
        }
        var sun = GameObject.Find("Roblox Sun");
        if (sun != null && sun.GetComponent<Light>() != null) sun.GetComponent<Light>().lightmapBakeType = LightmapBakeType.Realtime;
        TuneLights(fbx, instance, tune);   // baked lights don't get the realtime falloff boost
        InstallFalloff(tune.robloxStyle);

        var L = side.lighting;
        if (tune.robloxStyle)
        {
            // the bake carries only the sky (occluded); Ambient is added by the Roblox Lit shader
            if (L != null) SetStyleEnvironment(L, tune, LoadCal(fbx), true);
            if (L != null) PlaceReflectionProbes(instance, SkyCube(L, tune, LoadCal(fbx)), StudScale(fbx));
        }
        else
        {
            RenderSettings.ambientMode = AmbientMode.Skybox;
            RenderSettings.ambientIntensity = Mathf.Max(0.05f, L != null ? L.environmentDiffuseScale : 1f) * tune.ambient;
        }

        string lsPath = Dir(fbx) + "/" + Stem(fbx) + "_Lighting.lighting";
        var ls = AssetDatabase.LoadAssetAtPath<LightingSettings>(lsPath);
        if (ls == null) { ls = new LightingSettings(); AssetDatabase.CreateAsset(ls, lsPath); }
        ls.bakedGI = true;
        ls.realtimeGI = false;
        // CPU only: Unity's GPU lightmappers (OpenCL, and 6.6's compute one) froze the whole PC on this Linux/Mesa setup
#pragma warning disable CS0618
        ls.lightmapper = LightingSettings.Lightmapper.ProgressiveCPU;
#pragma warning restore CS0618
        // texels per metre: baked lamps have to match per-pixel real-time lamps, sky visibility is smooth
        if (tune.lampTexelsPerMetre > 0f) LampTexelsPerMetre = tune.lampTexelsPerMetre;
        float texelsPerMetre = tune.bakeLights ? LampTexelsPerMetre : 4f;
        ls.lightmapResolution = texelsPerMetre;
        ls.lightmapPadding = 2;
        ls.lightmapMaxSize = 2048;
        // small objects (trim, plants, fixtures) still get enough lightmap to show the lamps' shading on them
        foreach (var r in instance.GetComponentsInChildren<MeshRenderer>(true))
        {
            float across = Mathf.Max(r.bounds.size.x, Mathf.Max(r.bounds.size.y, r.bounds.size.z)) * texelsPerMetre;
            float scale = tune.bakeLights && across > 0f ? Mathf.Clamp(MinTexelsAcross / across, 1f, 8f) : 1f;
            var so = new SerializedObject(r);
            var p = so.FindProperty("m_ScaleInLightmap");
            if (p != null && !Mathf.Approximately(p.floatValue, scale)) { p.floatValue = scale; so.ApplyModifiedPropertiesWithoutUndo(); }
        }
        ls.mixedBakeMode = MixedLightingMode.IndirectOnly;
        ls.ao = !tune.robloxStyle;   // Roblox has no ambient occlusion
        // sky-only bake: 1 bounce (0 drops the sky too). Baked lamps: direct light only, Roblox has no bounce light
        if (tune.robloxStyle) ls.maxBounces = 1;
        if (tune.robloxStyle && tune.bakeLights)
        {
            // the lamp bake (RobloxLampBaker, on the GPU through normal rendering) writes every lightmap texel
            // afterwards, so Unity's CPU bake only has to lay the lightmaps out: as few samples as it allows
            ls.directSampleCount = 1;
            ls.indirectSampleCount = 1;
            ls.environmentSampleCount = 1;
            ls.filteringMode = LightingSettings.FilterMode.None;
        }
        else if (tune.robloxStyle)
        {
            ls.directSampleCount = 32;
            ls.indirectSampleCount = 512;
            ls.environmentSampleCount = 256;
            ls.filteringMode = LightingSettings.FilterMode.Auto;
        }
        // the bake only holds sky visibility (a scalar): no directional lightmaps (half the textures, cheaper shader)
        // baked lamps: directional maps, so the materials' normal maps shade the baked light like real-time lamps do
        if (tune.robloxStyle) ls.directionalityMode = tune.bakeLights && tune.lampDirectional ? LightmapsMode.CombinedDirectional : LightmapsMode.NonDirectional;
        EditorUtility.SetDirty(ls);
        AssetDatabase.SaveAssets();
        Lightmapping.lightingSettings = ls;
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(scene);
        if (tune.robloxStyle && tune.bakeLights)
        {
            System.Action done = null;
            done = () => { Lightmapping.bakeCompleted -= done; BakeLamps(fbx, instance, tune); };
            Lightmapping.bakeCompleted += done;
        }
        return Lightmapping.BakeAsync() ? null : "Unity refused to start the bake (see Console).";
    }

    /// Quest: bake the live lamp grid (RobloxLampGrid) into lightmaps. Unity's CPU baker only lays the lightmaps out
    /// (lamps off, black sky), then RobloxLampBaker.BakeGrid fills every texel with the grid's own lighting function,
    /// so the map looks like the grid. Lightmapped objects then skip the grid in the shader; moving objects (the
    /// players) keep using it. Undo: rebuild/re-enable the grid and clear the bake (RobloxLampBake.baked = false).
    public static string BakeGridLighting(string fbx, GameObject instance, Tuning tune, float texelsPerMetre)
    {
        if (instance == null) return "no model";
        var grid = instance.GetComponent<RobloxLampGrid>();
        if (grid == null || !grid.isActiveAndEnabled || grid.cells == null) return "build the lamp grid first (lampgrid build)";
        var scene = instance.scene;
        if (string.IsNullOrEmpty(scene.path)) return "save the scene first";
        tune = tune ?? new Tuning();
        foreach (var l in Object.FindObjectsByType<Light>(FindObjectsInactive.Include)) l.lightmapBakeType = LightmapBakeType.Realtime;
        foreach (var r in instance.GetComponentsInChildren<MeshRenderer>(true))
        {
            var go = r.gameObject;
            GameObjectUtility.SetStaticEditorFlags(go, GameObjectUtility.GetStaticEditorFlags(go) | StaticEditorFlags.ContributeGI | StaticEditorFlags.ReflectionProbeStatic);
            r.receiveGI = ReceiveGI.Lightmaps;
            // small objects (trim, plants, fixtures) still get enough texels to show the lamps' shading
            float across = Mathf.Max(r.bounds.size.x, Mathf.Max(r.bounds.size.y, r.bounds.size.z)) * texelsPerMetre;
            float scale = across > 0f ? Mathf.Clamp(MinTexelsAcross / across, 1f, 8f) : 1f;
            var so = new SerializedObject(r);
            var sp = so.FindProperty("m_ScaleInLightmap");
            if (sp != null && !Mathf.Approximately(sp.floatValue, scale)) { sp.floatValue = scale; so.ApplyModifiedPropertiesWithoutUndo(); }
        }
        string lsPath = Dir(fbx) + "/" + Stem(fbx) + "_Lighting.lighting";
        var ls = AssetDatabase.LoadAssetAtPath<LightingSettings>(lsPath);
        if (ls == null) { ls = new LightingSettings(); AssetDatabase.CreateAsset(ls, lsPath); }
        ls.bakedGI = true;
        ls.realtimeGI = false;
#pragma warning disable CS0618
        ls.lightmapper = LightingSettings.Lightmapper.ProgressiveCPU;   // GPU lightmappers froze this PC
#pragma warning restore CS0618
        ls.lightmapResolution = texelsPerMetre;
        ls.lightmapPadding = 4;   // more room between islands: dilation never reaches a neighbour
        ls.lightmapMaxSize = 2048;
        ls.mixedBakeMode = MixedLightingMode.IndirectOnly;
        ls.ao = false;
        ls.maxBounces = 1;
        ls.directSampleCount = 1;   // only the layout is used, every texel is rewritten afterwards
        ls.indirectSampleCount = 1;
        ls.environmentSampleCount = 1;
        ls.filteringMode = LightingSettings.FilterMode.None;
        ls.directionalityMode = LightmapsMode.CombinedDirectional;   // holds the normal-map vector (RbxSampleLightmap)
        EditorUtility.SetDirty(ls);
        AssetDatabase.SaveAssets();
        Lightmapping.lightingSettings = ls;
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(scene);
        // Unity's bake re-renders the reflection probes with the lamps off (black): keep the lamp-lit ones
        var probeFiles = new Dictionary<string, byte[]>();
        foreach (var rp in Object.FindObjectsByType<ReflectionProbe>(FindObjectsInactive.Include))
        {
            var pp = rp.bakedTexture != null ? AssetDatabase.GetAssetPath(rp.bakedTexture) : null;
            if (!string.IsNullOrEmpty(pp) && File.Exists(pp)) probeFiles[pp] = File.ReadAllBytes(pp);
        }
        System.Action done = null;
        done = () =>
        {
            Lightmapping.bakeCompleted -= done;
            foreach (var kv in probeFiles) { File.WriteAllBytes(kv.Key, kv.Value); AssetDatabase.ImportAsset(kv.Key, ImportAssetOptions.ForceUpdate); }
            Shader.SetGlobalFloat("_RbxFalloffK", FalloffK(tune, StudScale(fbx)));
            grid.Apply(true);
            var err = RobloxLampBaker.BakeGrid(instance);
            if (err != null) { Debug.LogError("Roblox Bridge: grid bake failed: " + err); return; }
            var lb = instance.GetComponent<RobloxLampBake>() ?? Undo.AddComponent<RobloxLampBake>(instance);
            lb.lamps = grid.lamps;
            lb.baked = true;
            EditorUtility.SetDirty(lb);
            ApplySceneLighting(fbx, instance, tune);   // shader regenerated with RBX_LAMPS_BAKED
            grid.Apply(true);
            UnityEditor.SceneManagement.EditorSceneManager.SaveScene(instance.scene);
            Debug.Log("Roblox Bridge: grid bake done, " + LightmapSettings.lightmaps.Length + " lightmaps");
        };
        Lightmapping.bakeCompleted += done;
        return Lightmapping.BakeAsync() ? null : "Unity refused to start the bake (see Console)";
    }

    /// After Unity's bake (which gave the lightmap layout): bake the lamps with the Roblox Lit shader's lamp maths,
    /// switch them off, set the look up for baked lamps and re-capture the reflections.
    static void BakeLamps(string fbx, GameObject instance, Tuning tune)
    {
        if (instance == null) return;
        // the shadows each lamp has in the Roblox look (the shadow budget hands them out per camera at run time)
        var budget = instance.GetComponent<RobloxShadowBudget>();
        var shadowed = new HashSet<Light>(budget != null ? budget.shadowed : new Light[0]);
        var lamps = new List<Light>();
        foreach (var l in instance.GetComponentsInChildren<Light>(true))
        {
            if (!l.enabled || !l.gameObject.activeInHierarchy || l.type == LightType.Directional || !l.name.StartsWith("RBXL_")) continue;
            if (budget != null) l.shadows = shadowed.Contains(l) ? LightShadows.Hard : LightShadows.None;
            if (!tune.bakeLampShadows) l.shadows = LightShadows.None;
            lamps.Add(l);
        }
        var err = RobloxLampBaker.Bake(instance, lamps, FalloffK(tune, StudScale(fbx)));
        if (err != null) { Debug.LogError("Roblox Bridge: lamp bake failed: " + err); return; }

        var lb = instance.GetComponent<RobloxLampBake>() ?? Undo.AddComponent<RobloxLampBake>(instance);
        Undo.RecordObject(lb, "Bake Roblox lights");
        lb.lamps = lamps.ToArray();
        lb.baked = true;
        foreach (var l in lamps) { Undo.RecordObject(l, "Bake Roblox lights"); l.enabled = false; }
        EditorUtility.SetDirty(lb);
        TuneLights(fbx, instance, tune);
        ApplySceneLighting(fbx, instance, tune);   // lightmap = light now (no sky cube), shader regenerated

        foreach (var p in instance.GetComponentsInChildren<ReflectionProbe>(true))
        {
            var path = p.bakedTexture != null ? AssetDatabase.GetAssetPath(p.bakedTexture) : null;
            if (!string.IsNullOrEmpty(path)) Lightmapping.BakeReflectionProbe(p, path);
        }
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(instance.scene);
        UnityEditor.SceneManagement.EditorSceneManager.SaveScene(instance.scene);
        Debug.Log("Roblox Bridge: lamps baked with the Roblox Lit shader's maths and switched off; scene saved");
    }

    static Material BlackSky()
    {
        const string path = "Assets/Roblox/Shaders/RobloxBlackSky.mat";
        var m = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (m != null) return m;
        var sh = Shader.Find("Skybox/Procedural");
        if (sh == null) return null;
        m = new Material(sh);
        m.SetFloat("_Exposure", 0f);
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        AssetDatabase.CreateAsset(m, path);
        return m;
    }

    static Material BuildSky(string fbx, LightingInfo L, GameObject src)
    {
        var m = L.skyMaps;
        if (m == null || string.IsNullOrEmpty(m.ft)) return null;
        var shader = Shader.Find("Skybox/6 Sided");
        if (shader == null) return null;
        string path = Dir(fbx) + "/" + Stem(fbx) + "_Sky.mat";
        var mat = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (mat == null) { mat = new Material(shader); AssetDatabase.CreateAsset(mat, path); }
        mat.shader = shader;

        // Where do Roblox +X (Rt) and -Z (Ft) point in Unity? (markers from the FBX)
        Vector3 rt = Vector3.left, ft = Vector3.back;  // what the axis conversion gives by default
        var o = FindChild(src, "RBXSun");
        var ax = FindChild(src, "RBXAxis_rt");
        var az = FindChild(src, "RBXAxis_ft");
        if (o != null && ax != null) rt = (ax.position - o.position).normalized;
        if (o != null && az != null) ft = (az.position - o.position).normalized;
        string[] xs = { "_LeftTex", "_RightTex" }; // Unity: Left = +X, Right = -X
        string[] zs = { "_FrontTex", "_BackTex" }; // Unity: Front = +Z, Back = -Z
        bool rtOnX = Mathf.Abs(rt.x) > Mathf.Abs(rt.z);
        string rtSlot = rtOnX ? (rt.x > 0 ? xs[0] : xs[1]) : (rt.z > 0 ? zs[0] : zs[1]);
        string lfSlot = rtOnX ? (rt.x > 0 ? xs[1] : xs[0]) : (rt.z > 0 ? zs[1] : zs[0]);
        bool ftOnZ = Mathf.Abs(ft.z) >= Mathf.Abs(ft.x);
        string ftSlot = ftOnZ ? (ft.z > 0 ? zs[0] : zs[1]) : (ft.x > 0 ? xs[0] : xs[1]);
        string bkSlot = ftOnZ ? (ft.z > 0 ? zs[1] : zs[0]) : (ft.x > 0 ? xs[1] : xs[0]);
        System.Action<string, string> set = (prop, rel) => { if (!string.IsNullOrEmpty(rel)) mat.SetTexture(prop, Tex(fbx, rel)); };
        set(rtSlot, m.rt); set(lfSlot, m.lf); set(ftSlot, m.ft); set(bkSlot, m.bk);
        set("_UpTex", m.up); set("_DownTex", m.dn);
        mat.SetFloat("_Exposure", 1f);
        EditorUtility.SetDirty(mat);
        AssetDatabase.SaveAssets();
        return mat;
    }

    static void SetMember(object o, string name, object v)
    {
        var t = o.GetType();
        var f = t.GetField(name);
        if (f != null) { f.SetValue(o, Conv(v, f.FieldType)); return; }
        var p = t.GetProperty(name);
        if (p != null && p.CanWrite) p.SetValue(o, Conv(v, p.PropertyType));
    }

    static object Conv(object v, System.Type t) { return (v is string && t.IsEnum) ? System.Enum.Parse(t, (string)v) : v; }

    // URP/HDRP post processing through reflection (no hard dependency, Built-in just skips it)
    static readonly string[] TonemapModes = { "None", "Neutral", "ACES", "None" };   // Roblox = our pass + URP None
    public const int RobloxTonemapIndex = 3;
    const string TonemapMatPath = "Assets/Roblox/Shaders/RobloxTonemap.mat";
    const string TonemapFeatureName = "Roblox Tonemap";

    /// Roblox's tonemapper as a Full Screen Pass renderer feature (before post processing) on every
    /// renderer of the active URP asset. quick = only update the exposure on the material.
    static void SetRobloxTonemap(bool on, float exposure, bool quick)
    {
        var mat = AssetDatabase.LoadAssetAtPath<Material>(TonemapMatPath);
        if (mat == null && on)
        {
            var sh = Shader.Find("Roblox Bridge/Roblox Tonemap");
            if (sh == null) { Debug.LogWarning("RobloxBridge: Roblox Tonemap shader not found."); return; }
            Directory.CreateDirectory(Path.GetDirectoryName(TonemapMatPath));
            mat = new Material(sh);
            AssetDatabase.CreateAsset(mat, TonemapMatPath);
        }
        if (mat != null && !Mathf.Approximately(mat.GetFloat("_RbxExposure"), exposure))
        {
            mat.SetFloat("_RbxExposure", exposure);
            EditorUtility.SetDirty(mat);
        }
        if (quick) return;
        var featT = System.Type.GetType("UnityEngine.Rendering.Universal.FullScreenPassRendererFeature, Unity.RenderPipelines.Universal.Runtime");
        if (featT == null) return;
        foreach (var rp in AllPipelines())
        {
        var list = new SerializedObject(rp).FindProperty("m_RendererDataList");
        for (int i = 0; list != null && i < list.arraySize; i++)
        {
            var rd = list.GetArrayElementAtIndex(i).objectReferenceValue as ScriptableObject;
            if (rd == null) continue;
            var rso = new SerializedObject(rd);
            var feats = rso.FindProperty("m_RendererFeatures");
            var map = rso.FindProperty("m_RendererFeatureMap");
            if (feats == null || map == null) continue;
            ScriptableObject feat = null;
            for (int j = 0; j < feats.arraySize; j++)
            {
                var f = feats.GetArrayElementAtIndex(j).objectReferenceValue as ScriptableObject;
                if (f != null && f.name == TonemapFeatureName) feat = f;
            }
            if (feat == null)
            {
                if (!on) continue;
                feat = ScriptableObject.CreateInstance(featT);
                feat.name = TonemapFeatureName;
                AssetDatabase.AddObjectToAsset(feat, rd);
                string guid; long id;
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(feat, out guid, out id);
                feats.arraySize++;
                feats.GetArrayElementAtIndex(feats.arraySize - 1).objectReferenceValue = feat;
                map.arraySize++;
                map.GetArrayElementAtIndex(map.arraySize - 1).longValue = id;
                rso.ApplyModifiedProperties();
            }
            featT.GetField("injectionPoint").SetValue(feat, System.Enum.Parse(featT.GetField("injectionPoint").FieldType, "BeforeRenderingPostProcessing"));
            featT.GetField("fetchColorBuffer").SetValue(feat, true);
            featT.GetField("passMaterial").SetValue(feat, mat);
            var fso = new SerializedObject(feat);
            var act = fso.FindProperty("m_Active");
            if (act != null) { act.boolValue = on; fso.ApplyModifiedProperties(); }
            EditorUtility.SetDirty(feat);
            EditorUtility.SetDirty(rd);
            // ScriptableRendererData.SetDirty (hides ScriptableObject's): the renderer rebuilds its features
            const System.Reflection.BindingFlags pub = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.DeclaredOnly;
            for (var t = rd.GetType(); t != null && t != typeof(ScriptableObject); t = t.BaseType)
            {
                var sd = t.GetMethod("SetDirty", pub, null, System.Type.EmptyTypes, null);
                if (sd != null) { sd.Invoke(rd, null); break; }
            }
        }
        }
        AssetDatabase.SaveAssets();
    }

    /// Every URP asset the project uses: the default one and each quality level's (PC, Mobile/Quest...),
    /// so the Roblox look is set up for every platform, not only the one that's active right now.
    static List<RenderPipelineAsset> AllPipelines()
    {
        var res = new List<RenderPipelineAsset>();
        if (GraphicsSettings.defaultRenderPipeline != null) res.Add(GraphicsSettings.defaultRenderPipeline);
        for (int i = 0; i < QualitySettings.names.Length; i++)
        {
            var q = QualitySettings.GetRenderPipelineAssetAt(i);
            if (q != null && !res.Contains(q)) res.Add(q);
        }
        return res;
    }

    static void TryPostVolume(string fbx, LightingInfo L, Tuning tune)
    {
        bool style = tune.robloxStyle;
        try
        {
            var profT = System.Type.GetType("UnityEngine.Rendering.VolumeProfile, Unity.RenderPipelines.Core.Runtime");
            var volT = System.Type.GetType("UnityEngine.Rendering.Volume, Unity.RenderPipelines.Core.Runtime");
            var bloomT = System.Type.GetType("UnityEngine.Rendering.Universal.Bloom, Unity.RenderPipelines.Universal.Runtime");
            var caT = System.Type.GetType("UnityEngine.Rendering.Universal.ColorAdjustments, Unity.RenderPipelines.Universal.Runtime");
            if (profT == null || volT == null || bloomT == null || caT == null) return;

            string path = Dir(fbx) + "/" + Stem(fbx) + "_PostFX.asset";
            var prof = AssetDatabase.LoadAssetAtPath(path, profT) as ScriptableObject;
            if (prof == null) { prof = ScriptableObject.CreateInstance(profT); AssetDatabase.CreateAsset(prof, path); }
            System.Func<System.Type, object> comp = t =>
            {
                var has = profT.GetMethod("Has", new[] { typeof(System.Type) });
                var addM = profT.GetMethod("Add", new[] { typeof(System.Type), typeof(bool) });
                object c = null;
                if (has != null && !(bool)has.Invoke(prof, new object[] { t }))
                {
                    c = addM.Invoke(prof, new object[] { t, false });
                    AssetDatabase.AddObjectToAsset((Object)c, prof);
                }
                else
                {
                    var compsF = profT.GetField("components");
                    foreach (var x in (System.Collections.IList)compsF.GetValue(prof)) if (x.GetType() == t) c = x;
                }
                return c;
            };
            System.Action<object, string, object> param = (c, field, v) =>
            {
                var f = c.GetType().GetField(field);
                if (f == null) return;
                var p = f.GetValue(c);
                SetMember(p, "value", v);
                SetMember(p, "overrideState", true);
            };

            bool rbxCurve = style && tune.tonemap == RobloxTonemapIndex;
            float postExp = rbxCurve ? 0f : L.exposure;   // Roblox curve: exposure is applied before it
            var ca = comp(caT);
            param(ca, "postExposure", postExp);
            param(ca, "saturation", Mathf.Clamp(tune.saturation, -100f, 100f));
            if (L.colorCorrection != null)
            {
                param(ca, "contrast", Mathf.Clamp(L.colorCorrection.contrast * 100f, -100f, 100f));
                param(ca, "saturation", Mathf.Clamp(L.colorCorrection.saturation * 100f + tune.saturation, -100f, 100f));
                param(ca, "colorFilter", C(L.colorCorrection.tint, Color.white));
                param(ca, "postExposure", postExp + L.colorCorrection.brightness * 2f);
            }
            if (L.bloom != null)
            {
                var bl = comp(bloomT);
                param(bl, "intensity", Mathf.Max(0f, L.bloom.intensity));
                param(bl, "threshold", Mathf.Max(0f, L.bloom.threshold * 0.5f));
                param(bl, "scatter", Mathf.Clamp01(L.bloom.size / 56f));
            }
            // Roblox has none of Unity's camera effects, and a neutral tone curve keeps its bright,
            // flat colours (ACES looks filmic). Off = hand these back to the project's own volumes.
            var styleParams = new[] {
                new object[] { "Vignette", "intensity", 0f }, new object[] { "FilmGrain", "intensity", 0f },
                new object[] { "ChromaticAberration", "intensity", 0f }, new object[] { "LensDistortion", "intensity", 0f },
                new object[] { "MotionBlur", "intensity", 0f }, new object[] { "PaniniProjection", "distance", 0f },
                new object[] { "DepthOfField", "mode", "Off" }, new object[] { "Tonemapping", "mode", TonemapModes[Mathf.Clamp(tune.tonemap, 0, TonemapModes.Length - 1)] } };
            foreach (var sp in styleParams)
            {
                var t = System.Type.GetType("UnityEngine.Rendering.Universal." + sp[0] + ", Unity.RenderPipelines.Universal.Runtime");
                if (t == null) continue;
                var c = comp(t);
                if (c == null) continue;
                if (style) param(c, (string)sp[1], sp[2]);
                else { var f = c.GetType().GetField((string)sp[1]); if (f != null) SetMember(f.GetValue(c), "overrideState", false); }
            }
            EditorUtility.SetDirty(prof);
            SetRobloxTonemap(rbxCurve, Mathf.Pow(2f, L.exposure), tune.quick);
            if (!tune.quick) AssetDatabase.SaveAssets();

            var go = GameObject.Find("Roblox PostFX");
            if (go == null) { go = new GameObject("Roblox PostFX"); Undo.RegisterCreatedObjectUndo(go, "Roblox PostFX"); }
            Component vol = go.GetComponent(volT);
            if (vol == null) vol = go.AddComponent(volT);
            SetMember(vol, "isGlobal", true);
            SetMember(vol, "priority", 10f);   // above the project's default volume
            SetMember(vol, "sharedProfile", prof);

            // camera post processing on (URP)
            var camDataT = System.Type.GetType("UnityEngine.Rendering.Universal.UniversalAdditionalCameraData, Unity.RenderPipelines.Universal.Runtime");
            var cam = Camera.main;
            if (cam != null && camDataT != null)
            {
                var cd = cam.GetComponent(camDataT);
                var pp = camDataT.GetProperty("renderPostProcessing");
                if (cd != null && pp != null) pp.SetValue(cd, true);
            }
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("RobloxBridge: post effects skipped (" + e.Message + ")");
        }
    }

    static string Safe(string n)
    {
        foreach (var c in Path.GetInvalidFileNameChars()) n = n.Replace(c, '_');
        return n;
    }

    static void SetF(Material m, string p, float v) { if (m.HasProperty(p)) m.SetFloat(p, v); }

    // colour is baked into the texture -> white tint; untextured parts use their colour
    static Color BaseTint(MatInfo info, Texture baseTex)
    {
        Color tint = Color.white;
        if (baseTex && info.tint != null && info.tint.Length == 3)
            tint = new Color(info.tint[0], info.tint[1], info.tint[2]).gamma;
        else if (!baseTex && info.color != null && info.color.Length == 3)
            tint = new Color(info.color[0], info.color[1], info.color[2]).gamma;
        tint.a = info.alpha;
        return tint;
    }

    [System.Serializable] public class ColorMatchEntry { public string name; public float[] gain; }
    [System.Serializable] public class ColorMatch { public ColorMatchEntry[] materials; }

    /// Per-material light gains measured against a Studio screenshot (tools/match_colors.py writes
    /// <model>.colormatch.json next to the FBX). Roblox Lit squares the display colour, so a light
    /// gain g scales the base colour by sqrt(g). Only in exact Roblox style; otherwise the plain colour.
    internal static void ApplyColorMatch(string fbx, bool on)
    {
        var p = Dir(fbx) + "/" + Stem(fbx) + ".colormatch.json";
        var side = Load(fbx);
        if (side == null || side.materials == null) return;
        var cm = File.Exists(p) ? JsonUtility.FromJson<ColorMatch>(File.ReadAllText(p)) : null;
        var gains = new Dictionary<string, float[]>();
        if (cm != null && cm.materials != null)
            foreach (var e in cm.materials) if (e.gain != null && e.gain.Length >= 3) gains[e.name] = e.gain;
        string matDir = Dir(fbx) + "/" + Stem(fbx) + "_Materials";
        int n = 0;
        foreach (var info in side.materials)
        {
            var m = AssetDatabase.LoadAssetAtPath<Material>(matDir + "/" + Safe(info.name) + ".mat");
            if (m == null || !m.HasProperty("_BaseColor")) continue;
            float[] g;
            var c = BaseTint(info, Tex(fbx, info.baseMap));   // also undoes an earlier match
            if (on && gains.TryGetValue(info.name, out g))
            {
                c.r *= Mathf.Sqrt(Mathf.Clamp(g[0], 0.25f, 9f));
                c.g *= Mathf.Sqrt(Mathf.Clamp(g[1], 0.25f, 9f));
                c.b *= Mathf.Sqrt(Mathf.Clamp(g[2], 0.25f, 9f));
                n++;
            }
            if (m.GetColor("_BaseColor") != c)
            {
                m.SetColor("_BaseColor", c);
                if (m.HasProperty("_Color")) m.SetColor("_Color", c);
                EditorUtility.SetDirty(m);
            }
        }
        if (n > 0) Debug.Log("Roblox Bridge: colour match applied to " + n + " materials (" + p + ")");
    }

    static void Apply(Material m, MatInfo info, string fbx, bool urp)
    {
        var baseTex = Tex(fbx, info.baseMap);
        var normTex = Tex(fbx, info.normalMap);
        var msTex = Tex(fbx, info.metallicSmoothness);
        var emTex = Tex(fbx, info.emissionMap);

        Color tint = BaseTint(info, baseTex);
        if (m.HasProperty("_BaseColor")) m.SetColor("_BaseColor", tint);
        if (m.HasProperty("_Color")) m.SetColor("_Color", tint);
        if (m.HasProperty("_BaseMap")) m.SetTexture("_BaseMap", baseTex);
        if (m.HasProperty("_MainTex")) m.SetTexture("_MainTex", baseTex);

        if (m.HasProperty("_BumpMap")) m.SetTexture("_BumpMap", normTex);
        // some built-in materials read noisier than Studio: weaker normals, softer mips (matched against Studio's facade)
        float bump = 1f, bias = 0f;
        var mk = m.name.Contains("_Builtin_") ? m.name.Substring(0, m.name.IndexOf("_Builtin_")) : null;
        if (mk != null && BuiltinLook.TryGetValue(mk, out var look)) { bump = look.x; bias = look.y; }
        SetF(m, "_BumpScale", bump);
        foreach (var t in new[] { baseTex, normTex, msTex })
        {
            var ti = t != null ? AssetImporter.GetAtPath(AssetDatabase.GetAssetPath(t)) as TextureImporter : null;
            if (ti != null && !Mathf.Approximately(ti.mipMapBias, bias)) { ti.mipMapBias = bias; ti.SaveAndReimport(); }
        }
        if (normTex) m.EnableKeyword("_NORMALMAP"); else m.DisableKeyword("_NORMALMAP");

        SetF(m, "_Metallic", msTex ? 1f : info.metallic);
        SetF(m, "_Smoothness", msTex ? 1f : info.smoothness);
        SetF(m, "_Glossiness", msTex ? 1f : info.smoothness);
        SetF(m, "_GlossMapScale", 1f);
        SetF(m, "_SmoothnessTextureChannel", 0f);
        if (m.HasProperty("_MetallicGlossMap")) m.SetTexture("_MetallicGlossMap", msTex);
        if (msTex) m.EnableKeyword(urp ? "_METALLICSPECGLOSSMAP" : "_METALLICGLOSSMAP");
        else { m.DisableKeyword("_METALLICSPECGLOSSMAP"); m.DisableKeyword("_METALLICGLOSSMAP"); }

        bool hasEm = emTex || (info.emission != null && info.emission.Length == 3);
        if (hasEm && m.HasProperty("_EmissionColor"))
        {
            if (emTex)
            {
                if (m.HasProperty("_EmissionMap")) m.SetTexture("_EmissionMap", emTex);
                float k = Mathf.Max(0f, info.emissionStrength);
                m.SetColor("_EmissionColor", new Color(k, k, k));
            }
            else m.SetColor("_EmissionColor", new Color(info.emission[0], info.emission[1], info.emission[2]));
            m.EnableKeyword("_EMISSION");
            m.globalIlluminationFlags = MaterialGlobalIlluminationFlags.BakedEmissive;
        }
        else m.DisableKeyword("_EMISSION");

        if (info.mode == "Transparent") SetTransparent(m, urp);
        else if (info.mode == "Cutout") SetCutout(m, urp);
        else SetOpaque(m, urp);
    }

    static void SetOpaque(Material m, bool urp)
    {
        if (urp) { SetF(m, "_Surface", 0); SetF(m, "_AlphaClip", 0); m.DisableKeyword("_SURFACE_TYPE_TRANSPARENT"); m.SetShaderPassEnabled("DepthOnly", true); }
        else SetF(m, "_Mode", 0);
        SetF(m, "_SrcBlend", (float)BlendMode.One);
        SetF(m, "_DstBlend", (float)BlendMode.Zero);
        SetF(m, "_ZWrite", 1);
        m.DisableKeyword("_ALPHATEST_ON");
        m.DisableKeyword("_ALPHABLEND_ON");
        m.SetOverrideTag("RenderType", "Opaque");
        m.renderQueue = -1;
    }

    static void SetCutout(Material m, bool urp)
    {
        if (urp) { SetF(m, "_Surface", 0); SetF(m, "_AlphaClip", 1); m.SetShaderPassEnabled("DepthOnly", true); }
        else SetF(m, "_Mode", 1);
        SetF(m, "_Cutoff", 0.5f);
        SetF(m, "_SrcBlend", (float)BlendMode.One);
        SetF(m, "_DstBlend", (float)BlendMode.Zero);
        SetF(m, "_ZWrite", 1);
        m.EnableKeyword("_ALPHATEST_ON");
        m.DisableKeyword("_ALPHABLEND_ON");
        m.SetOverrideTag("RenderType", "TransparentCutout");
        m.renderQueue = (int)RenderQueue.AlphaTest;
    }

    static void SetTransparent(Material m, bool urp)
    {
        if (urp) { SetF(m, "_Surface", 1); SetF(m, "_Blend", 0); SetF(m, "_AlphaClip", 0); m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT"); m.SetShaderPassEnabled("DepthOnly", false); }
        else { SetF(m, "_Mode", 3); m.EnableKeyword("_ALPHABLEND_ON"); }
        SetF(m, "_SrcBlend", (float)BlendMode.SrcAlpha);
        SetF(m, "_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
        SetF(m, "_ZWrite", 0);
        m.DisableKeyword("_ALPHATEST_ON");
        m.SetOverrideTag("RenderType", "Transparent");
        m.renderQueue = (int)RenderQueue.Transparent;
    }
}
}
#endif
