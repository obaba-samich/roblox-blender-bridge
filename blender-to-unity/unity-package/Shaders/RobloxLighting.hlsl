// Roblox-style light falloff for URP, included by the generated "Roblox Bridge/Roblox Lit" shader
// before LitForwardPass.hlsl. Roblox Point/Spot/Surface lights stay almost evenly bright out to
// their Range; URP's are inverse-square (hot spot at the light, dim further out). We wrap every
// GetAdditionalLight overload and replace only the distance falloff; shadows, cookies, spot cone,
// light layers and Forward+/Deferred light loops stay URP's own.
#ifndef ROBLOX_BRIDGE_LIGHTING_INCLUDED
#define ROBLOX_BRIDGE_LIGHTING_INCLUDED

// same order as Lighting.hlsl: GlobalIllumination.hlsl needs BRDFData and the debug helpers first
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/BRDF.hlsl"
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Debug/Debugging3D.hlsl"
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/GlobalIllumination.hlsl"
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/RealtimeLights.hlsl"
#include "Packages/com.unity.render-pipelines.core/ShaderLibrary/Color.hlsl"

// Falloff curve (1 - (d/range)^2)^power: 1 at the light, 0 at Range. Higher power = fades sooner.
// The generated shader #defines RBX_FALLOFF_POWER (the window's "Light falloff"); in the editor the
// global _RbxFalloffPower overrides it so the slider is live.
#ifndef RBX_FALLOFF_POWER
#define RBX_FALLOFF_POWER 2.0
#endif
float _RbxFalloffPower;

half RbxFalloff(float x)
{
    float p = _RbxFalloffPower > 0.0 ? _RbxFalloffPower : RBX_FALLOFF_POWER;
    return half(pow(saturate(1.0 - x), p));
}

// Exact mode = Roblox Future's lighting (shader structure read from Studio's shaders, constants
// measured in Studio at quality 21 with tools/calibrate_lighting.lua):
//   light falloff  full brightness to 0.72 Range, then (1 - smoothstep(t)) * K / (K + e^2),
//                  e = d - 0.72 Range, t = e / (0.28 Range), K = 20 studs^2
//   colours        decoded with gamma 2 (colour^2), not the sRGB curve
// RBX_FALLOFF_K is K in m^2, or -1 for the (1 - (d/R)^2)^power curve above. In the editor the
// global _RbxFalloffK overrides it live (0 = not set).
#ifndef RBX_FALLOFF_K
#define RBX_FALLOFF_K -1.0
#endif
float _RbxFalloffK;

float RbxK()
{
    return _RbxFalloffK != 0.0 ? _RbxFalloffK : RBX_FALLOFF_K;
}

half RbxDistanceFalloff(float d2, float invRange2)
{
    float k = RbxK();
    if (k <= 0.0) return RbxFalloff(d2 * invRange2);
    float range = rsqrt(max(invRange2, 1e-8));
    float start = 0.72 * range;
    float e = max(sqrt(d2) - start, 0.0);
    float t = saturate(e / (range - start));
    return half((1.0 - t * t * (3.0 - 2.0 * t)) * k / (k + e * e));
}

// set while URP shades a lamp (every lamp goes through RbxGetAdditionalLight; the sun doesn't)
static bool rbxShadingLamp = false;


void RbxApplyFalloff(inout Light light, uint i, float3 positionWS)
{
    rbxShadingLamp = true;
#if USE_CLUSTER_LIGHT_LOOP
    int idx = i;
#else
    int idx = GetPerObjectLightIndex(i);
#endif
#if USE_STRUCTURED_BUFFER_FOR_LIGHT_DATA
    float4 lightPositionWS = _AdditionalLightsBuffer[idx].position;
    half4 atten = _AdditionalLightsBuffer[idx].attenuation;
    half4 spotDirection = _AdditionalLightsBuffer[idx].spotDirection;
#else
    float4 lightPositionWS = _AdditionalLightsPosition[idx];
    half4 atten = _AdditionalLightsAttenuation[idx];
    half4 spotDirection = _AdditionalLightsSpotDir[idx];
#endif
    if (lightPositionWS.w == 0.0) return;   // additional directional light: no falloff
    float3 v = lightPositionWS.xyz - positionWS;
    float d2 = max(dot(v, v), HALF_MIN);
    half3 dir = half3(v * rsqrt(d2));
    // atten.x = 1 / range^2, atten.zw = spot cone (inner angle 0 in exact mode = Roblox's cone)
    light.distanceAttenuation = RbxDistanceFalloff(d2, float(atten.x)) * AngleAttenuation(spotDirection.xyz, dir, atten.zw);
}

