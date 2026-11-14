using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Unity.Collections;

namespace RobloxBridge
{
/// Bakes the Roblox Lit shader's real-time lamp lighting into the scene's lightmaps (Shaders/RobloxLampBake.shader):
/// every lightmap texel gets sum over lamps of colour x intensity x RbxDistanceFalloff x spot cone x N.L x shadow,
/// the same maths the shader uses for real-time lamps, with hard shadows that start at each light's shadow near
/// plane like its real-time shadows. Unity's own baker can't run that shader, so it can't reproduce the look.
/// Needs a lightmap layout first (a Unity bake with the lamps real-time); overwrites those lightmap files.
///
/// GPU-safe: each lightmap is worked on in tiles (small render targets) and the GPU is waited on every few lamps.
/// The first version rendered whole 2048 lightmaps at 2x2 supersampling in float targets (~650 MB at once) and
/// queued hundreds of passes without waiting, which hung the graphics driver and the whole PC.
static class RobloxLampBaker
{
    const int ShadowRes = 1024;
    const int SuperSample = 2;   // light worked out at 2x2 points per lightmap texel, then averaged (clean part edges; 4x tested, no better)
    const int TileOut = 512;     // lightmap texels per tile side (the GPU works on TileOut*SuperSample)
    const int LampsPerSync = 4;  // wait for the GPU after this many lamps
    static readonly Vector3[] FaceFwd = { Vector3.right, Vector3.left, Vector3.up, Vector3.down, Vector3.forward, Vector3.back };
    static readonly Vector3[] FaceUp = { Vector3.up, Vector3.up, Vector3.back, Vector3.forward, Vector3.up, Vector3.up };

    /// The live lamp grid's light (RobloxLampGrid: its lamps, static shadow cubes and leak, through the shader's own
    /// RbxGridLamps) baked into the lightmaps, so the map looks exactly like the grid and the Quest only reads a
    /// lightmap. The grid has to be built and on. Returns null on success, else what went wrong.
    public static string BakeGrid(GameObject instance)
    {
        var shader = Shader.Find("Hidden/Roblox Bridge/Grid Bake");
        if (shader == null) return "grid bake shader missing";
        var gm = new Material(shader);
        try { return Bake(instance, new Light[0], 0f, gm); }
        finally { Object.DestroyImmediate(gm); }
    }

