// Roblox Bridge grid bake (editor only): the lamp grid's light (RbxGridLamps in RobloxLighting.hlsl, the same
// function the Roblox Lit shader runs per pixel, static shadow cubes and leak included) for every unwrapped
// lightmap texel, so the baked map looks like the live lamp grid. Driven by Editor/RobloxLampBaker.cs (BakeGrid).
Shader "Hidden/Roblox Bridge/Grid Bake"
{
    SubShader
    {
        Cull Off ZWrite Off ZTest Always

        // 0: grid lamps on every covered texel of _PosTex/_NrmTex (SV_Target1: lamp direction x luminance, for the directional map)
        Pass
        {
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 4.5
            #define RBX_LAMP_GRID 1
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/Shaders/LitInput.hlsl"
            #include "Packages/com.robloxbridge.importer/Shaders/RobloxLighting.hlsl"
            TEXTURE2D(_PosTex);
            TEXTURE2D(_NrmTex);
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
            struct mrt { float4 c : SV_Target0; float4 d : SV_Target1; };
            v2f vert(uint id : SV_VertexID)
            {
                v2f o;
                float2 uv = float2((id << 1) & 2, id & 2);
                o.pos = float4(uv * 2.0 - 1.0, 0.5, 1.0);
                o.uv = uv;
            #if UNITY_UV_STARTS_AT_TOP
                o.uv.y = 1.0 - o.uv.y;
            #endif
                return o;
            }
            mrt frag(v2f i)
            {
                uint w, h;
                _PosTex.GetDimensions(w, h);
                int2 px = int2(min(i.uv * float2(w, h), float2(w - 1, h - 1)));
                float4 p = LOAD_TEXTURE2D(_PosTex, px);
                if (p.w < 0.5) discard;
                float3 n = normalize(LOAD_TEXTURE2D(_NrmTex, px).xyz);
                mrt o;
                float3 vlum;
                o.c = float4(RbxGridLamps(p.xyz, half3(n), vlum), 0.0);
                o.d = float4(vlum, 0.0);   // for the directional map (see RbxSampleLightmap)
                return o;
            }
            ENDHLSL
        }
    }
}
