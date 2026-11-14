// Roblox Bridge lamp bake (editor only): coverage mask from the unwrapped positions (w = 1 where covered).
Shader "Hidden/Roblox Bridge/Lamp Bake Coverage"
{
    // declared (default black) and set by RobloxLampBaker before each Blit: unbound, _MainTex read as white,
    // which marked every texel covered, so nothing was dilated and part edges were averaged with black
    Properties { _MainTex ("Source", 2D) = "black" {} }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _MainTex;
            float4 frag(v2f_img i) : SV_Target { return tex2D(_MainTex, i.uv).w; }
            ENDCG
        }
    }
}
