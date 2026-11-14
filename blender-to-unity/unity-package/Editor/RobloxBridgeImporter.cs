// Roblox Bridge Importer - core (import settings, lights, materials). The Roblox lighting (scene lighting,
// shader, bakes) plugs into this partial class through the hooks below when its files are in the package.
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
public partial class RobloxBridgeImporter : AssetPostprocessor
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

    // Hooks for the Roblox lighting files, when they are in the package: map scale and lightmap UVs.
    static partial void MapScaleHook(string fbx, ref float scale);
    static partial void LightmapUVHook(ModelImporter mi, string fbx);
    static float Scale(string fbx) { float s = 1f; MapScaleHook(fbx, ref s); return s; }

    // ------------------------------------------------------------- model / textures
    void OnPreprocessModel()
    {
        if (Load(assetPath) == null) return;
        var mi = (ModelImporter)assetImporter;
        mi.globalScale = Scale(assetPath);   // the whole map, lights included
        mi.useFileScale = true;
        mi.bakeAxisConversion = true;
        mi.importNormals = ModelImporterNormals.Import;
        mi.importTangents = ModelImporterTangents.CalculateMikk;
        mi.generateSecondaryUV = false;   // Blender's UVLightmap (Unity's own with calculated margins tested: 0.101 vs 0.087)
        LightmapUVHook(mi, assetPath);
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
                l.range = Mathf.Max(0.01f, info.range * Scale(assetPath));
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

    // draw UNION_* placeholder boxes (unions without exported geometry)
    public static bool ShowUnionBoxes = false;
    // built-in material -> (normal map strength, texture mip bias). Brick: Studio's is flat and soft; full-strength
    // normals and sharp mips made it twice as grainy (measured 2026-11-04).
    public static readonly Dictionary<string, Vector2> BuiltinLook = new Dictionary<string, Vector2> { { "Brick", new Vector2(0.5f, 0.25f) } };

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
