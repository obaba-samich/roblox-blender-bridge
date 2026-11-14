using UnityEngine;
using UnityEngine.Rendering;

namespace RobloxBridge
{
/// URP renders at most ~256 additional-light shadow slices per frame (a point light takes 6, a spot 1);
/// with more it drops shadows it picks itself and logs a warning every frame. Roblox only shadows local
/// lights near the camera as well, so before each camera renders this gives shadows to the lights
/// nearest to it (by distance minus range) until the budget is used, and none to the rest.
[ExecuteAlways]
public class RobloxShadowBudget : MonoBehaviour
{
    [Tooltip("Lights that cast shadows in Roblox (filled in by the importer).")]
    public Light[] shadowed = new Light[0];
    [Tooltip("Shadow slices per frame (point light = 6, spot = 1). URP's limit is 256.")]
    public int maxSlices = 240;

    float[] keys = new float[0];
    int[] order = new int[0];

    void OnEnable() { RenderPipelineManager.beginCameraRendering += Begin; }
    void OnDisable() { RenderPipelineManager.beginCameraRendering -= Begin; }

    void Begin(ScriptableRenderContext context, Camera cam)
    {
        int n = shadowed.Length;
        if (n == 0 || cam.cameraType == CameraType.Preview || cam.cameraType == CameraType.Reflection) return;
        if (keys.Length != n) { keys = new float[n]; order = new int[n]; }
        var p = cam.transform.position;
        for (int i = 0; i < n; i++)
        {
            var l = shadowed[i];
            order[i] = i;
            keys[i] = l != null && l.isActiveAndEnabled ? Vector3.Distance(p, l.transform.position) - l.range : float.MaxValue;
        }
        System.Array.Sort(keys, order);
        int used = 0;
        for (int k = 0; k < n; k++)
        {
            var l = shadowed[order[k]];
            if (l == null) continue;
            int cost = l.type == LightType.Point ? 6 : 1;
            bool on = keys[k] < float.MaxValue && used + cost <= maxSlices;
            if (on) used += cost;
            var want = on ? LightShadows.Hard : LightShadows.None;
            if (l.shadows != want) l.shadows = want;
        }
    }
}
}