    /// Returns null on success, else what went wrong.
    public static string Bake(GameObject instance, IList<Light> lamps, float falloffK, Material gridMat = null)
    {
        var lightmaps = LightmapSettings.lightmaps;
        if (lightmaps == null || lightmaps.Length == 0) return "no lightmaps to write into (bake the sky lighting first)";
        var shader = Shader.Find("Hidden/Roblox Bridge/Lamp Bake");
        var covShader = Shader.Find("Hidden/Roblox Bridge/Lamp Bake Coverage");
        if (shader == null || covShader == null) return "lamp bake shaders missing";
        var mat = new Material(shader);
        var cov = new Material(covShader);

        var byLightmap = new Dictionary<int, List<MeshRenderer>>();
        var casters = new List<MeshRenderer>();
        foreach (var r in Object.FindObjectsByType<MeshRenderer>())
        {
            if (!r.enabled || !r.gameObject.activeInHierarchy) continue;
            if (r.shadowCastingMode != ShadowCastingMode.Off) casters.Add(r);
            if (r.lightmapIndex < 0 || r.lightmapIndex >= lightmaps.Length) continue;
            List<MeshRenderer> list;
            if (!byLightmap.TryGetValue(r.lightmapIndex, out list)) byLightmap[r.lightmapIndex] = list = new List<MeshRenderer>();
            list.Add(r);
        }

        int hi = TileOut * SuperSample;
        var shadow = NewRT(ShadowRes, ShadowRes, RenderTextureFormat.RFloat, 24, 6);
        var pos = NewRT(hi, hi, RenderTextureFormat.ARGBFloat, 24);
        var nrm = NewRT(hi, hi, RenderTextureFormat.ARGBHalf, 0);
        var acc = NewRT(hi, hi, RenderTextureFormat.ARGBHalf, 0);
        var accDir = NewRT(hi, hi, RenderTextureFormat.ARGBFloat, 0);   // sum of lamp direction x luminance, luminance
        var maskHi = NewRT(hi, hi, RenderTextureFormat.RHalf, 0);
        var tileOut = NewRT(TileOut, TileOut, RenderTextureFormat.ARGBHalf, 0);
        var tileMask = NewRT(TileOut, TileOut, RenderTextureFormat.RHalf, 0);
        var tileDir = NewRT(TileOut, TileOut, RenderTextureFormat.ARGBFloat, 0);
        var tileNrm = NewRT(TileOut, TileOut, RenderTextureFormat.ARGBHalf, 0);
        var faceVP = new Matrix4x4[6];
        var rts = new List<RenderTexture> { shadow, pos, nrm, acc, accDir, maskHi, tileOut, tileMask, tileDir, tileNrm };
        int written = 0, mapNo = 0, buried = 0;
        var tris = new Dictionary<Mesh, (Vector3[] v, int[] t)>();
        Physics.SyncTransforms();
        try
        {
            foreach (var kv in byLightmap)
            {
                mapNo++;
                var tex = lightmaps[kv.Key].lightmapColor;
                string path = tex != null ? AssetDatabase.GetAssetPath(tex) : null;
                if (string.IsNullOrEmpty(path)) continue;
                int w = tex.width, h = tex.height;
                // directional lightmap (when Unity laid out "Directional" maps): written too, so normal maps shade baked light
                var dirAsset = lightmaps[kv.Key].lightmapDir;
                string dirPath = dirAsset != null ? AssetDatabase.GetAssetPath(dirAsset) : null;
                var outTex = new Texture2D(w, h, TextureFormat.RGBAHalf, false, true);
                outTex.SetPixels(new Color[w * h]);   // all black, alpha 0 = not covered
                var dirSum = new Texture2D(w, h, TextureFormat.RGBAFloat, false, true);
                dirSum.SetPixels(new Color[w * h]);
                var nrmTex = new Texture2D(w, h, TextureFormat.RGBAHalf, false, true);
                nrmTex.SetPixels(new Color[w * h]);
                int tilesX = Mathf.CeilToInt(w / (float)TileOut), tilesY = Mathf.CeilToInt(h / (float)TileOut);

                for (int ty = 0; ty < tilesY; ty++)
                for (int tx = 0; tx < tilesX; tx++)
                {
                    var tile = new Vector4(tx * TileOut / (float)w, ty * TileOut / (float)h, TileOut / (float)w, TileOut / (float)h);
                    // renderers whose lightmap rectangle overlaps this tile
                    var inTile = new List<MeshRenderer>();
                    Bounds tb = default;
                    foreach (var r in kv.Value)
                    {
                        var so = r.lightmapScaleOffset;
                        if (so.z > tile.x + tile.z || so.z + so.x < tile.x || so.w > tile.y + tile.w || so.w + so.y < tile.y) continue;
                        if (inTile.Count == 0) tb = r.bounds; else tb.Encapsulate(r.bounds);
                        inTile.Add(r);
                    }
                    if (inTile.Count == 0) continue;
                    if (EditorUtility.DisplayCancelableProgressBar("Roblox lamp bake",
                        "Lightmap " + mapNo + "/" + byLightmap.Count + ", tile " + (ty * tilesX + tx + 1) + "/" + (tilesX * tilesY),
                        (mapNo - 1 + (ty * tilesX + tx) / (float)(tilesX * tilesY)) / byLightmap.Count))
                        return "cancelled";

                    // 1. world position + normal of every sub-texel of the tile
                    var cb = new CommandBuffer { name = "Roblox lamp bake: unwrap" };
                    cb.SetRenderTarget(new RenderTargetIdentifier[] { pos, nrm }, pos.depthBuffer);
                    cb.ClearRenderTarget(true, true, Color.clear);
                    cb.SetGlobalVector("_Tile", tile);
                    foreach (var r in inTile)
                    {
                        var mf = r.GetComponent<MeshFilter>();
                        if (mf == null || mf.sharedMesh == null) continue;
                        cb.SetGlobalVector("_LmST", r.lightmapScaleOffset);
                        for (int s = 0; s < mf.sharedMesh.subMeshCount; s++) cb.DrawRenderer(r, mat, s, 0);
                    }
                    cb.SetRenderTarget(new RenderTargetIdentifier[] { acc, accDir }, acc.depthBuffer);
                    cb.ClearRenderTarget(false, true, Color.clear);
                    Graphics.ExecuteCommandBuffer(cb);
                    cb.Release();
                    if (DropBuriedSamples) buried += DropBuried(pos, nrm, tris);
                    mat.SetTexture("_PosTex", pos);
                    mat.SetTexture("_NrmTex", nrm);

                    // 2a. grid bake: the whole grid in one pass
                    if (gridMat != null)
                    {
                        gridMat.SetTexture("_PosTex", pos);
                        gridMat.SetTexture("_NrmTex", nrm);
                        var gcb = new CommandBuffer { name = "Roblox grid bake" };
                        gcb.SetRenderTarget(new RenderTargetIdentifier[] { acc, accDir }, acc.depthBuffer);
                        gcb.DrawProcedural(Matrix4x4.identity, gridMat, 0, MeshTopology.Triangles, 3);
                        Graphics.ExecuteCommandBuffer(gcb);
                        gcb.Release();
                        SyncGpu(acc);
                    }
                    // 2. each lamp that reaches the tile, exactly as the Roblox Lit shader lights it in real time
                    int sinceSync = 0;
                    foreach (var l in lamps)
                    {
                        if (l == null || l.type == LightType.Directional) continue;
                        var lp = l.transform.position;
                        if (tb.SqrDistance(lp) > l.range * l.range) continue;
                        bool shadows = l.shadows != LightShadows.None;
                        cb = new CommandBuffer { name = "Roblox lamp bake: lamp" };
                        if (shadows)
                        {
                            float near = Mathf.Max(0.01f, l.shadowNearPlane);
                            var proj = Matrix4x4.Perspective(90f, 1f, near, l.range);
                            var sphere = new Bounds(lp, Vector3.one * l.range * 2f);
                            cb.SetGlobalVector("_LampPos", lp);
                            for (int f = 0; f < 6; f++)
                            {
                                var view = Matrix4x4.Scale(new Vector3(1, 1, -1)) * Matrix4x4.TRS(lp, Quaternion.LookRotation(FaceFwd[f], FaceUp[f]), Vector3.one).inverse;
                                faceVP[f] = proj * view;
                                cb.SetRenderTarget(shadow, 0, CubemapFace.Unknown, f);
                                cb.ClearRenderTarget(true, true, new Color(1e9f, 0, 0, 0));
                                cb.SetViewProjectionMatrices(view, proj);
                                foreach (var c in casters)
                                {
                                    if (!c.bounds.Intersects(sphere)) continue;
                                    var mf = c.GetComponent<MeshFilter>();
                                    if (mf == null || mf.sharedMesh == null) continue;
                                    for (int s = 0; s < mf.sharedMesh.subMeshCount; s++) cb.DrawRenderer(c, mat, s, 1);
                                }
                            }
                        }
                        float cosOuter = Mathf.Cos(l.spotAngle * 0.5f * Mathf.Deg2Rad);
                        float inv = 1f / Mathf.Max(1e-4f, 1f - cosOuter);   // inner angle 0
                        var col = l.color.linear * l.intensity;
                        cb.SetGlobalVector("_LampPos", lp);
                        cb.SetGlobalVector("_LampColor", new Vector4(col.r, col.g, col.b, 0));
                        cb.SetGlobalVector("_SpotFwd", l.transform.forward);
                        cb.SetGlobalFloat("_Range", l.range);
                        cb.SetGlobalFloat("_K", falloffK);
                        cb.SetGlobalFloat("_IsSpot", l.type == LightType.Spot ? 1 : 0);
                        cb.SetGlobalFloat("_SpotInv", inv);
                        cb.SetGlobalFloat("_SpotOff", -cosOuter * inv);
                        cb.SetGlobalFloat("_HasShadow", shadows ? 1 : 0);
                        cb.SetGlobalFloat("_Bias", 0.02f);
                        cb.SetGlobalFloat("_ShadowTexel", 2f / ShadowRes);   // shadow-map texel size per metre of distance (90 deg faces)
                        cb.SetGlobalFloat("_JunctionTol", 2f / RobloxBridgeImporter.LampTexelsPerMetre);   // two lightmap texels
                        cb.SetGlobalMatrixArray("_FaceVP", faceVP);
                        cb.SetGlobalTexture("_ShadowTex", shadow);
                        cb.SetRenderTarget(new RenderTargetIdentifier[] { acc, accDir }, acc.depthBuffer);
                        cb.DrawProcedural(Matrix4x4.identity, mat, 2, MeshTopology.Triangles, 3);
                        Graphics.ExecuteCommandBuffer(cb);
                        cb.Release();
                        if (++sinceSync >= LampsPerSync) { SyncGpu(acc); sinceSync = 0; }
                    }

                    // 3. average the covered sub-samples into the tile's texels (alpha = covered), copy into the lightmap
                    cov.SetTexture("_MainTex", pos); Graphics.Blit(pos, maskHi, cov);
                    mat.SetTexture("_Src", acc);
                    mat.SetTexture("_Mask", maskHi);
                    mat.SetTexture("_Dir", accDir);
                    mat.SetTexture("_Nrm", nrm);
                    mat.SetFloat("_SS", SuperSample);
                    var dcb = new CommandBuffer { name = "Roblox lamp bake: downsample" };
                    dcb.SetRenderTarget(new RenderTargetIdentifier[] { tileOut, tileMask, tileDir, tileNrm }, tileOut.depthBuffer);
                    dcb.DrawProcedural(Matrix4x4.identity, mat, 4, MeshTopology.Triangles, 3);
                    Graphics.ExecuteCommandBuffer(dcb);
                    dcb.Release();
                    int cw = Mathf.Min(TileOut, w - tx * TileOut), ch = Mathf.Min(TileOut, h - ty * TileOut);
                    var prev = RenderTexture.active;
                    RenderTexture.active = tileOut;
                    outTex.ReadPixels(new Rect(0, 0, cw, ch), tx * TileOut, ty * TileOut);
                    RenderTexture.active = tileDir;
                    dirSum.ReadPixels(new Rect(0, 0, cw, ch), tx * TileOut, ty * TileOut);
                    RenderTexture.active = tileNrm;
                    nrmTex.ReadPixels(new Rect(0, 0, cw, ch), tx * TileOut, ty * TileOut);
                    RenderTexture.active = prev;
                    SyncGpu(tileOut);
                }
                outTex.Apply();
                Texture2D dirOut = null;
                if (!string.IsNullOrEmpty(dirPath))
                {
                    // Unity/URP directional encoding: decoded = colour x (dot(n, dir.xyz - 0.5) + 0.5) / dir.w.
                    // dir.xyz = luminance-weighted mean direction to the lamps (shorter when light comes from several
                    // sides, so normal maps vary it less); dir.w = the same half-Lambert for the surface's own normal,
                    // so the geometric normal gets exactly the baked light back.
                    var col = outTex.GetPixels();
                    var ds = dirSum.GetPixels();
                    var nr = nrmTex.GetPixels();
                    var dir = new Color[w * h];
                    for (int i = 0; i < col.Length; i++)
                    {
                        var n0 = new Vector3(nr[i].r, nr[i].g, nr[i].b);
                        n0 = n0.sqrMagnitude > 1e-6f ? n0.normalized : Vector3.up;
                        var d = ds[i];
                        if (gridMat != null)
                        {
                            // grid bake: H with n0.H = 1, light for normal N = E0 x N.H (RbxSampleLightmap)
                            float e0 = col[i].r * 0.2126f + col[i].g * 0.7152f + col[i].b * 0.0722f;
                            var hv = n0;
                            if (col[i].a >= 0.5f && e0 > 1e-6f)
                            {
                                var g = new Vector3(d.r, d.g, d.b) / e0;
                                hv = g + (1f - Vector3.Dot(n0, g)) * n0;
                                var tg = hv - n0;   // across the surface (n0.H = 1 fixed)
                                if (tg.magnitude > 3.8f) hv = n0 + tg * (3.8f / tg.magnitude);
                            }
                            dir[i] = new Color(hv.x / 8f + 0.5f, hv.y / 8f + 0.5f, hv.z / 8f + 0.5f, 1f);
                            continue;
                        }
                        var v = col[i].a < 0.5f || d.a <= 1e-6f ? Vector3.zero : Vector3.ClampMagnitude(new Vector3(d.r, d.g, d.b) / d.a, 1f);
                        float hl = Vector3.Dot(n0, v) * 0.5f + 0.5f;
                        if (hl < 0.1f) { v = Vector3.zero; hl = 0.5f; }   // light mostly from behind the face: no direction
                        dir[i] = new Color(v.x * 0.5f + 0.5f, v.y * 0.5f + 0.5f, v.z * 0.5f + 0.5f, hl);
                    }
                    outTex.SetPixels(col);
                    outTex.Apply();
                    dirOut = new Texture2D(w, h, TextureFormat.RGBAHalf, false, true);
                    dirOut.SetPixels(dir);
                    dirOut.Apply();
                }
                Object.DestroyImmediate(dirSum);
                Object.DestroyImmediate(nrmTex);

                // 4. fill a few texels around each UV island so filtering doesn't pull in black (whole map, half floats)
                var cover = NewRT(w, h, RenderTextureFormat.RHalf, 0);
                cov.SetTexture("_MainTex", outTex); Graphics.Blit(outTex, cover, cov);   // coverage from alpha
                Dilate(mat, outTex, cover);
                if (dirOut != null) Dilate(mat, dirOut, cover);
                Free(cover);

                // 5. overwrite the lightmap files
                File.WriteAllBytes(Path.GetFullPath(path), outTex.EncodeToEXR(Texture2D.EXRFlags.CompressZIP));
                Object.DestroyImmediate(outTex);
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
                if (dirOut != null)
                {
                    var px = dirOut.GetPixels();
                    var ldr = new Texture2D(w, h, TextureFormat.RGBA32, false, true);
                    ldr.SetPixels(px);
                    ldr.Apply();
                    File.WriteAllBytes(Path.GetFullPath(dirPath), ldr.EncodeToPNG());
                    Object.DestroyImmediate(ldr);
                    Object.DestroyImmediate(dirOut);
                    AssetDatabase.ImportAsset(dirPath, ImportAssetOptions.ForceUpdate);
                }
                written++;
            }
        }
        finally
        {
            EditorUtility.ClearProgressBar();
            foreach (var rt in rts) Free(rt);
            Object.DestroyImmediate(mat);
            Object.DestroyImmediate(cov);
        }
        Debug.Log("Roblox Bridge: baked " + (gridMat != null ? "the lamp grid" : lamps.Count + " lamps") + " into " + written + " lightmaps (" + buried + " buried samples left out)");
        return written > 0 ? null : "no lightmap files were written";
    }