Light RbxGetAdditionalLight(uint i, float3 positionWS)
{
    Light l = GetAdditionalLight(i, positionWS);
    RbxApplyFalloff(l, i, positionWS);
    return l;
}

Light RbxGetAdditionalLight(uint i, float3 positionWS, half4 shadowMask)
{
    Light l = GetAdditionalLight(i, positionWS, shadowMask);
    RbxApplyFalloff(l, i, positionWS);
    return l;
}

Light RbxGetAdditionalLight(uint i, InputData inputData, half4 shadowMask, AmbientOcclusionFactor aoFactor)
{
    Light l = GetAdditionalLight(i, inputData, shadowMask, aoFactor);
    RbxApplyFalloff(l, i, inputData.positionWS);
    return l;
}

// Roblox Lighting.Ambient lights everything, even fully enclosed rooms; the sky (OutdoorAmbient,
// EnvironmentDiffuseScale) only reaches where it's visible. Unity's environment light is either
// everywhere (realtime) or occluded (baked), so the bake carries the sky and we add Ambient here,
// on top of whatever GI the pixel has. Linear colour; RBX_AMBIENT is compiled in for builds and
// the global _RbxAmbient (w = 1) overrides it live in the editor.
#ifndef RBX_AMBIENT
#define RBX_AMBIENT float3(0, 0, 0)
#endif
float4 _RbxAmbient;
float _RbxBuiltinMetallic;   // metalness scale for built-in materials in exact mode (unset = 0)

half3 RbxAmbient()
{
    return half3(_RbxAmbient.w > 0.0 ? _RbxAmbient.rgb : RBX_AMBIENT);
}

// Roblox's sky light (its shader: ambient cube x skylight, skylight = how much of the sky a 4-stud
// voxel sees). Sky-cube mode: the bake holds only sky visibility (a white sky, no bounces), and here
// light = Ambient + lerp(leak, 1, visibility) x cube(normal), the cube measured in the place
// (up / side; down uses side). Leak = sky that Roblox's coarse grid lets into rooms.
// Off (w = 0): the bake holds coloured sky light, added as is.
#ifndef RBX_SKY_UP
#define RBX_SKY_UP float4(0, 0, 0, 0)
#endif
#ifndef RBX_SKY_SIDE
#define RBX_SKY_SIDE float4(0, 0, 0, 0)
#endif
float4 _RbxSkyUp;     // rgb up-facing sky light, w = 1 when set (editor, live)
float4 _RbxSkySide;   // rgb side-facing sky light, w = leak
// per wall direction (Unity +X, -X, +Z, -Z): scale of the side light, from the 4-direction calibration
#ifndef RBX_SKY_DIR_PX
#define RBX_SKY_DIR_PX half3(1, 1, 1)
#define RBX_SKY_DIR_NX half3(1, 1, 1)
#define RBX_SKY_DIR_PZ half3(1, 1, 1)
#define RBX_SKY_DIR_NZ half3(1, 1, 1)
#endif
float4 _RbxSkyDir[4];   // w = 1 when set (editor, live)

half3 RbxSkyDir(half3 n)
{
    bool live = _RbxSkyDir[0].w > 0.0;
    half3 px = live ? half3(_RbxSkyDir[0].rgb) : RBX_SKY_DIR_PX;
    half3 nx = live ? half3(_RbxSkyDir[1].rgb) : RBX_SKY_DIR_NX;
    half3 pz = live ? half3(_RbxSkyDir[2].rgb) : RBX_SKY_DIR_PZ;
    half3 nz = live ? half3(_RbxSkyDir[3].rgb) : RBX_SKY_DIR_NZ;
    // ambient-cube weights of the horizontal part of the normal
    half wx = n.x * n.x, wz = n.z * n.z;
    half3 h = wx * (n.x > 0 ? px : nx) + wz * (n.z > 0 ? pz : nz);
    return wx + wz > 1e-4 ? h / (wx + wz) : half3(1, 1, 1);
}

