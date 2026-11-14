// Roblox Bridge lamp grid (editor only): one face of a lamp's static shadow cube. Writes the distance from the
// lamp, keeping the nearest (BlendOp Min, cleared to a large value). Face layout matches RbxGridShadow in
// Shaders/RobloxLighting.hlsl: face = axis * 2 + (negative side), u/v = the other two axes / the major one.
// Driven by Editor/RobloxLampGridBuilder.cs.
Shader "Hidden/Roblox Bridge/Lamp Grid Shadow"
{
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        BlendOp Min
        Blend One One
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            float3 _LampPos;
            float _Face, _Near;
            struct v2f { float4 pos : SV_POSITION; float3 d : TEXCOORD0; float major : TEXCOORD1; };
            v2f vert(float4 vertex : POSITION)
            {
                v2f o;
                float3 d = mul(unity_ObjectToWorld, vertex).xyz - _LampPos;
                int f = (int)_Face;
                int axis = f >> 1;
                float s = (f & 1) ? -1.0 : 1.0;
                float major = s * (axis == 0 ? d.x : axis == 1 ? d.y : d.z);
                float2 uv = axis == 0 ? d.yz : axis == 1 ? d.xz : d.xy;
                o.pos = float4(uv, 0.5 * major, major);
            #if UNITY_UV_STARTS_AT_TOP
                o.pos.y = -o.pos.y;
            #endif
                o.d = d;
                o.major = major;
                return o;
            }
            float4 frag(v2f i) : SV_Target
            {
                if (i.major < _Near) discard;   // like the real-time shadows: nothing nearer than the shadow near plane
                return length(i.d);
            }
            ENDCG
        }
    }
}
