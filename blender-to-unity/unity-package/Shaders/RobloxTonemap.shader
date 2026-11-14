// Roblox's tonemapper, run by a Full Screen Pass renderer feature before URP's post processing
// (URP's own Tonemapping is set to None). Per channel, like Roblox's ImageProcess shader:
//   display = e * x(x + b) / (x(x + c) + d),  x = scene colour * 2^ExposureCompensation, capped at 4
// Constants measured in Studio (Future, quality 21) with tools/calibrate_lighting.lua.
Shader "Roblox Bridge/Roblox Tonemap"
{
    Properties
    {
        _RbxExposure ("Exposure (linear)", Float) = 1
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" "RenderPipeline" = "UniversalPipeline" }
        ZWrite Off Cull Off ZTest Always
        Pass
        {
            Name "RobloxTonemap"
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.core/Runtime/Utilities/Blit.hlsl"

            float _RbxExposure;

            half4 Frag(Varyings input) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);
                float4 c = SAMPLE_TEXTURE2D_X(_BlitTexture, sampler_LinearClamp, input.texcoord);
                float3 x = clamp(c.rgb * _RbxExposure, 0.0, 4.0);   // Roblox's HDR buffer holds up to 4
                float3 y = saturate(1.049 * x * (x + 0.654) / (x * (x + 0.775) + 0.2035));
                return half4(SRGBToLinear(y), c.a);   // display value; URP's output encodes it back to sRGB
            }
            ENDHLSL
        }
    }
}