    public static bool DropBuriedSamples = true;
    const float BuriedProbe = 0.06f;   // metres: how far along the normal a covering part is looked for

    /// Lightmap samples inside another part (where Roblox parts overlap: a wall behind trim, a floor under a
    /// baseboard) are never seen, but they're dark, and filtering spreads that into the visible corner next to them:
    /// the dark edges of old bakes. A short ray along the normal from such a sample hits the inside (back face) of
    /// the covering part; those samples are marked uncovered, so averaging and dilation fill them from the light
    /// around them. Uses the map's mesh colliders. Returns how many were dropped.
    static int DropBuried(RenderTexture pos, RenderTexture nrm, Dictionary<Mesh, (Vector3[] v, int[] t)> tris)
    {
        int w = pos.width, h = pos.height;
        var prev = RenderTexture.active;
        var pt = new Texture2D(w, h, TextureFormat.RGBAFloat, false, true);
        var nt = new Texture2D(w, h, TextureFormat.RGBAFloat, false, true);
        RenderTexture.active = pos; pt.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        RenderTexture.active = nrm; nt.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        RenderTexture.active = prev;
        var P = pt.GetPixels(); var N = nt.GetPixels();
        Object.DestroyImmediate(nt);
        var idx = new List<int>();
        for (int i = 0; i < P.Length; i++) if (P[i].a > 0.5f) idx.Add(i);
        int dropped = 0;
        if (idx.Count > 0)
        {
            var qp = new QueryParameters(Physics.DefaultRaycastLayers, false, QueryTriggerInteraction.Ignore, true);
            var cmds = new NativeArray<RaycastCommand>(idx.Count, Allocator.TempJob);
            var hits = new NativeArray<RaycastHit>(idx.Count, Allocator.TempJob);
            for (int k = 0; k < idx.Count; k++)
            {
                var c = P[idx[k]]; var nc = N[idx[k]];
                var n = new Vector3(nc.r, nc.g, nc.b).normalized;
                cmds[k] = new RaycastCommand(new Vector3(c.r, c.g, c.b) + n * 0.002f, n, qp, BuriedProbe);
            }
            RaycastCommand.ScheduleBatch(cmds, hits, 256).Complete();
            for (int k = 0; k < idx.Count; k++)
            {
                var hit = hits[k];
                if (hit.collider == null) continue;
                // back face: the triangle that was hit faces the same way as the ray (we're inside its part)
                var mc = hit.collider as MeshCollider;
                if (mc == null || mc.sharedMesh == null || hit.triangleIndex < 0) continue;
                if (!tris.TryGetValue(mc.sharedMesh, out var m)) tris[mc.sharedMesh] = m = (mc.sharedMesh.vertices, mc.sharedMesh.triangles);
                int ti = hit.triangleIndex * 3;
                if (ti + 2 >= m.t.Length) continue;
                var tr = mc.transform;
                Vector3 a = tr.TransformPoint(m.v[m.t[ti]]), b = tr.TransformPoint(m.v[m.t[ti + 1]]), c2 = tr.TransformPoint(m.v[m.t[ti + 2]]);
                var fn = Vector3.Cross(b - a, c2 - a);   // Unity winding: points out of the front face
                if (tr.localToWorldMatrix.determinant < 0f) fn = -fn;   // mirrored parts flip it
                if (Vector3.Dot(fn, cmds[k].direction) <= 0f) continue;   // front face: something in front, not around us
                P[idx[k]] = Color.clear;
                dropped++;
            }
            cmds.Dispose(); hits.Dispose();
        }
        if (dropped > 0)
        {
            pt.SetPixels(P); pt.Apply(false);
            Graphics.Blit(pt, pos);
        }
        Object.DestroyImmediate(pt);
        return dropped;
    }