half3 RbxSky(half3 normalWS, half3 bakedGI)
{
    float4 up = _RbxSkyUp.w > 0.0 ? _RbxSkyUp : RBX_SKY_UP;
    float4 side = _RbxSkyUp.w > 0.0 ? _RbxSkySide : RBX_SKY_SIDE;
    if (up.w <= 0.0) return bakedGI;   // the lightmap holds light (e.g. lamps baked with the shader's maths)
    half vis = saturate(dot(bakedGI, half3(0.2126, 0.7152, 0.0722)));
    half sky = lerp(half(side.w), 1.0, vis);
    half ny2 = normalWS.y * normalWS.y;
    half3 wall = half3(side.rgb) * RbxSkyDir(normalWS);
    half3 cube = lerp(wall, normalWS.y > 0 ? half3(up.rgb) : wall, ny2);
    return cube * sky;
}

// Roblox's tonemap (Shaders/RobloxTonemap.shader) at the end of the forward pass, for renderers without an HDR
// buffer or the Full Screen Pass (Quest, set by QuestRendering): fog and all are in, so it's the same curve.
float _RbxShaderTonemap, _RbxTonemapExposure;
half3 RbxShaderTonemap(half3 c)
{
    UNITY_BRANCH if (_RbxShaderTonemap < 0.5) return c;
    float3 x = clamp(float3(c) * _RbxTonemapExposure, 0.0, 4.0);
    float3 y = saturate(1.049 * x * (x + 0.654) / (x * (x + 0.775) + 0.2035));
    return half3(SRGBToLinear(y));
}

// Lamps baked into the lightmap with the Roblox Lit lamp maths (Editor/RobloxLampBaker): the lightmap is lamp
// light, so it's shaded like the real-time lamps were (albedo x brdf diffuse) rather than like ambient (which
// gives the reflected part away first). RbxGlobalIllumination adds it; Ambient and sky stay on the ambient path.
#ifndef RBX_LAMPS_BAKED
#define RBX_LAMPS_BAKED 0
#endif
static half3 rbxLampGI = half3(0.0, 0.0, 0.0);
float _RbxBakeCompare;

half3 RbxBakedGI(half3 normalWS, half3 bakedGI)
{
#if RBX_LAMPS_BAKED
    rbxLampGI = bakedGI;
    return RbxAmbient();
#else
    return RbxSky(normalWS, bakedGI) + RbxAmbient();
#endif
}

void RbxMixRealtimeAndBakedGI(inout Light light, half3 normalWS, inout half3 bakedGI)
{
    MixRealtimeAndBakedGI(light, normalWS, bakedGI);
    bakedGI = RbxBakedGI(normalWS, bakedGI);
}

void RbxMixRealtimeAndBakedGI(inout Light light, half3 normalWS, inout half3 bakedGI, half4 shadowMask)
{
    MixRealtimeAndBakedGI(light, normalWS, bakedGI, shadowMask);
    bakedGI = RbxBakedGI(normalWS, bakedGI);
}

void RbxMixRealtimeAndBakedGI(inout Light light, half3 normalWS, inout half3 bakedGI, AmbientOcclusionFactor aoFactor)
{
    MixRealtimeAndBakedGI(light, normalWS, bakedGI, aoFactor);
    bakedGI = RbxBakedGI(normalWS, bakedGI);
}

