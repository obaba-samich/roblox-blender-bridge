using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace RobloxBridge
{
/// Builds the RobloxLampGrid of a Roblox model instance: the lamp arrays and the world-space grid of which
/// lamps reach each cell (a lamp is listed in every cell its Range sphere touches). Nothing in the lobby
/// moves, so this is done once; rebuild after moving or changing lamps.
public static class RobloxLampGridBuilder
{
    public static float CellSize = 1f;
    public static int ShadowRes = 160;   // shadow cube face size (256 scored 0.056 vs live, 128 0.060); 160 keeps every lamp's cubes at ~75 MB
    // Every lamp gets a shadow cube, not just the ones with Shadows on in Roblox: up close Roblox lets unshadowed
    // lamps through walls, but from a few metres away it lights with its voxel grid, which walls block. Without
    // this, the entrance lanterns light the facade above the canopy and lamps shine through windows.
    public static bool ShadowAllLamps = true;
    const int IndexWidth = 2048;

    public static string Build(GameObject instance)
    {
        var grid = instance.GetComponent<RobloxLampGrid>();
        // the lamps: the grid's own list when it has one (it switches them off), otherwise the lights that are on
        var lamps = (grid != null && grid.lamps.Length > 0 ? grid.lamps.Where(l => l != null)
            : instance.GetComponentsInChildren<Light>(true).Where(l => l.enabled && l.gameObject.activeInHierarchy))
            .Where(l => l.type == LightType.Point || l.type == LightType.Spot).ToArray();
        if (lamps.Length == 0) return "no point or spot lights in " + instance.name;
        if (lamps.Length > RobloxLampGrid.MaxLamps) return lamps.Length + " lamps, the grid takes " + RobloxLampGrid.MaxLamps;

        // grid over everything the model draws
        var rends = instance.GetComponentsInChildren<Renderer>(true);
        var b = rends[0].bounds;
        foreach (var r in rends) b.Encapsulate(r.bounds);
        float cs = CellSize;
        var min = b.min - Vector3.one * 0.01f;
        Vector3Int dims;
        while (true)
        {
            dims = Vector3Int.CeilToInt((b.max + Vector3.one * 0.01f - min) / cs);
            if ((long)dims.x * dims.y * dims.z <= 4000000) break;
            cs *= 1.25f;
        }

        // lamp data, as URP sends it to the shader
        var pos = new Vector4[lamps.Length]; var col = new Vector4[lamps.Length];
        var dir = new Vector4[lamps.Length]; var cone = new Vector4[lamps.Length];
        for (int i = 0; i < lamps.Length; i++)
        {
            var l = lamps[i];
            var p = l.transform.position;
            pos[i] = new Vector4(p.x, p.y, p.z, 1f / Mathf.Max(l.range * l.range, 1e-4f));
            var c = GraphicsSettings.lightsUseLinearIntensity ? l.color.linear * l.intensity : (l.color * l.intensity).linear;
            if (GraphicsSettings.lightsUseColorTemperature && l.useColorTemperature) c *= Mathf.CorrelatedColorTemperatureToRGB(l.colorTemperature);
            col[i] = new Vector4(c.r, c.g, c.b, 0f);
            var f = -l.transform.forward;
            dir[i] = new Vector4(f.x, f.y, f.z, 0f);
            if (l.type == LightType.Spot)
            {
                float cosOuter = Mathf.Cos(Mathf.Deg2Rad * l.spotAngle * 0.5f);
                float cosInner = Mathf.Cos(Mathf.Deg2Rad * l.innerSpotAngle * 0.5f);
                float inv = 1f / Mathf.Max(0.001f, cosInner - cosOuter);
                cone[i] = new Vector4(inv, -cosOuter * inv, 0f, 0f);
            }
            else cone[i] = new Vector4(0f, 1f, 0f, 0f);
            cone[i].z = -1f;   // no shadow cube (set below for shadowed lamps)
        }

        // static shadow cubes for the lamps that cast shadows in Roblox (the shadow budget's list; all if there's none)
        var budget = Object.FindObjectsByType<RobloxShadowBudget>(FindObjectsInactive.Include).FirstOrDefault();
        var shadowed = budget != null ? new HashSet<Light>(budget.shadowed.Where(l => l != null)) : new HashSet<Light>(lamps);
        var shadowLamps = Enumerable.Range(0, lamps.Length).Where(i => ShadowAllLamps || shadowed.Contains(lamps[i])).ToList();
        var src = PrefabUtility.GetCorrespondingObjectFromOriginalSource(instance);
        float studs = RobloxBridgeImporter.StudScale(src != null ? AssetDatabase.GetAssetPath(src) : null);
        var shadows = RenderShadows(instance, lamps, shadowLamps, cone, studs);
        if (shadows == null && shadowLamps.Count > 0) return "shadow shader missing";

        // lamps per cell: the Range sphere touches the cell, the spot cone reaches it and (shadowed lamps) some of
        // the cell can see the lamp. Each pixel then only loops over lamps that can light it.
        var slices = new Unity.Collections.NativeArray<ushort>[shadows != null ? shadows.depth : 0];
        for (int i = 0; i < slices.Length; i++) slices[i] = shadows.GetPixelData<ushort>(0, i);
        int culledCone = 0, culledShadow = 0;
        // lamps behind walls stay listed in and next to leak cells (their light partly gets through there)
        var leak = BuildLeak(instance, grid != null ? grid.leakGroups : null, min, cs, dims);
        var leakPx = leak != null ? leak.GetPixelData<byte>(0) : default;
        System.Func<int, int, int, bool> nearLeak = (x, y, z) =>
        {
            if (leak == null) return false;
            for (int dz = -1; dz <= 1; dz++) for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++)
            {
                int xx = x + dx, yy = y + dy, zz = z + dz;
                if (xx < 0 || yy < 0 || zz < 0 || xx >= dims.x || yy >= dims.y || zz >= dims.z) continue;
                if (leakPx[(zz * dims.y + yy) * dims.x + xx] > 0) return true;
            }
            return false;
        };
        var lists = new List<int>[dims.x * dims.y * dims.z];
        for (int li = 0; li < lamps.Length; li++)
        {
            var l = lamps[li];
            var p = l.transform.position;
            float rr = l.range;
            var c0 = Vector3Int.Max(Vector3Int.zero, Vector3Int.FloorToInt((p - Vector3.one * rr - min) / cs));
            var c1 = Vector3Int.Min(dims - Vector3Int.one, Vector3Int.FloorToInt((p + Vector3.one * rr - min) / cs));
            for (int z = c0.z; z <= c1.z; z++)
            for (int y = c0.y; y <= c1.y; y++)
            for (int x = c0.x; x <= c1.x; x++)
            {
                var lo = min + new Vector3(x, y, z) * cs;
                var q = Vector3.Max(lo, Vector3.Min(p, lo + Vector3.one * cs));   // nearest point of the cell
                if ((q - p).sqrMagnitude >= rr * rr) continue;
                var cc = lo + Vector3.one * (cs * 0.5f);
                if (l.type == LightType.Spot && !InCone(l, cc, cs * 0.87f)) { culledCone++; continue; }
                if (cone[li].z >= 0f && !CellSeesLamp(slices, (int)cone[li].z, p, lo, cs) && !nearLeak(x, y, z)) { culledShadow++; continue; }
                int ci = (z * dims.y + y) * dims.x + x;
                (lists[ci] ??= new List<int>()).Add(li);
            }
        }

        // pack: identical lists share one run in the index texture
        var runs = new Dictionary<string, int>();
        var indices = new List<int>();
        var cellPx = new Color32[lists.Length];
        int maxCount = 0;
        for (int ci = 0; ci < lists.Length; ci++)
        {
            var lst = lists[ci];
            if (lst == null) continue;
            if (lst.Count > 255) return "a cell has " + lst.Count + " lamps (max 255)";
            maxCount = Mathf.Max(maxCount, lst.Count);
            var key = string.Join(",", lst);
            if (!runs.TryGetValue(key, out int off)) { off = indices.Count; runs[key] = off; indices.AddRange(lst); }
            cellPx[ci] = new Color32((byte)(off & 255), (byte)((off >> 8) & 255), (byte)((off >> 16) & 255), (byte)lst.Count);
        }
        if (indices.Count >= 1 << 24) return "index list too long";

        var cells = new Texture3D(dims.x, dims.y, dims.z, TextureFormat.RGBA32, false) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Clamp, name = instance.name + " lamp grid cells" };
        cells.SetPixels32(cellPx);
        cells.Apply(false, true);
        int ih = Mathf.Max(1, (indices.Count + IndexWidth - 1) / IndexWidth);
        var idxPx = new Color32[IndexWidth * ih];
        for (int i = 0; i < indices.Count; i++) idxPx[i] = new Color32((byte)(indices[i] & 255), (byte)(indices[i] >> 8), 0, 0);
        var index = new Texture2D(IndexWidth, ih, TextureFormat.RG16, false, true) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Clamp, name = instance.name + " lamp grid index" };
        index.SetPixels32(idxPx);
        index.Apply(false, true);


        // save the textures next to the scene
        var scene = instance.scene;
        if (string.IsNullOrEmpty(scene.path)) return "save the scene first";
        var folder = System.IO.Path.ChangeExtension(scene.path, null);
        if (!AssetDatabase.IsValidFolder(folder)) AssetDatabase.CreateFolder(System.IO.Path.GetDirectoryName(scene.path), System.IO.Path.GetFileName(folder));
        string cellsPath = folder + "/LampGridCells.asset", indexPath = folder + "/LampGridIndex.asset";
        string shadowsPath = folder + "/LampGridShadows.asset", leakPath = folder + "/LampGridLeak.asset";
        AssetDatabase.DeleteAsset(cellsPath); AssetDatabase.DeleteAsset(indexPath); AssetDatabase.DeleteAsset(shadowsPath); AssetDatabase.DeleteAsset(leakPath);
        if (leak != null) AssetDatabase.CreateAsset(leak, leakPath);
        AssetDatabase.CreateAsset(cells, cellsPath);
        AssetDatabase.CreateAsset(index, indexPath);
        if (shadows != null) AssetDatabase.CreateAsset(shadows, shadowsPath);

        if (grid == null) grid = Undo.AddComponent<RobloxLampGrid>(instance);
        Undo.RecordObject(grid, "Build lamp grid");
        grid.lamps = lamps; grid.cells = cells; grid.index = index; grid.shadows = shadows; grid.leak = leak;
        grid.gridMin = min; grid.cellSize = cs; grid.dims = dims;
        grid.posRange = pos; grid.color = col; grid.spotDir = dir; grid.cone = cone;
        EditorUtility.SetDirty(grid);
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        grid.Apply(grid.isActiveAndEnabled);
        return lamps.Length + " lamps, " + dims.x + "x" + dims.y + "x" + dims.z + " cells of " + cs.ToString("0.##") + " m, up to "
            + maxCount + " lamps per cell, " + indices.Count + " indices (" + runs.Count + " distinct lists), "
            + shadowLamps.Count + " shadow cubes of " + ShadowRes + "px; dropped " + culledCone + " cell-lamp pairs outside spot cones, "
            + culledShadow + " fully behind walls";
    }

    // leak cells: 1 where the cell centre is inside the bounds of a child of a leak group (each elevator cabin)
    static Texture3D BuildLeak(GameObject instance, string[] groups, Vector3 min, float cs, Vector3Int dims)
    {
        if (groups == null || groups.Length == 0) return null;
        var boxes = new List<Bounds>();
        foreach (var t in instance.GetComponentsInChildren<Transform>(true).Where(t => System.Array.IndexOf(groups, t.name) >= 0))
            foreach (Transform c in t)
            {
                var rs = c.GetComponentsInChildren<Renderer>(true);
                if (rs.Length == 0) continue;
                var b = rs[0].bounds;
                foreach (var r in rs) b.Encapsulate(r.bounds);
                boxes.Add(b);
            }
        if (boxes.Count == 0) return null;
        var px = new byte[dims.x * dims.y * dims.z];
        foreach (var b in boxes)
        {
            var c0 = Vector3Int.Max(Vector3Int.zero, Vector3Int.FloorToInt((b.min - min) / cs));
            var c1 = Vector3Int.Min(dims - Vector3Int.one, Vector3Int.FloorToInt((b.max - min) / cs));
            for (int z = c0.z; z <= c1.z; z++)
            for (int y = c0.y; y <= c1.y; y++)
            for (int x = c0.x; x <= c1.x; x++)
                if (b.Contains(min + (new Vector3(x, y, z) + Vector3.one * 0.5f) * cs)) px[(z * dims.y + y) * dims.x + x] = 255;
        }
        // grow by a cell: the texture is filtered, and the cabin's own walls sit at the edge of its bounds
        var grown = (byte[])px.Clone();
        for (int z = 0; z < dims.z; z++) for (int y = 0; y < dims.y; y++) for (int x = 0; x < dims.x; x++)
        {
            if (px[(z * dims.y + y) * dims.x + x] == 0) continue;
            for (int dz = -1; dz <= 1; dz++) for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++)
            {
                int xx = x + dx, yy = y + dy, zz = z + dz;
                if (xx >= 0 && yy >= 0 && zz >= 0 && xx < dims.x && yy < dims.y && zz < dims.z) grown[(zz * dims.y + yy) * dims.x + xx] = 255;
            }
        }
        px = grown;
        var tex = new Texture3D(dims.x, dims.y, dims.z, TextureFormat.R8, false) { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp, name = instance.name + " lamp grid leak" };
        tex.SetPixelData(px, 0);
        tex.Apply(false, false);   // kept readable: the builder reads it back for the lamp lists
        return tex;
    }

    // the cone (plus the cell's bounding radius) reaches the cell centre
    static bool InCone(Light l, Vector3 c, float radius)
    {
        var v = c - l.transform.position;
        float d = v.magnitude;
        if (d <= radius) return true;
        float ang = Vector3.Angle(l.transform.forward, v) - Mathf.Asin(Mathf.Clamp01(radius / d)) * Mathf.Rad2Deg;
        return ang < l.spotAngle * 0.5f + 2f;
    }

    // some point in or just around the cell sees the lamp in its shadow cube (generous, so nothing visible is dropped)
    static bool CellSeesLamp(Unity.Collections.NativeArray<ushort>[] slices, int first, Vector3 lamp, Vector3 lo, float cs)
    {
        const int n = 6; const float margin = 0.25f;
        int res = ShadowRes;
        for (int z = 0; z < n; z++)
        for (int y = 0; y < n; y++)
        for (int x = 0; x < n; x++)
        {
            var q = lo - Vector3.one * margin + new Vector3(x, y, z) * ((cs + 2f * margin) / (n - 1));
            var d = q - lamp;
            float dist = d.magnitude;
            if (dist < 0.3f) return true;
            var a = new Vector3(Mathf.Abs(d.x), Mathf.Abs(d.y), Mathf.Abs(d.z));
            int axis = a.x >= a.y && a.x >= a.z ? 0 : (a.y >= a.z ? 1 : 2);
            float c = d[axis];
            var uv = (axis == 0 ? new Vector2(d.y, d.z) : axis == 1 ? new Vector2(d.x, d.z) : new Vector2(d.x, d.y)) / Mathf.Abs(c) * 0.5f + Vector2.one * 0.5f;
            int face = axis * 2 + (c < 0f ? 1 : 0);
            int px = Mathf.Clamp((int)(uv.x * res), 0, res - 1), py = Mathf.Clamp((int)(uv.y * res), 0, res - 1);
            // the 3x3 texels around it (a gap narrower than a texel still counts)
            for (int oy = -1; oy <= 1; oy++)
            for (int ox = -1; ox <= 1; ox++)
            {
                int sx = Mathf.Clamp(px + ox, 0, res - 1), sy = Mathf.Clamp(py + oy, 0, res - 1);
                if (Mathf.HalfToFloat(slices[first + face][sy * res + sx]) >= dist - 0.3f) return true;
            }
        }
        return false;
    }

    // each shadowed lamp: 6 faces of distance-from-the-lamp, nearest caster (renderers that cast shadows, both sides)
    static Texture2DArray RenderShadows(GameObject instance, Light[] lamps, List<int> shadowLamps, Vector4[] cone, float studs)
    {
        if (shadowLamps.Count == 0) return null;
        var sh = Shader.Find("Hidden/Roblox Bridge/Lamp Grid Shadow");
        if (sh == null) return null;
        var mat = new Material(sh);
        var casters = instance.GetComponentsInChildren<MeshRenderer>(true)
            .Where(r => r.enabled && r.gameObject.activeInHierarchy && r.shadowCastingMode != ShadowCastingMode.Off).ToList();
        int res = ShadowRes;
        var arr = new Texture2DArray(res, res, shadowLamps.Count * 6, TextureFormat.RHalf, false, true)
            { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Clamp, name = instance.name + " lamp grid shadows" };
        var rt = new RenderTexture(res, res, 0, RenderTextureFormat.RHalf, RenderTextureReadWrite.Linear);
        rt.Create();
        var read = new Texture2D(res, res, TextureFormat.RHalf, false, true);
        try
        {
            for (int k = 0; k < shadowLamps.Count; k++)
            {
                var l = lamps[shadowLamps[k]];
                cone[shadowLamps[k]].z = k * 6;
                var p = l.transform.position;
                var bounds = new Bounds(p, Vector3.one * 2f * l.range);
                // the lamp's own fixture (shade, housing, bulb) doesn't block it: the importer's rule for shadowed
                // lamps, applied here per lamp so the unshadowed ones get it too. Glass still casts, as in Roblox.
                float fr = RobloxBridgeImporter.FixtureRadiusStuds * studs, fs = RobloxBridgeImporter.FixtureSizeStuds * studs;
                var own = new HashSet<MeshRenderer>(casters.Where(r => r.bounds.SqrDistance(p) < fr * fr && r.bounds.size.magnitude < fs
                    && !(RobloxBridgeImporter.IsTransparent(r) && !RobloxBridgeImporter.IsNearlyInvisible(r))));
                if (EditorUtility.DisplayCancelableProgressBar("Roblox lamp grid", "Shadows " + (k + 1) + "/" + shadowLamps.Count, k / (float)shadowLamps.Count))
                    return null;
                for (int f = 0; f < 6; f++)
                {
                    var cb = new CommandBuffer { name = "Roblox lamp grid shadow" };
                    cb.SetRenderTarget(rt);
                    cb.ClearRenderTarget(false, true, new Color(65000f, 0f, 0f, 0f));
                    cb.SetGlobalVector("_LampPos", p);
                    cb.SetGlobalFloat("_Face", f);
                    cb.SetGlobalFloat("_Near", l.shadowNearPlane > 0.11f ? l.shadowNearPlane : Mathf.Clamp(RobloxBridgeImporter.ShadowNearStuds * studs, 0.1f, 10f));
                    foreach (var r in casters)
                    {
                        if (!r.bounds.Intersects(bounds) || own.Contains(r)) continue;
                        var mf = r.GetComponent<MeshFilter>();
                        if (mf == null || mf.sharedMesh == null) continue;
                        for (int s = 0; s < mf.sharedMesh.subMeshCount; s++) cb.DrawRenderer(r, mat, s, 0);
                    }
                    Graphics.ExecuteCommandBuffer(cb);
                    cb.Release();
                    var prev = RenderTexture.active;
                    RenderTexture.active = rt;
                    read.ReadPixels(new Rect(0, 0, res, res), 0, 0);   // also waits for the GPU (no long queue)
                    read.Apply(false);
                    RenderTexture.active = prev;
                    arr.SetPixelData(read.GetPixelData<ushort>(0), 0, k * 6 + f);
                }
            }
        }
        finally
        {
            EditorUtility.ClearProgressBar();
            rt.Release(); Object.DestroyImmediate(rt); Object.DestroyImmediate(read); Object.DestroyImmediate(mat);
        }
        arr.Apply(false, false);
        return arr;
    }
}
}