    // grow covered texels into the empty ones around them (8 texels), so bilinear filtering never pulls in black
    static void Dilate(Material mat, Texture2D tex, RenderTexture cover)
    {
        int w = tex.width, h = tex.height;
        var a = NewRT(w, h, RenderTextureFormat.ARGBHalf, 0);
        var b = NewRT(w, h, RenderTextureFormat.ARGBHalf, 0);
        var ma = NewRT(w, h, RenderTextureFormat.RHalf, 0);
        var mb = NewRT(w, h, RenderTextureFormat.RHalf, 0);
        Graphics.Blit(tex, a);
        Graphics.Blit(cover, ma);
        RenderTexture src = a, srcM = ma, dst = b, dstM = mb;
        for (int it = 0; it < 8; it++)
        {
            mat.SetTexture("_Src", src);
            mat.SetTexture("_Mask", srcM);
            var icb = new CommandBuffer { name = "Roblox lamp bake: dilate" };
            icb.SetRenderTarget(new RenderTargetIdentifier[] { dst, dstM }, dst.depthBuffer);
            icb.DrawProcedural(Matrix4x4.identity, mat, 3, MeshTopology.Triangles, 3);
            Graphics.ExecuteCommandBuffer(icb);
            icb.Release();
            SyncGpu(dst);
            var t1 = src; src = dst; dst = t1;
            var t2 = srcM; srcM = dstM; dstM = t2;
        }
        var prev = RenderTexture.active;
        RenderTexture.active = src;
        tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        var maskTex = new Texture2D(w, h, TextureFormat.RGBAHalf, false, true);
        RenderTexture.active = srcM;
        maskTex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        RenderTexture.active = prev;
        foreach (var rt in new[] { a, b, ma, mb }) Free(rt);

        // push-pull: fill every texel still empty with coverage-weighted averages from coarser levels, so the
        // lightmap's mipmaps (used for far surfaces) never average real light with black
        var px = tex.GetPixels();
        var m = maskTex.GetPixels();
        Object.DestroyImmediate(maskTex);
        var wgt = new float[px.Length];
        for (int i = 0; i < px.Length; i++) wgt[i] = m[i].r > 0.5f ? 1f : 0f;
        PushPull(px, wgt, w, h);
        tex.SetPixels(px);
        tex.Apply();
    }