// Roblox squares part colours (and texture x colour) to get the surface albedo
inline void RbxInitializeStandardLitSurfaceData(float2 uv, out SurfaceData outSurfaceData)
{
    InitializeStandardLitSurfaceData(uv, outSurfaceData);
    // display-space albedo, as Roblox has it before squaring
    half3 c = LinearToSRGB(outSurfaceData.albedo);
#if defined(_RBX_TINTMASK)
    // built-in material / MaterialVariant colour map: part colour only where its alpha says so
    half4 t = SAMPLE_TEXTURE2D(_BaseMap, sampler_BaseMap, uv);
    c = LinearToSRGB(t.rgb) * lerp(half3(1.0, 1.0, 1.0), LinearToSRGB(_BaseColor.rgb), t.a);
    outSurfaceData.albedo = SRGBToLinear(c);
#endif
    if (RbxK() > 0.0) outSurfaceData.albedo = c * c;
#if defined(_RBX_TINTMASK)
    // Roblox's built-in Metal looks about as bright as a matte part of its colour (hallway cart);
    // Unity's full metalness makes dark colours a black mirror. Scale (global, 0 = matte) until measured.
    if (RbxK() > 0.0) outSurfaceData.metallic *= _RbxBuiltinMetallic;
#endif
}

// Roblox's light highlight (its material shaders, sun and Future light loop alike):
//   r = 0.089 + 0.911 x roughness map, a2 = r^4, d = NH^2 (a2 - 1) + 1
//   spec = F_schlick(LH) x (a2 + a2^2) / (d^2 (3 LH + 0.5) (0.75 NH + 0.25))   (x NdotL x light, like URP)
// URP's divides by LH^2 instead, ~3x brighter when a lamp grazes a wall, awning or floor next to it.
// Roblox also takes the reflected part out of the diffuse light: diffuse x (1 - F). URP adds
// brdfData.diffuse + brdfData.specular x this, so both are folded into the returned factor (relative to F0).
// Lamps (Point/Spot/Surface) put no visible highlight on surfaces in Studio: with them off, the Lobby
// hallway matches Studio within ~2% (carpet 134 vs 136, floor 115 vs 114, pillar 127 vs 126), with
// them it's 15-45% too bright and milky. The sun keeps Roblox's highlight. _RbxLampSpecular scales it
// (unset = 0); RbxGetAdditionalLight marks that a lamp is being shaded.
float _RbxLampSpecular;

half RbxDirectBRDFSpecular(BRDFData brdfData, half3 normalWS, half3 lightDirectionWS, half3 viewDirectionWS)
{
    if (RbxK() <= 0.0) return DirectBRDFSpecular(brdfData, normalWS, lightDirectionWS, viewDirectionWS);
    if (rbxShadingLamp && _RbxLampSpecular <= 0.0) return 0.0;   // Roblox look: lamps add no highlight (skip the maths)
    float3 h = SafeNormalize(float3(lightDirectionWS) + float3(viewDirectionWS));
    float nh = max(0.001, dot(float3(normalWS), h));
    float lh = saturate(dot(float3(lightDirectionWS), h));
    float r = 0.089 + 0.911 * brdfData.perceptualRoughness;
    float a2 = r * r * r * r;
    float d = nh * nh * (a2 - 1.0) + 1.0;
    float spec = (a2 + a2 * a2) / (d * d * (3.0 * lh + 0.5) * (0.75 * nh + 0.25));
    float f0 = max(dot(float3(brdfData.specular), float3(1.0, 1.0, 1.0) / 3.0), 1e-4);
    float fc = pow(1.0 - lh, 5.0);
    float f = f0 + (1.0 - f0) * fc;
    float diffuse = dot(float3(brdfData.diffuse), float3(1.0, 1.0, 1.0) / 3.0);
    float k = rbxShadingLamp ? _RbxLampSpecular : 1.0;
    return half(min(k * f * (spec - diffuse) / f0, 1000.0));
}

