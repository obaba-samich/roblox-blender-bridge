using UnityEngine;

namespace RobloxBridge
{
/// Set by the importer's lamp bake: the Roblox lamps' light is in the lightmaps (baked with the Roblox Lit
/// shader's own lamp maths), so the lamps themselves are switched off. Baking without "Bake lights too"
/// switches them back on.
[DisallowMultipleComponent]
public class RobloxLampBake : MonoBehaviour
{
    public bool baked;
    [Tooltip("The lamps whose light is baked (switched off while baked).")]
    public Light[] lamps = new Light[0];
}
}
