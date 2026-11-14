using UnityEngine;

namespace RobloxBridge
{
/// Lamps without URP's light loop (for Quest): every lamp's position, colour, range and cone go to the
/// Roblox Lit shader as global arrays, and a world-space grid (built in the editor, Tools/Roblox Bridge)
/// lists for each cell the lamps whose range reaches it. The shader adds those lamps per pixel with the
/// same maths as the real-time lamps (Roblox falloff, spot cone, N.L, no highlight). Shadows of the map come from
/// static shadow cubes rendered once in the editor (nothing in the map moves), so there's no shadow rendering, no
/// per-object or per-camera light limit and no CPU light culling at run time. While this is on, the
/// Light components are switched off so URP doesn't light them a second time.
[ExecuteAlways]
public class RobloxLampGrid : MonoBehaviour
{
    public const int MaxLamps = 256;   // keep in step with RBX_GRID_MAX_LAMPS in Shaders/RobloxLighting.hlsl

    public Light[] lamps = new Light[0];
    [Tooltip("Per cell: offset into the index list (rgb, 24 bits) and lamp count (a).")]
    public Texture3D cells;
    [Tooltip("Lamp indices of all cells, one after another (r + g * 256).")]
    public Texture2D index;
    [Tooltip("Static shadow cubes, 6 slices per shadowed lamp (distance from the lamp).")]
    public Texture2DArray shadows;
    [Tooltip("Shadow depth bias in metres.")]
    public float shadowBias = 0.03f;
    [Tooltip("Shadow normal offset in shadow texels.")]
    public float shadowNormalOffset = 1.0f;
    [Tooltip("Roblox's shadows are coarse and leak into small enclosed spaces (elevator cabins). Inside the bounds of " +
             "each child of these objects (by name), Leak Amount of the blocked lamp light gets through, evenly. Rebuild after changing.")]
    public string[] leakGroups = new string[0];
    [Range(0f, 1f)] public float leakAmount = 0.6f;
    [Tooltip("Per cell: 1 inside a leak group's bounds (filtered, so it fades over a cell).")]
    public Texture3D leak;
    public Vector3 gridMin;
    public float cellSize = 1f;
    public Vector3Int dims;
    // per lamp: xyz position, w 1/range^2 | rgb colour x intensity (linear) | xyz -forward | x, y spot cone
    public Vector4[] posRange = new Vector4[0], color = new Vector4[0], spotDir = new Vector4[0], cone = new Vector4[0];

    static readonly int PosId = Shader.PropertyToID("_RbxGridPos"), ColId = Shader.PropertyToID("_RbxGridColor"),
        DirId = Shader.PropertyToID("_RbxGridSpot"), ConeId = Shader.PropertyToID("_RbxGridCone"),
        CellsId = Shader.PropertyToID("_RbxGridCells"), IndexId = Shader.PropertyToID("_RbxGridIndex"),
        MinId = Shader.PropertyToID("_RbxGridMin"), DimId = Shader.PropertyToID("_RbxGridDim"),
        ShadowsId = Shader.PropertyToID("_RbxGridShadows"), ShadowParamsId = Shader.PropertyToID("_RbxGridShadowParams"),
        LeakId = Shader.PropertyToID("_RbxGridLeak");
    const string Keyword = "RBX_LAMP_GRID";

    void OnEnable() { Apply(true); }
    void OnDisable() { Apply(false); }

    public void Apply(bool on)
    {
        on &= cells != null && index != null && posRange.Length > 0;
        foreach (var l in lamps) if (l != null) l.enabled = !on;
        if (!on) { Shader.DisableKeyword(Keyword); return; }
        Shader.SetGlobalVectorArray(PosId, Pad(posRange));
        Shader.SetGlobalVectorArray(ColId, Pad(color));
        Shader.SetGlobalVectorArray(DirId, Pad(spotDir));
        Shader.SetGlobalVectorArray(ConeId, Pad(cone));
        Shader.SetGlobalTexture(CellsId, cells);
        Shader.SetGlobalTexture(IndexId, index);
        if (shadows != null) Shader.SetGlobalTexture(ShadowsId, shadows);
        if (leak != null) Shader.SetGlobalTexture(LeakId, leak);
        Shader.SetGlobalVector(ShadowParamsId, new Vector4(shadows != null ? shadows.width : 1f, shadowBias, shadowNormalOffset, leak != null ? leakAmount : 0f));
        Shader.SetGlobalVector(MinId, new Vector4(gridMin.x, gridMin.y, gridMin.z, 1f / cellSize));
        Shader.SetGlobalVector(DimId, new Vector4(dims.x, dims.y, dims.z, index.width));
        Shader.EnableKeyword(Keyword);
    }

    // global arrays keep the size they're first set with, so always send the full MaxLamps
    static Vector4[] Pad(Vector4[] v)
    {
        var o = new Vector4[MaxLamps];
        System.Array.Copy(v, o, Mathf.Min(v.Length, MaxLamps));
        return o;
    }
}
}