// Lamp grid (Runtime/RobloxLampGrid, for Quest): every lamp read from global arrays, the ones reaching this
// point listed by a static world-space grid. Same maths as a real-time lamp here (Roblox falloff, URP's spot
// cone, N.L; lamps add no highlight) without shadows or URP's light limits; returns the summed light, which
// is shaded with the BRDF diffuse exactly like URP shades a lamp.
#define RBX_GRID_MAX_LAMPS 256
#if defined(RBX_LAMP_GRID)
float4 _RbxGridPos[RBX_GRID_MAX_LAMPS];     // xyz position, w = 1 / range^2
float4 _RbxGridColor[RBX_GRID_MAX_LAMPS];   // rgb colour x intensity
float4 _RbxGridSpot[RBX_GRID_MAX_LAMPS];    // xyz = -forward
float4 _RbxGridCone[RBX_GRID_MAX_LAMPS];    // URP's spot attenuation (x scale, y offset; 0, 1 for point lights), z = first shadow slice (-1 none)
float4 _RbxGridMin;                         // xyz grid origin, w = 1 / cell size
float4 _RbxGridDim;                         // xyz cells, w = index texture width
TEXTURE3D(_RbxGridCells);
TEXTURE2D(_RbxGridIndex);
TEXTURE2D_ARRAY(_RbxGridShadows);           // static shadow cubes: 6 slices per shadowed lamp, distance from the lamp
SAMPLER(sampler_RbxGridShadows);
TEXTURE3D(_RbxGridLeak);                    // per cell: 1 inside small enclosed spaces Roblox's coarse shadows leak into
SAMPLER(sampler_RbxGridLeak);
float4 _RbxGridShadowParams;                // x = face resolution, y = depth bias (m), z = normal offset (shadow texels), w = leak amount

// 1 = lit. Face = axis * 2 + (negative side), u/v = the other two axes / the major one (Shaders/RobloxLampGridShadow.shader)
float RbxGridShadow(float3 positionWS, float3 normalWS, float3 lampPos, float firstSlice, float dist)
{
    if (firstSlice < 0.0) return 1.0;
    float sw, shh, slices;
    _RbxGridShadows.GetDimensions(sw, shh, slices);
    if (firstSlice + 6.0 > slices) return 1.0;
    float res = _RbxGridShadowParams.x;
    // push the point off the surface by about a shadow texel at this distance (like URP's normal bias)
    float3 d = positionWS + normalWS * (_RbxGridShadowParams.z * 2.0 * dist / res) - lampPos;
    float3 a = abs(d);
    int axis = a.x >= a.y && a.x >= a.z ? 0 : (a.y >= a.z ? 1 : 2);
    float c = axis == 0 ? d.x : axis == 1 ? d.y : d.z;
    float2 uv = (axis == 0 ? d.yz : axis == 1 ? d.xz : d.xy) / abs(c) * 0.5 + 0.5;
    int face = axis * 2 + (c < 0.0 ? 1 : 0);
    // 2x2 texels compared and blended (what URP's hard shadows get from the comparison sampler): one gather
    float2 t = uv * res - 0.5;
    float2 fr = frac(t);
    float4 occ = _RbxGridShadows.GatherRed(sampler_RbxGridShadows, float3((floor(t) + 1.0) / res, firstSlice + face));
    float4 lit = step(length(d) - _RbxGridShadowParams.y, occ);   // x (0,1) y (1,1) z (1,0) w (0,0)
    return lerp(lerp(lit.w, lit.z, fr.x), lerp(lit.x, lit.y, fr.x), fr.y);
}
#endif