    static void PushPull(Color[] px, float[] wgt, int w, int h)
    {
        if (w <= 1 && h <= 1) return;
        int w2 = Mathf.Max(1, w / 2), h2 = Mathf.Max(1, h / 2);
        var cpx = new Color[w2 * h2];
        var cw = new float[w2 * h2];
        for (int y = 0; y < h2; y++)
        for (int x = 0; x < w2; x++)
        {
            Color sum = Color.clear; float n = 0;
            for (int dy = 0; dy < 2; dy++)
            for (int dx = 0; dx < 2; dx++)
            {
                int sx = Mathf.Min(w - 1, x * 2 + dx), sy = Mathf.Min(h - 1, y * 2 + dy);
                float k = wgt[sy * w + sx];
                if (k <= 0f) continue;
                sum += px[sy * w + sx] * k; n += k;
            }
            int ci = y * w2 + x;
            cpx[ci] = n > 0f ? sum / n : Color.clear;
            cw[ci] = n > 0f ? 1f : 0f;
        }
        PushPull(cpx, cw, w2, h2);   // the coarser level is filled completely
        for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
        {
            int i = y * w + x;
            if (wgt[i] > 0f) continue;
            px[i] = cpx[Mathf.Min(h2 - 1, y / 2) * w2 + Mathf.Min(w2 - 1, x / 2)];
        }
    }

    static RenderTexture NewRT(int w, int h, RenderTextureFormat f, int depth, int slices = 0)
    {
        var rt = new RenderTexture(w, h, depth, f) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Clamp };
        if (slices > 0) { rt.dimension = TextureDimension.Tex2DArray; rt.volumeDepth = slices; }
        rt.Create();
        return rt;
    }

    static void Free(RenderTexture rt)
    {
        if (rt == null) return;
        if (RenderTexture.active == rt) RenderTexture.active = null;
        rt.Release();
        Object.DestroyImmediate(rt);
    }

    // wait until the GPU has finished everything queued so far (keeps the driver's queue short)
    static void SyncGpu(RenderTexture rt)
    {
        var req = AsyncGPUReadback.Request(rt, 0, 0, 1, 0, 1, 0, 1);
        req.WaitForCompletion();
    }
}
}