// vlum (bake only, dead code otherwise): sum of each lamp's luminance (before N.L) x its direction, so a baked
// lightmap can rebuild the light for a normal-mapped normal (RobloxLampBaker.BakeGrid, RbxSampleLightmap)
float3 RbxGridLamps(float3 positionWS, half3 normalWS, out float3 vlum)
{
    float3 sum = 0.0;
    vlum = 0.0;
#if defined(RBX_LAMP_GRID)
    // every read is kept in range: an out-of-bounds texture or array read can hang the GPU (RADV)
    uint width = uint(_RbxGridDim.w);
    if (width == 0u) return sum;
    // the bound textures' own sizes, not the C# values: on an editor start the globals and textures can briefly disagree
    uint cw, chh, cd;
    _RbxGridCells.GetDimensions(cw, chh, cd);
    int3 c = int3(floor((positionWS - _RbxGridMin.xyz) * _RbxGridMin.w));
    if (any(c < 0) || any(c >= int3(min(_RbxGridDim.xyz, float3(cw, chh, cd))))) return sum;
    uint4 cell = uint4(round(LOAD_TEXTURE3D(_RbxGridCells, c) * 255.0));
    uint offset = cell.r + cell.g * 256u + cell.b * 65536u;
    uint iw, ih;
    _RbxGridIndex.GetDimensions(iw, ih);
    width = min(width, iw);
    uint total = width * ih;
    uint count = min(cell.a, 64u);
    // share of the blocked light that gets through anyway (w = leak amount, 0 = no leak texture)
    float leak = 0.0;
    if (_RbxGridShadowParams.w > 0.0)
        leak = _RbxGridShadowParams.w * SAMPLE_TEXTURE3D_LOD(_RbxGridLeak, sampler_RbxGridLeak,
            (positionWS - _RbxGridMin.xyz) * _RbxGridMin.w / _RbxGridDim.xyz, 0).r;
    for (uint k = 0u; k < count; k++)
    {
        uint o = offset + k;
        if (o >= total) break;
        uint2 e = uint2(round(LOAD_TEXTURE2D(_RbxGridIndex, int2(o % width, o / width)).rg * 255.0));
        uint li = min(e.x + e.y * 256u, uint(RBX_GRID_MAX_LAMPS - 1));
        float4 pr = _RbxGridPos[li];
        float3 v = pr.xyz - positionWS;
        float d2 = max(dot(v, v), HALF_MIN);
        if (d2 * pr.w >= 1.0) continue;
        float3 l = v * rsqrt(d2);
        float ndl = saturate(dot(float3(normalWS), l));
        if (ndl <= 0.0) continue;
        float4 cn = _RbxGridCone[li];
        float spot = saturate(dot(_RbxGridSpot[li].xyz, l) * cn.x + cn.y);
        if (spot <= 0.0) continue;
        float sh = lerp(RbxGridShadow(positionWS, float3(normalWS), pr.xyz, cn.z, sqrt(d2)), 1.0, leak);
        float3 c = _RbxGridColor[li].rgb * (RbxDistanceFalloff(d2, pr.w) * spot * spot * sh);
        sum += c * ndl;
        vlum += l * dot(c, float3(0.2126, 0.7152, 0.0722));
    }
#endif
    return sum;
}

float3 RbxGridLamps(float3 positionWS, half3 normalWS)
{
    float3 vlum;
    return RbxGridLamps(positionWS, normalWS, vlum);
}

// Grid bake with normals (RobloxLampBaker.BakeGrid): the lightmap holds the light E0 for the surface's own normal
// n0, the directional map a vector H with n0.H = 1 (8 x (rgb - 0.5)), such that the light for any normal N is
// E0 x N.H: the live grid's sum of lamps x N.L, exact for n0 and as long as no lamp drops behind the bumped
// normal. Unity's directional format (half-Lambert) only gives about half of the normal map's contrast.
// Both maps are read at mip 0: at a part's edge the 2x2 pixel block spans unrelated lightmap islands, the UV
// derivatives jump and the GPU would pick a tiny mip that blends neighbouring parts in (dark part outlines).
// The lightmap is smooth, so mip 0 at any distance doesn't shimmer.
#if defined(LIGHTMAP_ON) && defined(DIRLIGHTMAP_COMBINED) && RBX_LAMPS_BAKED && !defined(DYNAMICLIGHTMAP_ON) && !(defined(UNITY_DOTS_INSTANCING_ENABLED) && !defined(USE_LEGACY_LIGHTMAPS))
half3 RbxSampleLightmap(float2 staticLightmapUV, half3 normalWS)
{
    half4 enc = SAMPLE_TEXTURE2D_LOD(unity_Lightmap, samplerunity_Lightmap, staticLightmapUV, 0);
    half3 e0 = DecodeLightmap(enc, half4(LIGHTMAP_HDR_MULTIPLIER, LIGHTMAP_HDR_EXPONENT, 0.0, 0.0));
    half3 h = (SAMPLE_TEXTURE2D_LOD(unity_LightmapInd, samplerunity_Lightmap, staticLightmapUV, 0).xyz - 0.5) * 8.0;
    UNITY_BRANCH if (_RbxBakeCompare < -0.5) return e0;   // debug: the light for the flat surface only
    return e0 * max(dot(normalWS, h), 0.0);
}
#undef SAMPLE_GI
#define SAMPLE_GI(staticLmName, shName, normalWSName) RbxSampleLightmap(staticLmName, normalWSName)
#endif

// Roblox's ambient + reflection (material shaders): the reflection weight is a split-sum BRDF normalised
// to (F0 A + B) / (A + B), and that part is taken out of the ambient light:
//   ambient x albedo x (1 - metal) x (1 - R)  +  environment(roughness) x R
// URP adds the reflection on top of the full ambient, so floors and walls seen at a shallow angle
// get a milky glaze Roblox doesn't have. A, B: Karis' fit of the split-sum table Roblox samples.
half3 RbxGlobalIllumination(BRDFData brdfData, BRDFData brdfDataClearCoat, float clearCoatMask,
    half3 bakedGI, half occlusion, float3 positionWS, half3 normalWS, half3 viewDirectionWS, float2 normalizedScreenSpaceUV)
{
#if defined(LIGHTMAP_ON) && RBX_LAMPS_BAKED
    // the grid's light is in this object's lightmap (RobloxLampBaker.BakeGrid); moving objects keep the grid.
    // Global _RbxBakeCompare = 1 (editor only): live grid instead of the lightmap, to compare the two
    half3 gridLamps = 0.0;
    UNITY_BRANCH if (_RbxBakeCompare > 0.5) { gridLamps = half3(RbxGridLamps(positionWS, normalWS) * brdfData.diffuse); rbxLampGI = 0.0; }
#else
    half3 gridLamps = half3(RbxGridLamps(positionWS, normalWS) * brdfData.diffuse);
#endif
    if (RbxK() <= 0.0)
        return GlobalIllumination(brdfData, brdfDataClearCoat, clearCoatMask, bakedGI, occlusion, positionWS, normalWS, viewDirectionWS, normalizedScreenSpaceUV) + gridLamps;
    half nv = max(1e-4, dot(normalWS, viewDirectionWS));
    half r = 0.089 + 0.911 * brdfData.perceptualRoughness;
    half4 k = r * half4(-1.0, -0.0275, -0.572, 0.022) + half4(1.0, 0.0425, 1.04, -0.04);
    half a004 = min(k.x * k.x, exp2(-9.28 * nv)) * k.x + k.y;
    half2 ab = half2(-1.04, 1.04) * a004 + k.zw;
    half3 refl = (brdfData.specular * ab.x + ab.y) / max(1e-4, ab.x + ab.y);
    half3 env = GlossyEnvironmentReflection(reflect(-viewDirectionWS, normalWS), positionWS, r, 1.0h, normalizedScreenSpaceUV);
    // brdfData.diffuse = albedo x (1 - metal) x 0.96 (URP's dielectric F0 taken out); Roblox takes out R instead
    half3 diffuse = brdfData.diffuse / 0.96;
    // baked lamps like live grid lamps: lamp light, not ambient, so no occlusion map on it
    return (bakedGI * diffuse * (1.0 - refl) + env * refl) * occlusion + rbxLampGI * brdfData.diffuse + gridLamps;
}

half3 RbxGlobalIllumination(BRDFData brdfData, BRDFData brdfDataClearCoat, float clearCoatMask,
    half3 bakedGI, half occlusion, float3 positionWS, half3 normalWS, half3 viewDirectionWS)
{
    return RbxGlobalIllumination(brdfData, brdfDataClearCoat, clearCoatMask, bakedGI, occlusion, positionWS, normalWS, viewDirectionWS, float2(0.0, 0.0));
}

// everything included after this (LitForwardPass/Lighting.hlsl) gets the Roblox falloff, Ambient, albedo and highlight
#define InitializeStandardLitSurfaceData RbxInitializeStandardLitSurfaceData
#define GetAdditionalLight RbxGetAdditionalLight
#define MixRealtimeAndBakedGI RbxMixRealtimeAndBakedGI
#define DirectBRDFSpecular RbxDirectBRDFSpecular
#define GlobalIllumination RbxGlobalIllumination

#endif
